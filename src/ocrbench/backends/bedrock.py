"""Claude (or any model) via AWS Bedrock Converse."""

from __future__ import annotations

import asyncio

from botocore.exceptions import ConnectionClosedError, EndpointConnectionError, ReadTimeoutError

from .base import Backend, HTTPError, Request, Response, mime


class BedrockBackend(Backend):
    def __init__(self, name: str, spec: dict):
        import boto3
        from botocore.config import Config as BotoConfig

        self.name = name
        self.model = spec["model_id"]
        self._init_temperature(spec)
        self.client = boto3.client(
            "bedrock-runtime",
            region_name=spec.get("region"),
            config=BotoConfig(
                max_pool_connections=128, read_timeout=600, retries={"total_max_attempts": 1, "mode": "standard"}
            ),
        )

    async def complete(self, req: Request) -> Response:
        content: list[dict] = [
            {"image": {"format": mime(p).split("/")[1], "source": {"bytes": p.read_bytes()}}} for p in req.images
        ]
        content.append({"text": req.prompt})

        def call():
            try:
                return self.client.converse(
                    modelId=self.model,
                    system=[{"text": req.system}] if req.system else [],
                    messages=[{"role": "user", "content": content}],
                    inferenceConfig={
                        "maxTokens": req.max_tokens,
                        **({} if self.temperature(req) is None else {"temperature": self.temperature(req)}),
                    },
                )
            except self.client.exceptions.ThrottlingException as e:
                raise HTTPError(429, str(e)) from e
            except (
                self.client.exceptions.ServiceUnavailableException,
                self.client.exceptions.ModelNotReadyException,
                self.client.exceptions.InternalServerException,
                self.client.exceptions.ModelStreamErrorException,
            ) as e:
                raise HTTPError(503, str(e)) from e
            except self.client.exceptions.ModelTimeoutException as e:
                raise HTTPError(504, str(e)) from e
            except (EndpointConnectionError, ReadTimeoutError, ConnectionClosedError) as e:
                raise HTTPError(503, f"{type(e).__name__}: {e}") from e

        r = await asyncio.to_thread(call)
        text = "".join(b.get("text", "") for b in r["output"]["message"]["content"])
        u = r.get("usage", {})
        return Response(text, u.get("inputTokens"), u.get("outputTokens"), r.get("stopReason"))
