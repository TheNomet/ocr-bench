"""Scrub error text before it is written to records that get committed and published.

Provider errors can carry account ids, ARNs, hostnames, IPs, keys and request ids. Records keep the
error class, an HTTP status if any, and a message with those removed. Rules run in order: ones that
consume longer tokens (URLs, ARNs, UUIDs) come before ones that would match a piece of them (account ids).
"""

from __future__ import annotations

import re
from collections.abc import Callable

_LABEL = r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
# Last labels that mean "file name", not "host", for names with two or more dots (doc-1.p1.r1.md).
_FILE_EXT = (
    r"md|json|jsonl|ya?ml|toml|txt|log|csv|html?|py|sh|png|jpe?g|webp|gif|pdf|safetensors|bin|pt|onnx|gguf"
    r"|lock|gz|tgz|tar|zip|tmp|bak|cfg|ini"
)
_TLDS = r"com|net|org|io|ai|cloud|de|no|se|dk|uk|eu|internal|local|corp|lan|intra|intranet|cn"
_IPV4 = r"(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
_H16 = r"[0-9a-f]{1,4}"
# Full, compressed and IPv4-suffixed IPv6, with an optional zone id (fe80::1%eth0). Not inside a longer
# token, and it must contain a digit, so `a::b` scopes and a bare `::` stay; times (12:30:45) have
# neither eight groups nor `::`.
_IPV6 = (
    r"(?<![\w:.])(?=[0-9a-f:]*\d)(?:"
    rf"(?:{_H16}:){{6}}{_IPV4}"
    rf"|(?:{_H16}(?::{_H16}){{0,4}})?::(?:{_H16}:){{0,5}}{_IPV4}"
    rf"|(?:{_H16}:){{7}}{_H16}"
    rf"|(?:{_H16}(?::{_H16}){{0,6}})?::(?:{_H16}(?::{_H16}){{0,6}})?"
    r")(?:%[\w.~-]+)?(?![\w%]|:[0-9a-f]|\.\d)"
)

_PATTERNS: list[tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]]] = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S), "<private-key>"),
    (re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s\"'<>,)]+", re.I), "<url>"),
    (re.compile(r"arn:aws[a-z-]*:[^\s\"',)]*"), "<arn>"),
    (re.compile(r"\b\d{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com(?:\.cn)?\b", re.I), "<registry>"),
    # `Authorization: Basic x`, `"Authorization": "Bearer x"`, AWS SigV4 (`AWS4-HMAC-SHA256 Credential=..., ...`)
    (
        re.compile(
            r"(?i)\b(authorization[\"']?\s*[:=]\s*[\"']?)(?:(aws4-hmac-sha256)\s+[^\"'\r\n}]+"
            r"|(?:(basic|bearer|digest|negotiate|token)\s+)?(?!<|(?:basic|bearer|digest|negotiate|token)\s)[^\s\"',}]+)"
        ),
        lambda m: f"{m[1]}{(m[2] or m[3]) + ' ' if m[2] or m[3] else ''}<redacted>",
    ),
    (re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]*"), "<jwt>"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "<key>"),
    (re.compile(r"\b(?:AKIA|ASIA|AIDA|AROA|ANPA)[A-Z0-9]{16}\b"), "<access-key-id>"),
    (re.compile(r"\b0oa[a-zA-Z0-9]{17,}\b"), "<client-id>"),
    (re.compile(r"\balias/[A-Za-z0-9/_-]+"), "alias/<redacted>"),
    # botocore: "Endpoint <name> of account <acct> not found." / "Could not find endpoint "<name>"."
    (
        re.compile(
            r"(?i)\b(endpoint(?:[ -]config(?:uration)?)?|model)\s+[\"']?[A-Za-z0-9-]+[\"']?(?=\s+of\s+account\b)"
        ),
        r"\1 <endpoint>",
    ),
    (re.compile(r"(?i)\b(could not find (?:endpoint|model)\s+)[\"']?[A-Za-z0-9-]+[\"']?"), r"\1<endpoint>"),
    (re.compile(r"(?i)\b(endpoint_?name[\"']?\s*[=:]\s*[\"']?)[A-Za-z0-9-]+"), r"\1<endpoint>"),
    # Entra / Azure AD: "... in the directory 'Contoso'", "Tenant 'x' not found", key thumbprints
    (re.compile(r"(?i)\b(directory|tenant)\s+(['\"])[^'\"\r\n]*\2"), r"\1 \2<tenant>\2"),
    (re.compile(r"(?i)\b(thumbprint\b[^'\"\r\n]{0,60}?['\"])[^'\"\r\n]+(['\"])"), r"\1<thumbprint>\2"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<id>"),
    (re.compile(r"\b[0-9a-f]{32,}\b", re.I), "<id>"),
    (re.compile(r"\b[\w.+-]+@" + _LABEL + r"(?:\." + _LABEL + r")+\b", re.I), "<email>"),
    # before the host contexts (host='fe80::1' would lose only its first group) and the IPv4 rule (::ffff:10.0.0.1)
    (re.compile(_IPV6, re.I), "<ip>"),
    # host contexts: host='x' (urllib3), //x, @x
    (
        re.compile(r"(?i)\b(host(?:name)?|server|domain|netloc)([\"']?\s*[=:]\s*[\"']?)[^\s\"',)/:\]]+"),
        r"\1\2<host>",
    ),
    (re.compile(r"(?<=//)" + _LABEL + r"(?:\." + _LABEL + r")+", re.I), "<host>"),
    (re.compile(r"(?<=@)" + _LABEL + r"(?:\." + _LABEL + r")+", re.I), "<host>"),
    (re.compile(r"\b" + _IPV4 + r"\b"), "<ip>"),
    # any name with two or more dots whose last label is alphabetic and not a file extension
    (
        re.compile(
            r"\b(?:" + _LABEL + r"\.){2,}(?!(?:" + _FILE_EXT + r")\b)[a-z][a-z0-9-]*[a-z0-9]\b(?!\.?[a-z0-9-])", re.I
        ),
        "<host>",
    ),
    (re.compile(r"\b" + _LABEL + r"\.(?:" + _TLDS + r")\b(?![.-]?[a-z0-9])", re.I), "<host>"),
    (re.compile(r"\b\d{12}\b"), "<account>"),
    # `Bearer x`, `token=x`, `?token=x&...`, `key: x`, and JSON / quoted forms like `"access_token": "x"`
    (
        re.compile(
            r"(?i)(bearer|token|key|secret|password|passwd|credential|signature|client_?id)[\"']?(?:\s*[=:]\s*|\s+)"
            r"[\"']?(?![^\s\"',}&]*<)[^\s\"',}&]+"
        ),
        r"\1=<redacted>",
    ),
]


def redact(text: str, limit: int = 300) -> str:
    """Redact first, then truncate: a cut can never expose the start of a secret a rule would have caught."""
    for rx, repl in _PATTERNS:
        text = rx.sub(repl, text)
    return text[:limit]


def error_text(e: BaseException) -> str:
    return redact(f"{type(e).__name__}: {e}")
