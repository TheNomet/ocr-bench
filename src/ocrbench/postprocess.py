"""Named post-processing steps for a backend's raw output, applied before scoring and extraction.

A backend lists them in the config (`backends.<name>.postprocess: [strip_grounding]`); the list
is recorded in run.json, so re-scoring a run applies exactly what the run used. The raw output
on disk is never changed.
"""

from __future__ import annotations

import re
from collections.abc import Callable

_REF = re.compile(r"<\|ref\|>(.*?)<\|/ref\|>", re.S)
_DET = re.compile(r"<\|det\|>.*?<\|/det\|>", re.S)
_SPECIAL = re.compile(r"<\|[^|]*\|>|<｜[^｜]*｜>")
_THINK = re.compile(r"<think>.*?</think>\s*", re.S)
_FENCE = re.compile(r"\A\s*```[a-zA-Z]*\n(.*?)\n```\s*\Z", re.S)


def strip_grounding(text: str) -> str:
    """DeepSeek-OCR-family output: unwrap <|ref|>text<|/ref|>, drop <|det|>[boxes]<|/det|> and other <|...|> tokens."""
    text = _REF.sub(lambda m: m.group(1), text or "")
    return _SPECIAL.sub("", _DET.sub("", text))


def strip_think(text: str) -> str:
    """Reasoning models that inline their thinking: drop <think>...</think>."""
    return _THINK.sub("", text or "")


def unwrap_code_fence(text: str) -> str:
    """Output wrapped in one ```markdown ... ``` block: keep the inside."""
    m = _FENCE.match(text or "")
    return m.group(1) if m else (text or "")


_LOC = re.compile(r"<loc_\d+>")
_CELL = re.compile(r"<(?:fcel|ecel|lcel|ucel|xcel|ched|rhed|srow)>")
_CHECKED = re.compile(r"<checkbox_selected>")
_UNCHECKED = re.compile(r"<checkbox_unselected>")
_DOCTAG = re.compile(r"</?[a-z_]+(?:_\d+)?>")


def doctags_to_text(text: str) -> str:
    """Docling DocTags (Granite-Docling, SmolDocling): drop <loc_N> boxes, table cells -> `|` rows, <nl> ->
    line break, checkboxes -> ☑/☐, every other element tag -> line break."""
    t = _LOC.sub("", text or "")
    t = _CHECKED.sub("☑ ", _UNCHECKED.sub("☐ ", t))
    t = _CELL.sub(" | ", t).replace("<nl>", "\n")
    t = _DOCTAG.sub("\n", t)
    lines = (re.sub(r"[ \t]+", " ", ln).strip(" ") for ln in t.splitlines())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(ln.removeprefix("| ").strip() for ln in lines)).strip()


REGISTRY: dict[str, Callable[[str], str]] = {
    "strip_grounding": strip_grounding,
    "strip_think": strip_think,
    "unwrap_code_fence": unwrap_code_fence,
    "doctags_to_text": doctags_to_text,
}


def apply(steps: list[str] | None, text: str) -> str:
    for s in steps or []:
        text = REGISTRY[s](text)
    return text
