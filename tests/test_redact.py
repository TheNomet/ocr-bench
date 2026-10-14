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
        # Fake key ids, split so secret scanners don't flag the test itself
        ("key id " + "AKIA" + "ABCDEFGHIJKLMNOP is invalid", "AKIA" + "ABCDEFGHIJKLMNOP", "<access-key-id>"),
        ("session " + "ASIA" + "ABCDEFGHIJKLMNOP expired", "ASIA" + "ABCDEFGHIJKLMNOP", "<access-key-id>"),
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
        ("connect to 2001:0db8:85a3:0000:0000:8a2e:0370:7334 refused", "8a2e", "connect to <ip> refused"),
        ("connect to 2001:db8::8a2e:370:7334 refused", "8a2e", "connect to <ip> refused"),
        ("dial [fe80::1ff:fe23:4567:890a]:8000 failed", "fe23", "dial [<ip>]:8000 failed"),
        ("from ::ffff:10.0.0.1 denied", "10.0.0", "from <ip> denied"),
        ("link fe80::1%eth0 down", "eth0", "link <ip> down"),
        ("route 2001:db8:42::/48 unreachable", "db8", "route <ip>/48"),
        ("connect fd00::abcd:12: refused", "abcd", "connect <ip>: refused"),
        ("peer 2001:db8::1.", "db8", "peer <ip>."),
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
        # look like IPv6 but aren't
        "started 12:30:45, done 12:31:02.250",
        "a :: b, and :: alone",
        "image@sha256:abcd1234 pulled",
        "aspect 16:9 and 4:3",
        "Foo::bar, a::b, std::vector, ns::Type::f, u8::MAX",
    ],
)
def test_ordinary_text_unchanged(text):
    assert redact(text) == text


def test_long_digests_and_arns_keep_their_rules():
    assert redact("pull x@sha256:" + "ab" * 32 + " ok") == "pull x@sha256:<id> ok"
    assert redact("denied arn:aws:iam::123456789012:role/r ok") == "denied <arn> ok"


def test_truncated_after_redaction():
    assert len(redact("x" * 1000)) == 300 and len(redact("x" * 1000, limit=10)) == 10
    # The account id straddles the limit: redacting first means no digits of it survive.
    out = redact("y" * 290 + " 123456789012 tail")
    assert len(out) == 300 and "1234" not in out


def test_idempotent():
    for text in [
        "Authorization: Bearer abc",
        "token=abc",
        "Authorization: AWS4-HMAC-SHA256 Credential=a",
        "[fe80::1]:80",
    ]:
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
