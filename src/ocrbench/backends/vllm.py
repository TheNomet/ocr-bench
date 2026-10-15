"""Self-hosted models (`kind: selfhosted`): vLLM's OpenAI API, behind SageMaker or at a direct URL.

The request body comes from `backends.<name>.request` (prompt, sampling, extra vLLM fields), so
another vLLM-served model only needs a config entry. Each one gets its own SageMaker model and
endpoint (<prefix>-<name>).
"""

from __future__ import annotations

import asyncio
import json

import httpx
from botocore.exceptions import ClientError, ConnectionClosedError, EndpointConnectionError, ReadTimeoutError

from ..config import Config
from .base import Backend, HTTPError, Request, Response, data_url

# sagemaker-runtime error codes worth retrying, as the status the runners retry on (base.RETRYABLE).
# ModelError is handled separately: its status 0 is the 60 s endpoint timeout, which is not retried.
_RETRY_CODES = {
    "ThrottlingException": 429,
    "Throttling": 429,
    "ModelNotReadyException": 503,
    "ServiceUnavailable": 429,
    "ServiceUnavailableException": 429,
    "InternalFailure": 500,
    "InternalServerError": 500,
    "InternalDependencyException": 503,
    "InternalStreamFailure": 500,
}


def _retry_status(e: Exception) -> int | None:
    if isinstance(e, ClientError) and (code := e.response.get("Error", {}).get("Code")) in _RETRY_CODES:
        return _RETRY_CODES[code]
    msg = str(e)
    return next((s for c, s in _RETRY_CODES.items() if c in msg), 429 if "Throttl" in msg else None)


class _Vllm(Backend):
    def __init__(self, cfg: Config, name: str, spec: dict):
        self.name = name
        self.served = spec["served_model_name"]
        self.req_cfg = spec["request"]

    def body(self, req: Request) -> dict:
        rc = self.req_cfg
        content = [{"type": "text", "text": rc["prompt"]}]
        images = [{"type": "image_url", "image_url": {"url": data_url(p)}} for p in req.images]
        content = images + content if rc.get("image_first") else content + images
        return {
            "model": self.served,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": rc.get("max_tokens", req.max_tokens),
            "temperature": rc.get("temperature", 0.0),
            **(rc.get("extra_body") or {}),
        }

    @staticmethod
    def parse(data: dict) -> Response:
        ch = data["choices"][0]
        u = data.get("usage") or {}
        return Response(
            ch["message"]["content"] or "", u.get("prompt_tokens"), u.get("completion_tokens"), ch.get("finish_reason")
        )

    @staticmethod
    def parse_stream(chunks) -> Response:
        """vLLM's server-sent events (`"stream": true`), in byte chunks that may split an event anywhere."""
        text, finish, usage, buf = [], None, {}, b""

        def event(ev: bytes) -> None:
            nonlocal finish, usage
            for line in ev.splitlines():
                data = line[5:].strip() if line.startswith(b"data:") else b""
                if not data or data == b"[DONE]":
                    continue
                d = json.loads(data)
                if err := d.get("error"):
                    raise HTTPError(int(err.get("code") or 500), str(err.get("message") or err))
                usage = d.get("usage") or usage
                for ch in d.get("choices") or []:
                    text.append((ch.get("delta") or {}).get("content") or "")
                    finish = ch.get("finish_reason") or finish

        for c in chunks:
            buf += c
            while b"\n\n" in buf:
                ev, buf = buf.split(b"\n\n", 1)
                event(ev)
        event(buf)
        return Response("".join(text), usage.get("prompt_tokens"), usage.get("completion_tokens"), finish)


