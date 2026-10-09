"""Any OpenAI-compatible /v1/chat/completions endpoint, with pluggable auth.

auth.type:
  none            no Authorization header
  bearer_env      Authorization: Bearer $<auth.env>
  oauth2_kms_jwt  OAuth2 client-credentials, client authenticated with a private_key_jwt
                  (RFC 7523) assertion signed by an AWS KMS ECC_NIST_P256 key. The private
                  key never leaves KMS. Token cached and refreshed; a 401 forces a refresh.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import ssl
import time
import uuid

import httpx

from .base import Backend, HTTPError, Request, Response, data_url


def ssl_context(ca_bundle: str | None) -> ssl.SSLContext:
    try:
        import certifi

        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    for extra in filter(None, [ca_bundle, os.environ.get("OCRBENCH_EXTRA_CA")]):
        if os.path.exists(extra):
            ctx.load_verify_locations(cafile=extra)
    return ctx


def _b64(b: bytes) -> bytes:
    return base64.urlsafe_b64encode(b).rstrip(b"=")


def _der_to_raw(sig: bytes) -> bytes:
    """DER-encoded ECDSA signature (what KMS returns) -> 64-byte r||s (what JWS ES256 wants)."""

    def read_int(buf: bytes, i: int) -> tuple[int, int]:
        if buf[i] != 0x02:
            raise ValueError("bad DER signature")
        n = buf[i + 1]
        return int.from_bytes(buf[i + 2 : i + 2 + n], "big"), i + 2 + n

    i = 2 if sig[1] < 0x80 else 2 + (sig[1] & 0x7F)
    r, i = read_int(sig, i)
    s, _ = read_int(sig, i)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


class _KmsJwtAuth:
    def __init__(self, auth: dict, verify: ssl.SSLContext):
        self.a = auth
        self.verify = verify
        self.token: str | None = None
        self.expires_at = 0.0
        self.lock = asyncio.Lock()

    def _fetch(self) -> tuple[str, float]:
        import boto3

        a, now = self.a, int(time.time())
        cid = a["client_id"]
        header = _b64(json.dumps({"alg": "ES256", "typ": "JWT"}, separators=(",", ":")).encode())
        claims = {
            "iss": cid,
            "sub": cid,
            "aud": a.get("audience") or a["token_url"],
            "iat": now,
            "exp": now + 300,
            "jti": str(uuid.uuid4()),
        }
        payload = _b64(json.dumps(claims, separators=(",", ":")).encode())
        signing_input = header + b"." + payload
        kms = boto3.client("kms", region_name=a.get("kms_region") or os.environ.get("AWS_REGION"))
        der = kms.sign(
            KeyId=a["kms_key_id"], Message=signing_input, MessageType="RAW", SigningAlgorithm="ECDSA_SHA_256"
        )["Signature"]
        assertion = (signing_input + b"." + _b64(_der_to_raw(der))).decode()
        r = httpx.post(
            a["token_url"],
            timeout=30,
            verify=self.verify,
            data={
                "grant_type": "client_credentials",
                "client_id": cid,
                "client_assertion": assertion,
                "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                **({"scope": a["scope"]} if a.get("scope") else {}),
            },
        )
        if r.status_code >= 400:
            raise HTTPError(r.status_code, f"token endpoint: {r.text}")
        body = r.json()
        return body["access_token"], time.time() + float(body.get("expires_in", 3000)) - 60

    async def header(self, force: bool = False) -> dict:
        async with self.lock:
            if force or not self.token or time.time() > self.expires_at:
                self.token, self.expires_at = await asyncio.to_thread(self._fetch)
        return {"Authorization": f"Bearer {self.token}"}


class OpenAICompatBackend(Backend):
    def __init__(self, name: str, spec: dict):
        self.name = name
        self.model = spec["model"]
        self.price_per_mtok = spec.get("price_per_mtok")
        self._init_temperature(spec)
        self.url = spec["base_url"].rstrip("/") + "/v1/chat/completions"
        self.headers = dict(spec.get("headers") or {})
        self.cache_bust = bool(spec.get("cache_bust", False))
        self.extra_body = spec.get("extra_body") or {}
        verify = ssl_context(spec.get("ca_bundle"))
        self.client = httpx.AsyncClient(
            verify=verify, timeout=httpx.Timeout(600.0), limits=httpx.Limits(max_connections=256)
        )
        auth = spec.get("auth") or {"type": "none"}
        self.auth_type = auth.get("type", "none")
        self.kms_auth = _KmsJwtAuth(auth, verify) if self.auth_type == "oauth2_kms_jwt" else None
        self.bearer_env = auth.get("env") if self.auth_type == "bearer_env" else None
        if self.auth_type not in ("none", "bearer_env", "oauth2_kms_jwt"):
            raise ValueError(f"llm_backends.{name}.auth.type {self.auth_type!r} not supported")

    async def _auth(self, force: bool) -> dict:
        if self.kms_auth:
            return await self.kms_auth.header(force)
        if self.bearer_env:
            return {"Authorization": f"Bearer {os.environ[self.bearer_env]}"}
        return {}

    async def warmup(self) -> None:
        await self._auth(False)

    async def complete(self, req: Request) -> Response:
        system = req.system
        if self.cache_bust:  # some gateways return cached completions for identical bodies
            system = f"{system}\n[request {uuid.uuid4().hex[:12]}]"
        content = [{"type": "image_url", "image_url": {"url": data_url(p)}} for p in req.images]
        content.append({"type": "text", "text": req.prompt})
        body = {
            "model": self.model,
            "max_tokens": req.max_tokens,
            **({} if self.temperature(req) is None else {"temperature": self.temperature(req)}),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
            **self.extra_body,
        }
        for attempt in (1, 2):
            headers = {**self.headers, **await self._auth(force=attempt == 2)}
            r = await self.client.post(self.url, json=body, headers=headers)
            if r.status_code == 401 and attempt == 1 and self.kms_auth:
                continue
            break
        if r.status_code >= 400:
            raise HTTPError(r.status_code, r.text)
        data = r.json()
        ch = data["choices"][0]
        u = data.get("usage") or {}
        return Response(
            ch["message"]["content"] or "", u.get("prompt_tokens"), u.get("completion_tokens"), ch.get("finish_reason")
        )
