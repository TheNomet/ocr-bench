"""Small file helpers shared by stages and reporting."""

from __future__ import annotations

import json
from pathlib import Path


def write_if_changed(path: Path, text: str) -> bool:
    """Avoid rewriting committed files whose content didn't change (keeps git history quiet)."""
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
