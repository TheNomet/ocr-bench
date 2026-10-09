"""Backends: anything that turns (image or text) + prompt into text.

    get_backend(cfg, "ocr")             -> self-hosted model (SageMaker, or ocr.direct_url)
    get_backend(cfg, "<llm key>")       -> llm_backends[<key>] (bedrock | openai_compatible)

Every backend implements `async complete(Request) -> Response`. Runners never know which
provider they are talking to.
"""

from __future__ import annotations

from ..config import Config
from .base import Backend, HTTPError, Request, Response


def get_backend(cfg: Config, name: str) -> Backend:
    if name == "ocr":
        from .vllm_ocr import make_ocr_backend

        return make_ocr_backend(cfg)
    spec = cfg.llm(name)
    provider = spec.get("provider")
    if provider == "bedrock":
        from .bedrock import BedrockBackend

        return BedrockBackend(name, spec)
    if provider == "openai_compatible":
        from .openai_compat import OpenAICompatBackend

        return OpenAICompatBackend(name, spec)
    raise ValueError(f"llm_backends.{name}: unknown provider {provider!r} (bedrock | openai_compatible)")


__all__ = ["Backend", "HTTPError", "Request", "Response", "get_backend"]
