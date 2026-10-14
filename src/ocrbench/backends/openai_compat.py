"""Any OpenAI-compatible /v1/chat/completions endpoint, with pluggable auth.

auth.type:
  none            no Authorization header
  bearer_env      Authorization: Bearer $<auth.env>
  oauth2_kms_jwt  OAuth2 client-credentials, client authenticated with a private_key_jwt
                  (RFC 7523) assertion signed by an AWS KMS ECC_NIST_P256 key. The private
                  key never leaves KMS. Token cached and refreshed; a 401 refreshes it (once,
                  however many requests got the 401 for that token).
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


def ssl_context(ca_bundle: str | None, name: str = "") -> ssl.SSLContext:
    if ca_bundle and not os.path.exists(ca_bundle):
        raise ValueError(f"backends.{name}.ca_bundle: {ca_bundle!r} does not exist on this machine")
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
        return body["access_token"], token_expiry(now, float(body.get("expires_in", 3000)))

    async def header(self, force: bool = False, stale: dict | None = None) -> dict:
        """`stale`: the header a request got a 401 with. Only the first such request refreshes the
        token; the others see it was already replaced and reuse the new one."""
        async with self.lock:
            rejected = stale is not None and stale.get("Authorization") == f"Bearer {self.token}"
            if force or rejected or not self.token or time.time() > self.expires_at:
                self.token, self.expires_at = await asyncio.to_thread(self._fetch)
            return {"Authorization": f"Bearer {self.token}"}


def token_expiry(issued: float, expires_in: float) -> float:
    """When to stop using a token: a margin of 10% of its lifetime (1-60 s) before it expires, so a
    short-lived token is still reused instead of refetched for every request."""
    return issued + max(0.0, expires_in - min(60.0, max(1.0, expires_in * 0.1)))


def message_text(content) -> str:
    """A message's text. Some gateways return a list of content parts: join the text ones and drop the
    rest (reasoning/thinking, images)."""
    if isinstance(content, list):
        texts = (p.get("text") for p in content if isinstance(p, dict) and p.get("type", "text") in _TEXT_PARTS)
        return "".join(t for t in texts if isinstance(t, str))
    return content or ""


_TEXT_PARTS = ("text", "output_text")


class OpenAICompatBackend(Backend):
    def __init__(self, name: str, spec: dict):
        self.name = name
        self.model = spec["model"]
        self._init_temperature(spec)
        self.url = spec["base_url"].rstrip("/") + "/v1/chat/completions"
        self.headers = dict(spec.get("headers") or {})
        self.cache_bust = bool(spec.get("cache_bust", False))
        self.extra_body = dict(spec.get("extra_body") or {})
        if spec.get("reasoning_effort"):  # low | medium | high (gateway/provider maps it to a thinking budget)
            self.extra_body["reasoning_effort"] = spec["reasoning_effort"]
        verify = ssl_context(spec.get("ca_bundle"), name)
        self.client = httpx.AsyncClient(
            verify=verify, timeout=httpx.Timeout(600.0), limits=httpx.Limits(max_connections=256)
        )
        auth = spec.get("auth") or {"type": "none"}
        self.auth_type = auth.get("type", "none")
        self.kms_auth = _KmsJwtAuth(auth, verify) if self.auth_type == "oauth2_kms_jwt" else None
        self.bearer_env = auth.get("env") if self.auth_type == "bearer_env" else None
        if self.auth_type not in ("none", "bearer_env", "oauth2_kms_jwt"):
            raise ValueError(f"backends.{name}.auth.type {self.auth_type!r} not supported")

    async def _auth(self, force: bool = False, stale: dict | None = None) -> dict:
        if self.kms_auth:
            return await self.kms_auth.header(force, stale)
        if self.bearer_env:
            return {"Authorization": f"Bearer {os.environ[self.bearer_env]}"}
        return {}

    async def warmup(self) -> None:
        await self._auth(False)

    async def _post(self, body: dict, auth: dict) -> httpx.Response:
        try:
            return await self.client.post(self.url, json=body, headers={**self.headers, **auth})
        except httpx.TransportError as e:  # connect/read timeouts, resets: retryable
            raise HTTPError(503, f"{type(e).__name__}: {e}") from e

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
        auth = await self._auth()
        r = await self._post(body, auth)
        if r.status_code == 401 and self.kms_auth:  # token revoked or expired early: fetch a new one once
            r = await self._post(body, await self._auth(stale=auth))
        if r.status_code >= 400:
            raise HTTPError(r.status_code, r.text)
        data = r.json()
        ch = data["choices"][0]
        u = data.get("usage") or {}
        return Response(
            message_text(ch["message"].get("content")),
            u.get("prompt_tokens"),
            u.get("completion_tokens"),
            ch.get("finish_reason"),
        )
