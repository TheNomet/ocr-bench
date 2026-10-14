"""Retry policy: transcribe.call_with_retries and the SageMaker vLLM error mapping that feeds it."""

from __future__ import annotations

import asyncio
import io
import json

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from botocore.stub import Stubber

from ocrbench import transcribe
from ocrbench.backends.base import RETRYABLE, HTTPError, Request
from ocrbench.backends.vllm import SageMakerVllm


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    """asyncio.sleep -> no-op that records the requested backoff."""
    calls: list[float] = []

    async def fake_sleep(s: float, *_a, **_k) -> None:
        calls.append(s)

    monkeypatch.setattr(transcribe.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(transcribe.random, "uniform", lambda lo, hi: hi)  # jitter pinned to the full delay
    return calls


def flaky(*errors: Exception, result="ok"):
    """Async callable raising `errors` in order, then returning `result`; .calls counts attempts."""
    pending = list(errors)

    async def fn():
        fn.calls += 1
        if pending:
            raise pending.pop(0)
        return result

    fn.calls = 0
    return fn


def run(fn, max_retries: int):
    return asyncio.run(transcribe.call_with_retries(fn, max_retries))


def test_success_first_try(sleeps):
    fn = flaky()
    result, retries, seconds = run(fn, 3)
    assert (result, retries, fn.calls, sleeps) == ("ok", 0, 1, [])
    assert seconds >= 0


@pytest.mark.parametrize("status", sorted(RETRYABLE))
def test_retries_retryable_then_succeeds(sleeps, status):
    fn = flaky(HTTPError(status, "busy"), HTTPError(status, "busy"))
    result, retries, _ = run(fn, 3)
    assert (result, retries, fn.calls) == ("ok", 2, 3)
    assert sleeps == [2.0, 4.0]


def test_stops_at_max_retries(sleeps):
    fn = flaky(*[HTTPError(429, "slow down") for _ in range(10)])
    with pytest.raises(HTTPError) as ei:
        run(fn, 3)
    assert ei.value.status == 429
    assert fn.calls == 4  # first attempt + 3 retries
    assert len(sleeps) == 3


def test_backoff_is_capped(sleeps):
    fn = flaky(*[HTTPError(503, "down") for _ in range(8)])
    run(fn, 8)
    assert sleeps == [2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0, 30.0]


def test_zero_retries(sleeps):
    fn = flaky(HTTPError(503, "down"))
    with pytest.raises(HTTPError):
        run(fn, 0)
    assert (fn.calls, sleeps) == (1, [])


@pytest.mark.parametrize("status", [0, 400, 401, 403, 404, 413, 422])
def test_non_retryable_status_not_retried(sleeps, status):
    fn = flaky(HTTPError(status, "nope"))
    with pytest.raises(HTTPError) as ei:
        run(fn, 5)
    assert ei.value.status == status
    assert (fn.calls, sleeps) == (1, [])


def test_other_exceptions_not_retried(sleeps):
    fn = flaky(ValueError("bad json"))
    with pytest.raises(ValueError):
        run(fn, 5)
    assert (fn.calls, sleeps) == (1, [])


# ---------------------------------------------------------------------------- SageMaker error mapping
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


REQ = Request("sys", "user", [], max_tokens=16)


def complete(be):
    return asyncio.run(be.complete(REQ))


def test_success_parses_body(backend):
    be, stub = backend
    body = {"choices": [{"message": {"content": "hei"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 3}}
    stub.add_response(
        "invoke_endpoint",
        {"Body": io.BytesIO(json.dumps(body).encode())},  # pyright: ignore[reportArgumentType]
        {
            "EndpointName": "bench-ocr",
            "ContentType": "application/json",
            "Accept": "application/json",
            "Body": json.dumps(be.body(REQ)),
        },
    )
    r = complete(be)
    assert (r.text, r.prompt_tokens, r.finish_reason) == ("hei", 3, "stop")


def test_model_error_status_zero_is_not_retryable(backend):
    """ModelError with OriginalStatusCode 0 is SageMaker's 60 s endpoint timeout: retrying won't help."""
    be, stub = backend
    stub.add_client_error(
        "invoke_endpoint",
        service_error_code="ModelError",
        service_message="Received server error (0) from primary",
        http_status_code=424,
        modeled_fields={"OriginalStatusCode": 0},
    )
    with pytest.raises(HTTPError) as ei:
        complete(be)
    assert ei.value.status == 0 and 0 not in RETRYABLE


def test_model_error_keeps_original_status(backend):
    be, stub = backend
    stub.add_client_error(
        "invoke_endpoint",
        service_error_code="ModelError",
        http_status_code=424,
        modeled_fields={"OriginalStatusCode": 503},
    )
    with pytest.raises(HTTPError) as ei:
        complete(be)
    assert ei.value.status == 503 and 503 in RETRYABLE


def test_model_error_without_status_is_500(backend):
    be, stub = backend
    stub.add_client_error("invoke_endpoint", service_error_code="ModelError", http_status_code=424)
    with pytest.raises(HTTPError) as ei:
        complete(be)
    assert ei.value.status == 500


@pytest.mark.parametrize(
    ("code", "message"),
    [("ThrottlingException", "Rate exceeded"), ("ServiceUnavailable", "Service is unavailable")],
)
def test_throttling_maps_to_429(backend, code, message):
    be, stub = backend
    stub.add_client_error("invoke_endpoint", service_error_code=code, service_message=message, http_status_code=400)
    with pytest.raises(HTTPError) as ei:
        complete(be)
    assert ei.value.status == 429 and 429 in RETRYABLE


def test_other_client_errors_pass_through(backend):
    be, stub = backend
    stub.add_client_error(
        "invoke_endpoint",
        service_error_code="ValidationError",
        service_message="no such endpoint",
        http_status_code=400,
    )
    with pytest.raises(ClientError) as ei:
        complete(be)
    assert not isinstance(ei.value, HTTPError)
    assert ei.value.response["Error"]["Code"] == "ValidationError"


def test_connection_errors_map_to_503(monkeypatch, backend):
    be, _ = backend

    def boom(**_kw):
        raise EndpointConnectionError(endpoint_url="https://runtime.sagemaker.example")

    monkeypatch.setattr(be.rt, "invoke_endpoint", boom)
    with pytest.raises(HTTPError) as ei:
        complete(be)
    assert ei.value.status == 503


def test_mapped_errors_drive_the_retry_loop(sleeps, monkeypatch, backend):
    """End to end: a throttled call is retried, a status-0 ModelError is not."""
    be, _ = backend
    model_error = be.rt.exceptions.ModelError
    throttled = ClientError({"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}}, "InvokeEndpoint")
    timeout = model_error(
        {"Error": {"Code": "ModelError", "Message": "timeout"}, "OriginalStatusCode": 0}, "InvokeEndpoint"
    )
    body = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()
    outcomes: list[Exception | None] = [throttled, throttled, None, timeout]

    def invoke(**_kw):
        o = outcomes.pop(0)
        if o is not None:
            raise o
        return {"Body": io.BytesIO(body)}

    monkeypatch.setattr(be.rt, "invoke_endpoint", invoke)
    r, retries, _ = asyncio.run(transcribe.call_with_retries(lambda: be.complete(REQ), 3))
    assert (r.text, retries, len(sleeps)) == ("ok", 2, 2)
    with pytest.raises(HTTPError) as ei:
        asyncio.run(transcribe.call_with_retries(lambda: be.complete(REQ), 3))
    assert ei.value.status == 0 and len(sleeps) == 2 and outcomes == []
