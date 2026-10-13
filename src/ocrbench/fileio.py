"""Small file helpers shared by stages and reporting."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def write_if_changed(path: Path, text: str) -> bool:
    """Avoid rewriting committed files whose content didn't change (keeps git history quiet)."""
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def read_text(path: Path) -> str | None:
    """The file's text (undecodable bytes replaced), or None if it can't be read."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def read_jsonl(path: Path) -> list[dict]:
    """One JSON object per line. Blank lines are skipped; a line that isn't a JSON object (e.g. cut off by a
    crash mid-write) is skipped with a warning on stderr. A missing file reads as no records."""
    text = read_text(path)
    if text is None:
        return []
    out: list[dict] = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            out.append(obj)
        else:
            print(f"warning: {path}:{n}: skipped a line that is not a JSON object", file=sys.stderr)
    return out