class DirectVllm(_Vllm):
    def __init__(self, cfg: Config, name: str, spec: dict):
        super().__init__(cfg, name, spec)
        base = spec["direct_url"].rstrip("/")
        self.url = base + "/v1/chat/completions"
        self.health = base + "/health"
        self.model = f"{self.served}@direct"  # the URL (host/IP) stays out of records
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(900.0), limits=httpx.Limits(max_connections=256))

    async def warmup(self) -> None:
        (await self.client.get(self.health)).raise_for_status()

    async def complete(self, req: Request) -> Response:
        try:
            r = await self.client.post(self.url, json=self.body(req))
        except httpx.TransportError as e:
            raise HTTPError(503, f"{type(e).__name__}: {e}") from e
        if r.status_code >= 400:
            raise HTTPError(r.status_code, r.text)
        return self.parse(r.json())


class SageMakerVllm(_Vllm):
    """vLLM's /invocations accepts chat-completion bodies. Real-time endpoints cap a request at 60 s; with
    `sagemaker.response_stream: true` the answer is streamed instead, which SageMaker allows up to 8 min."""

    stream = False

    def __init__(self, cfg: Config, name: str, spec: dict):
        import boto3
        from botocore.config import Config as BotoConfig

        super().__init__(cfg, name, spec)
        self.boto3, self.region, self.instance_type = boto3, cfg.region, None
        self.endpoint = cfg.endpoint_name(name)
        self.model = f"{self.served}@sagemaker"  # endpoint name stays out of records
        self.stream = bool((spec.get("sagemaker") or {}).get("response_stream"))
        self.rt = boto3.client(
            "sagemaker-runtime",
            region_name=cfg.region,
            config=BotoConfig(
                max_pool_connections=256, read_timeout=120, retries={"total_max_attempts": 1, "mode": "standard"}
            ),
        )

    async def warmup(self) -> None:
        sm = self.boto3.client("sagemaker", region_name=self.region)
        v = (await asyncio.to_thread(sm.describe_endpoint, EndpointName=self.endpoint))["ProductionVariants"][0]
        live = [p["InstanceType"] for p in v.get("InstancePools") or [] if p.get("CurrentInstanceCount")]
        self.instance_type = (live or [v.get("CurrentInstanceType")])[0]

    async def complete(self, req: Request) -> Response:
        payload = self.body(req)
        if self.stream:
            payload |= {"stream": True, "stream_options": {"include_usage": True}}
        body = json.dumps(payload)

        def chunks(r):
            for ev in r["Body"]:  # botocore raises EventStreamError for ModelStreamError / InternalStreamFailure
                if "PayloadPart" in ev:
                    yield ev["PayloadPart"]["Bytes"]

        def call():
            try:
                if self.stream:
                    r = self.rt.invoke_endpoint_with_response_stream(
                        EndpointName=self.endpoint,
                        ContentType="application/json",
                        Accept="text/event-stream",
                        Body=body,
                    )
                    return self.parse_stream(chunks(r))
                r = self.rt.invoke_endpoint(
                    EndpointName=self.endpoint, ContentType="application/json", Accept="application/json", Body=body
                )
            except self.rt.exceptions.ModelError as e:  # status 0 = the 60 s endpoint timeout: not retried
                raise HTTPError(int(e.response.get("OriginalStatusCode", 500)), str(e)) from e
            except (EndpointConnectionError, ReadTimeoutError, ConnectionClosedError) as e:
                raise HTTPError(503, f"{type(e).__name__}: {e}") from e
            except HTTPError:
                raise
            except Exception as e:
                if (status := _retry_status(e)) is not None:
                    raise HTTPError(status, str(e)) from e
                if isinstance(e, ClientError) and e.response.get("Error", {}).get("Code") == "ModelStreamError":
                    raise HTTPError(0, str(e)) from e  # the 8 min streaming limit: not retried either
                raise
            return self.parse(json.loads(r["Body"].read()))

        return await asyncio.to_thread(call)


def make(cfg: Config, name: str, spec: dict) -> Backend:
    return DirectVllm(cfg, name, spec) if spec.get("direct_url") else SageMakerVllm(cfg, name, spec)
