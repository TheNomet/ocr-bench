"""redact() keeps provider error text publishable: no credentials, account ids, ARNs, hosts or request ids."""

import pytest

from ocrbench.backends.base import HTTPError
from ocrbench.redact import error_text, redact


@pytest.mark.parametrize(
    ("text", "secret", "marker"),
    [
        ('{"access_token":"eyJabc.def"}', "eyJabc.def", "token=<redacted>"),
        ('{"api_key": "sk-123"}', "sk-123", "key=<redacted>"),
        ("{'client_secret': 'shh'}", "shh", "secret=<redacted>"),
        ("Authorization: Bearer abc.def-ghi", "abc.def-ghi", "Authorization: Bearer <redacted>"),
        ("Authorization: Basic dXNlcjpwYXNz", "dXNlcjpwYXNz", "Authorization: Basic <redacted>"),
        ('{"Authorization": "Basic dXNlcjpwYXNz"}', "dXNlcjpwYXNz", "Basic <redacted>"),
        ("authorization=AWS4-HMAC-SHA256 Credential=x/y, Signature=abc", "Signature=abc", "<redacted>"),
        ("Bearer abc.def-ghi expired", "abc.def-ghi", "Bearer=<redacted>"),
        ("GET /oauth?token=abc123&x=1 failed", "abc123", "token=<redacted>&x=1"),
        ("password: hunter2", "hunter2", "password=<redacted>"),
        ("token xyz rejected", "xyz", "token=<redacted>"),
        ("bad key sk-proj-AbCdEfGhIjKlMnOpQrSt used", "AbCdEfGh", "<key>"),
        ("jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2ln rejected", "eyJzdWIi", "<jwt>"),
        ("denied for arn:aws:sagemaker:eu-west-1:123456789012:endpoint/e-1", "123456789012", "<arn>"),
        ("arn:aws-us-gov:iam::123456789012:role/r", "role/r", "<arn>"),
        ("account 123456789012 is not allowed", "123456789012", "<account>"),
        ("key id AKIAABCDEFGHIJKLMNOP is invalid", "AKIAABCDEFGHIJKLMNOP", "<access-key-id>"),
        ("session ASIAABCDEFGHIJKLMNOP expired", "ASIAABCDEFGHIJKLMNOP", "<access-key-id>"),
        ("invalid_client 0oaAbCdEfGhIjKlMnOpQ", "0oaAbCdEfGhIjKlMnOpQ", "<client-id>"),
        ("KMS key alias/team-signing-key is disabled", "team-signing-key", "alias/<redacted>"),
        (
            "ValidationError: Endpoint acme-ocr-model of account 123456789012 not found.",
            "acme-ocr-model",
            "Endpoint <endpoint> of account <account> not found",
        ),
        ('Could not find endpoint "acme-ocr-model".', "acme-ocr", "endpoint <endpoint>"),
        (
            "AADSTS700016: Application with identifier 'x' was not found in the directory 'Acme Corp'.",
            "Acme Corp",
            "directory '<tenant>'",
        ),
        ("AADSTS90002: Tenant 'acmecorp' not found.", "acmecorp", "Tenant '<tenant>'"),
        (
            "AADSTS700027: Client assertion contains an invalid signature. Thumbprint of key used by client: 'A1B2C3'",
            "A1B2C3",
            "<thumbprint>",
        ),
        ("pull 123456789012.dkr.ecr.eu-west-1.amazonaws.com/x:1 denied", "dkr.ecr", "<registry>"),
        ("vpce-0a1b2c-xyz.runtime.sagemaker.eu-west-1.vpce.amazonaws.com refused", "vpce-0a1b2c", "<host>"),
        ("gateway.corp.internal timed out", "gateway.corp", "<host>"),
        ("llm-gw.acme.example.zz timed out", "acme", "<host>"),
        ("connect to gateway.example.com failed", "example.com", "<host>"),
        ("resolve api.example.co.uk failed", "example.co", "<host>"),
        ("HTTPSConnectionPool(host='gw-prod', port=443): Max retries", "gw-prod", "host='<host>'"),
        ("dial //llm.example:443 failed", "llm.example", "//<host>"),
        ("user ci-bot@build.example rejected", "build.example", "<email>"),
        ("connect to 10.20.30.40:8000 refused", "10.20.30.40", "<ip>"),
        ("see https://example.com/a?b=c", "example.com", "<url>"),
        ("s3://acme-bucket/runs/x not found", "acme-bucket", "<url>"),
        ("RequestId: 0f3e1b2a-1c2d-4e5f-8a9b-0c1d2e3f4a5b", "0f3e1b2a", "<id>"),
        # a UUID whose last group is 12 digits must not be split by the account rule
        ("RequestId: 0f3e1b2a-1c2d-4e5f-8a9b-123456789012", "0f3e1b2a", "<id>"),
        ("trace 0123456789abcdef0123456789abcdef", "0123456789abcdef", "<id>"),
        (
            "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADAN\n-----END PRIVATE KEY----- bad",
            "MIIEvQIBADAN",
            "<private-key> bad",
        ),
    ],
)
def test_redacts(text, secret, marker):
    out = redact(text)
    assert secret not in out and marker in out, out


@pytest.mark.parametrize(
    "text",
    [
        "HTTP 503: model not ready",
        "e.g. the model returned nothing",
        "wrote report.md and model.safetensors",
        "missing outputs/m/doc-1.p1.r1.x2.md",
        "vllm 0.11.2 on torch-2.6.0 with CUDA 12.8",
        "baidu/Unlimited-OCR@sagemaker failed",
    ],
)
def test_ordinary_text_unchanged(text):
    assert redact(text) == text


def test_truncated_after_redaction():
    assert len(redact("x" * 1000)) == 300 and len(redact("x" * 1000, limit=10)) == 10
    # The account id straddles the limit: redacting first means no digits of it survive.
    out = redact("y" * 290 + " 123456789012 tail")
    assert len(out) == 300 and "1234" not in out


def test_idempotent():
    for text in ["Authorization: Bearer abc", "token=abc", "Authorization: AWS4-HMAC-SHA256 Credential=a"]:
        once = redact(text)
        assert redact(once) == once


def test_http_error_redacts_before_truncating():
    e = HTTPError(500, "a" * 280 + " 123456789012 account")
    assert "1234" not in str(e) and "<account>" in str(e)
    assert "gw.example.internal" not in str(HTTPError(503, "connect gw.example.internal")) and e.status == 500


def test_error_text_keeps_class_and_status():
    assert error_text(RuntimeError("HTTP 429: account 123456789012 throttled")) == (
        "RuntimeError: HTTP 429: account <account> throttled"
    )
