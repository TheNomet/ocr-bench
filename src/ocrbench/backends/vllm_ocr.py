"""The self-hosted OCR model: vLLM's OpenAI API, either behind SageMaker or at a direct URL.

The request body comes from `ocr.request` in the config (prompt, sampling, extra vLLM
fields), so a different vLLM-served OCR model only needs a config change.
"""

from __future__ import annotations

import asyncio
import json
import re

import httpx

from ..config import Config
from .base import Backend, HTTPError, Request, Response, data_url

_REF = re.compile(r"<\|ref\|>(.*?)<\|/ref\|>", re.S)
_DET = re.compile(r"<\|det\|>.*?<\|/det\|>", re.S)
_SPECIAL = re.compile(r"<\|[^|]*\|>|<｜[^｜]*｜>")


def strip_grounding(text: str) -> str:
    """DeepSeek-OCR-family output: unwrap <|ref|>text<|/ref|>, drop <|det|>[boxes]<|/det|>."""
    text = _REF.sub(lambda m: m.group(1), text or "")
    return _SPECIAL.sub("", _DET.sub("", text))


class _VllmOcr(Backend):
    def __init__(self, cfg: Config):
        o = cfg["ocr"]
        self.name = "ocr"
        self.served = o["served_model_name"]
        self.req_cfg = o["request"]
        self.strip = bool(o.get("strip_grounding_tokens", True))
        self.price_per_mtok = None

    def body(self, req: Request) -> dict:
        rc = self.req_cfg
        content = [{"type": "text", "text": rc["prompt"]}]
        content += [{"type": "image_url", "image_url": {"url": data_url(p)}} for p in req.images]
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


class DirectOcr(_VllmOcr):
    def __init__(self, cfg: Config):
        super().__init__(cfg)
        base = cfg["ocr"]["direct_url"].rstrip("/")
        self.url = base + "/v1/chat/completions"
        self.health = base + "/health"
        self.model = f"{self.served}@{base}"
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(900.0), limits=httpx.Limits(max_connections=256))

    async def warmup(self) -> None:
        (await self.client.get(self.health)).raise_for_status()

    async def complete(self, req: Request) -> Response:
        r = await self.client.post(self.url, json=self.body(req))
        if r.status_code >= 400:
            raise HTTPError(r.status_code, r.text)
        return self.parse(r.json())


class SageMakerOcr(_VllmOcr):
    """vLLM's /invocations accepts chat-completion bodies. Real-time endpoints cap a request at 60 s."""

    def __init__(self, cfg: Config):
        import boto3
        from botocore.config import Config as BotoConfig

        super().__init__(cfg)
        self.boto3, self.region, self.instance_type = boto3, cfg.region, None
        self.endpoint = cfg.endpoint_name
        self.model = f"{self.served}@sagemaker:{self.endpoint}"
        self.rt = boto3.client(
            "sagemaker-runtime",
            region_name=cfg.region,
            config=BotoConfig(max_pool_connections=256, read_timeout=120, retries={"max_attempts": 1}),
        )

    async def warmup(self) -> None:
        sm = self.boto3.client("sagemaker", region_name=self.region)
        v = (await asyncio.to_thread(sm.describe_endpoint, EndpointName=self.endpoint))["ProductionVariants"][0]
        live = [p["InstanceType"] for p in v.get("InstancePools") or [] if p.get("CurrentInstanceCount")]
        self.instance_type = (live or [v.get("CurrentInstanceType")])[0]

    async def complete(self, req: Request) -> Response:
        body = json.dumps(self.body(req))

        def call():
            try:
                r = self.rt.invoke_endpoint(
                    EndpointName=self.endpoint, ContentType="application/json", Accept="application/json", Body=body
                )
            except self.rt.exceptions.ModelError as e:
                raise HTTPError(int(e.response.get("OriginalStatusCode", 500)), str(e)) from e
            except Exception as e:
                msg = str(e)
                if "Throttl" in msg or "ServiceUnavailable" in msg:
                    raise HTTPError(429, msg) from e
                raise
            return json.loads(r["Body"].read())

        return self.parse(await asyncio.to_thread(call))


def make_ocr_backend(cfg: Config) -> Backend:
    return DirectOcr(cfg) if cfg["ocr"].get("direct_url") else SageMakerOcr(cfg)
