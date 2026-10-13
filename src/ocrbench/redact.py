"""Scrub error text before it is written to records that get committed and published.

Provider errors can carry account ids, ARNs, hostnames and request ids. Records keep the error class,
an HTTP status if any, and a message with those removed.
"""

from __future__ import annotations

import re

_PATTERNS = [
    (re.compile(r"arn:aws[a-z-]*:[^\s\"',)]*"), "<arn>"),
    (re.compile(r"https?://[^\s\"',)]+"), "<url>"),
    (re.compile(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:amazonaws\.com|internal|local|net|com|io)\b", re.I), "<host>"),
    (re.compile(r"\b\d{12}\b"), "<account>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<id>"),
    (re.compile(r"(?i)(bearer|token|key|secret|password)[=: ]+\S+"), r"\1=<redacted>"),
]


def redact(text: str, limit: int = 300) -> str:
    for rx, repl in _PATTERNS:
        text = rx.sub(repl, text)
    return text[:limit]


def error_text(e: BaseException) -> str:
    return redact(f"{type(e).__name__}: {e}")
