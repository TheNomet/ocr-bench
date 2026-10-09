from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Request:
    system: str
    prompt: str
    images: list[Path] = field(default_factory=list)  # page images, sent before the prompt
    max_tokens: int = 8192
    temperature: float = 0.0


@dataclass
class Response:
    text: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    finish_reason: str | None = None


class HTTPError(Exception):
    """Provider error with an HTTP-like status, so runners can decide whether to retry."""

    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message[:300]}")
        self.status = status


RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504}


def mime(path: Path) -> str:
    return "image/png" if path.suffix.lower() == ".png" else "image/jpeg"


def data_url(path: Path) -> str:
    return f"data:{mime(path)};base64,{base64.b64encode(path.read_bytes()).decode()}"


_UNSET = object()


class Backend:
    name: str
    model: str
    price_per_mtok: dict | None = None
    # Config `temperature:` overrides the request's value (some models only accept 1); null = don't send it.
    temperature_override: object = _UNSET

    def temperature(self, req: Request) -> float | None:
        return req.temperature if self.temperature_override is _UNSET else self.temperature_override  # type: ignore[return-value]

    def _init_temperature(self, spec: dict) -> None:
        if "temperature" in spec:
            self.temperature_override = spec["temperature"]

    async def warmup(self) -> None:  # noqa: B027 — optional hook
        pass

    async def complete(self, req: Request) -> Response:
        raise NotImplementedError

    def cost_usd(self, prompt_tokens: float, completion_tokens: float) -> float | None:
        p = self.price_per_mtok
        if not p:
            return None
        return (prompt_tokens * p.get("input", 0) + completion_tokens * p.get("output", 0)) / 1e6
