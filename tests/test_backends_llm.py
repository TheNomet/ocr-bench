"""Hosted LLM backends, offline: Bedrock stop reasons, OpenAI-compatible responses, token refresh, TLS config."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from ocrbench import prompts
from ocrbench.backends import bedrock, openai_compat
from ocrbench.backends.base import Request

REQ = Request("sys", "user", [], max_tokens=16)


# ---------------------------------------------------------------------------- Bedrock
@pytest.mark.parametrize(
    ("raw", "norm"),
    [("max_tokens", "length"), ("model_context_window_exceeded", "length"), ("end_turn", "end_turn"), (None, None)],
)
def test_bedrock_stop_reason_normalised(raw, norm):
    assert bedrock.finish_reason(raw) == norm


def test_bedrock_truncated_answer_reports_length(monkeypatch):
    b = bedrock.BedrockBackend("b", {"model_id": "m", "region": "eu-north-1"})
    out = {"output": {"message": {"content": [{"text": "cut"}]}}, "usage": {"inputTokens": 3, "outputTokens": 16}}
    monkeypatch.setattr(b.client, "converse", lambda **_kw: {**out, "stopReason": "max_tokens"})
    r = asyncio.run(b.complete(REQ))
    assert (r.text, r.completion_tokens, r.finish_reason) == ("cut", 16, "length")


# ---------------------------------------------------------------------------- OpenAI-compatible
def _backend(handler, **spec) -> openai_compat.OpenAICompatBackend:
    be = openai_compat.OpenAICompatBackend("gw", {"model": "m", "base_url": "https://gw.example", **spec})
    be.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return be


def _reply(content, finish="stop"):
    body = {"choices": [{"message": {"content": content}, "finish_reason": finish}], "usage": {"prompt_tokens": 2}}
    return httpx.Response(200, json=body)


def test_content_parts_are_joined_to_text():
    parts = [
        {"type": "reasoning", "text": "thinking..."},
        {"type": "text", "text": "Hei "},
        {"type": "image_url", "image_url": {"url": "x"}},
        {"type": "text", "text": "verden"},
    ]
    r = asyncio.run(_backend(lambda req: _reply(parts)).complete(REQ))
    assert r.text == "Hei verden" and r.finish_reason == "stop"
    assert asyncio.run(_backend(lambda req: _reply(None)).complete(REQ)).text == ""
    assert asyncio.run(_backend(lambda req: _reply("plain")).complete(REQ)).text == "plain"


def test_concurrent_401s_refresh_the_token_once(monkeypatch):
    fetched = []

    def fetch(self):
        fetched.append(1)
        return f"tok{len(fetched)}", 1e12

    monkeypatch.setattr(openai_compat._KmsJwtAuth, "_fetch", fetch)

    async def go():
        seen = []

        async def handler(req: httpx.Request):
            seen.append(req.headers["authorization"])
            await asyncio.sleep(0.01)  # all 16 are in flight with the first token before any 401 comes back
            return httpx.Response(401) if req.headers["authorization"] == "Bearer tok1" else _reply("ok")

        be = _backend(handler, auth={"type": "oauth2_kms_jwt", "token_url": "https://idp.example/t"})
        await be.warmup()
        rs = await asyncio.gather(*(be.complete(REQ) for _ in range(16)))
        return rs, seen

    rs, seen = asyncio.run(go())
    assert all(r.text == "ok" for r in rs)
    assert len(fetched) == 2  # the first token, then one refresh for all 16 rejected requests
    assert seen.count("Bearer tok1") == 16 and seen.count("Bearer tok2") == 16


@pytest.mark.parametrize(("expires_in", "valid_for"), [(3000, 2940), (300, 270), (30, 27), (5, 4), (0.5, 0)])
def test_token_expiry_margin_is_proportional(expires_in, valid_for):
    assert openai_compat.token_expiry(1000.0, expires_in) == pytest.approx(1000.0 + valid_for)


def test_missing_ca_bundle_is_a_config_error(tmp_path):
    with pytest.raises(ValueError, match=r"backends\.gw\.ca_bundle.*does not exist"):
        openai_compat.OpenAICompatBackend(
            "gw", {"model": "m", "base_url": "https://gw.example", "ca_bundle": str(tmp_path / "nope.pem")}
        )


def test_bearer_env_header(monkeypatch):
    monkeypatch.setenv("GW_TOKEN", "secret")
    seen = []

    def handler(req):
        seen.append(req.headers["authorization"])
        return _reply(json.dumps({"a": 1}))

    asyncio.run(_backend(handler, auth={"type": "bearer_env", "env": "GW_TOKEN"}).complete(REQ))
    assert seen == ["Bearer secret"]


# ---------------------------------------------------------------------------- prompts
def test_empty_prompt_override_is_no_override():
    p = prompts.resolve({"prompts": {"transcribe": None, "extract": {"max_tokens": 9}}})
    assert p["transcribe"] == prompts.DEFAULTS["transcribe"] and p["extract"]["max_tokens"] == 9
    with pytest.raises(ValueError, match="unknown stage"):
        prompts.resolve({"prompts": {"nope": None}})
