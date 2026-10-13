"""redact() keeps provider error text publishable: no credentials, account ids, ARNs, hosts or request ids."""

import pytest

from ocrbench.redact import error_text, redact


@pytest.mark.parametrize(
    ("text", "secret", "marker"),
    [
        ('{"access_token":"eyJabc.def"}', "eyJabc.def", "token=<redacted>"),
        ('{"api_key": "sk-123"}', "sk-123", "key=<redacted>"),
        ("{'client_secret': 'shh'}", "shh", "secret=<redacted>"),
        ("Authorization: Bearer abc.def-ghi", "abc.def-ghi", "Bearer=<redacted>"),
        ("GET /oauth?token=abc123&x=1 failed", "abc123", "token=<redacted>&x=1"),
        ("password: hunter2", "hunter2", "password=<redacted>"),
        ("token xyz rejected", "xyz", "token=<redacted>"),
        ("denied for arn:aws:sagemaker:eu-west-1:123456789012:endpoint/e-1", "123456789012", "<arn>"),
        ("arn:aws-us-gov:iam::123456789012:role/r", "role/r", "<arn>"),
        ("account 123456789012 is not allowed", "123456789012", "<account>"),
        ("vpce-0a1b2c-xyz.runtime.sagemaker.eu-west-1.vpce.amazonaws.com refused", "vpce-0a1b2c", "<host>"),
        ("gateway.corp.internal timed out", "gateway.corp", "<host>"),
        ("see https://example.com/a?b=c", "example.com", "<url>"),
        ("RequestId: 0f3e1b2a-1c2d-4e5f-8a9b-0c1d2e3f4a5b", "0f3e1b2a", "<id>"),
    ],
)
def test_redacts(text, secret, marker):
    out = redact(text)
    assert secret not in out and marker in out, out


def test_plain_text_unchanged_and_truncated():
    assert redact("HTTP 503: model not ready") == "HTTP 503: model not ready"
    assert len(redact("x" * 1000)) == 300 and len(redact("x" * 1000, limit=10)) == 10


def test_error_text_keeps_class_and_status():
    assert error_text(RuntimeError("HTTP 429: account 123456789012 throttled")) == (
        "RuntimeError: HTTP 429: account <account> throttled"
    )
