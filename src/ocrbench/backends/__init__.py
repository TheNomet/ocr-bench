"""Backends: anything that turns (image or text) + prompt into text.

    get_backend(cfg, "<name>")  -> backends[<name>], by kind:
        selfhosted         vLLM on a SageMaker endpoint (or backends.<name>.direct_url)
        bedrock            AWS Bedrock Converse
        openai_compatible  any /v1/chat/completions endpoint

Every backend implements `async complete(Request) -> Response`. Runners never know which
provider they are talking to.
"""

from __future__ import annotations

from ..config import Config
from .base import Backend, HTTPError, Request, Response


def get_backend(cfg: Config, name: str) -> Backend:
    spec = cfg.backend(name)
    kind = spec["kind"]
    if kind == "selfhosted":
        from .vllm import make

        return make(cfg, name, spec)
    if kind == "bedrock":
        from .bedrock import BedrockBackend

        return BedrockBackend(name, spec)
    if kind == "openai_compatible":
        from .openai_compat import OpenAICompatBackend

        return OpenAICompatBackend(name, spec)
    raise ValueError(f"backends.{name}: unknown kind {kind!r}")


__all__ = ["Backend", "HTTPError", "Request", "Response", "get_backend"]
