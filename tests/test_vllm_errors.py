"""Self-hosted vLLM backend: retryable SageMaker errors and no host in the recorded model name."""

from __future__ import annotations

import asyncio

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
