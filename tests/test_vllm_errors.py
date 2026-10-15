"""Self-hosted vLLM backend: retryable SageMaker errors and no host in the recorded model name."""

from __future__ import annotations

import asyncio
import json

import pytest
from botocore.stub import Stubber

from ocrbench.backends.base import RETRYABLE, HTTPError, Request
from ocrbench.backends.vllm import DirectVllm, SageMakerVllm


@pytest.fixture
def backend(monkeypatch):
    """A SageMakerVllm on a real (offline) sagemaker-runtime client: the Stubber answers every call."""
    import boto3

    for k in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_CONFIG_FILE", "/nonexistent")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/nonexistent")
    be = SageMakerVllm.__new__(SageMakerVllm)
    be.name, be.served, be.endpoint = "ocr", "ocr-model", "bench-ocr"
    be.req_cfg = {"prompt": "transcribe"}
    be.rt = boto3.client("sagemaker-runtime", region_name="eu-west-1")
    with Stubber(be.rt) as stub:
        yield be, stub
        stub.assert_no_pending_responses()


def complete(be):
    return asyncio.run(be.complete(Request("sys", "user", [], max_tokens=16)))


@pytest.mark.parametrize(
    ("code", "http"),
    [
        ("InternalFailure", 500),
        ("InternalDependencyException", 530),
        ("ModelNotReadyException", 429),
        ("ThrottlingException", 400),
        ("ServiceUnavailable", 503),
    ],
)
def test_transient_sagemaker_errors_are_retryable(backend, code, http):
    be, stub = backend
    stub.add_client_error(
        "invoke_endpoint", service_error_code=code, service_message="try later", http_status_code=http
    )
    with pytest.raises(HTTPError) as ei:
        complete(be)
    assert ei.value.status in RETRYABLE


def test_endpoint_timeout_still_not_retryable(backend):
    be, stub = backend
    stub.add_client_error(
        "invoke_endpoint",
        service_error_code="ModelError",
        http_status_code=424,
        modeled_fields={"OriginalStatusCode": 0},
    )
    with pytest.raises(HTTPError) as ei:
        complete(be)
    assert ei.value.status not in RETRYABLE


def test_validation_error_not_mapped(backend):
    be, stub = backend
    stub.add_client_error("invoke_endpoint", service_error_code="ValidationError", http_status_code=400)
    with pytest.raises(Exception) as ei:
        complete(be)
    assert not isinstance(ei.value, HTTPError)


def test_direct_url_stays_out_of_model_name():
    be = DirectVllm(
        None,  # pyright: ignore[reportArgumentType]
        "ocr",
        {"served_model_name": "ocr-model", "request": {"prompt": "p"}, "direct_url": "http://10.1.2.3:8000/"},
    )
    assert be.model == "ocr-model@direct" and "10.1.2.3" not in be.model
    assert be.url == "http://10.1.2.3:8000/v1/chat/completions"


def test_image_first_puts_the_page_before_the_prompt(tmp_path):
    from PIL import Image

    img = tmp_path / "p.png"
    Image.new("RGB", (2, 2), "white").save(img)
    be = SageMakerVllm.__new__(SageMakerVllm)
    be.served, be.req_cfg = "m", {"prompt": "Convert this page to docling."}
    assert [c["type"] for c in be.body(Request("", "", images=[img]))["messages"][0]["content"]] == [
        "text",
        "image_url",
    ]
    be.req_cfg = {**be.req_cfg, "image_first": True}
    assert [c["type"] for c in be.body(Request("", "", images=[img]))["messages"][0]["content"]] == [
        "image_url",
        "text",
    ]


def _sse(*events: dict) -> bytes:
    return b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events) + b"data: [DONE]\n\n"


def test_parse_stream_joins_deltas_across_split_chunks():
    raw = _sse(
        {"choices": [{"delta": {"role": "assistant"}}]},
        {"choices": [{"delta": {"content": "Kontout"}}]},
        {"choices": [{"delta": {"content": "skrift"}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 2307, "completion_tokens": 1355}},
    )
    chunks = [raw[i : i + 7] for i in range(0, len(raw), 7)]  # events cut at arbitrary bytes
    r = SageMakerVllm.parse_stream(chunks)
    assert (r.text, r.prompt_tokens, r.completion_tokens, r.finish_reason) == ("Kontoutskrift", 2307, 1355, "stop")


def test_parse_stream_raises_on_an_error_event():
    with pytest.raises(HTTPError) as e:
        SageMakerVllm.parse_stream([_sse({"error": {"code": 400, "message": "too many tokens"}})])
    assert e.value.status == 400


class _FakeRt:
    """invoke_endpoint_with_response_stream only; records the request body."""

    class exceptions:  # noqa: N801 - mirrors the boto3 client attribute
        class ModelError(Exception):
            pass

    def __init__(self, events=None, error=None):
        self.events, self.error, self.body = events or [], error, None

    def invoke_endpoint_with_response_stream(self, **kw):
        self.body = json.loads(kw["Body"])
        if self.error:
            raise self.error
        return {"Body": iter(self.events)}


def _streaming(rt) -> SageMakerVllm:
    be = SageMakerVllm.__new__(SageMakerVllm)
    be.served, be.endpoint, be.stream, be.rt = "m", "bench-m", True, rt
    be.req_cfg = {"prompt": "Read text in the image.", "max_tokens": 8192}
    return be


def test_streaming_request_and_response():
    raw = _sse({"choices": [{"delta": {"content": "Hei"}, "finish_reason": "length"}]})
    rt = _FakeRt(events=[{"PayloadPart": {"Bytes": raw[:9]}}, {"PayloadPart": {"Bytes": raw[9:]}}])
    r = asyncio.run(_streaming(rt).complete(Request("", "")))
    assert r.text == "Hei" and r.finish_reason == "length"
    assert rt.body["stream"] is True and rt.body["stream_options"] == {"include_usage": True}


def test_streaming_time_limit_is_not_retried():
    from botocore.exceptions import EventStreamError

    err = EventStreamError({"Error": {"Code": "ModelStreamError", "Message": "did not complete in time"}}, "Invoke")
    with pytest.raises(HTTPError) as e:
        asyncio.run(_streaming(_FakeRt(error=err)).complete(Request("", "")))
    assert e.value.status == 0 and e.value.status not in RETRYABLE
    err = EventStreamError({"Error": {"Code": "InternalStreamFailure", "Message": "x"}}, "Invoke")
    with pytest.raises(HTTPError) as e:
        asyncio.run(_streaming(_FakeRt(error=err)).complete(Request("", "")))
    assert e.value.status in RETRYABLE
