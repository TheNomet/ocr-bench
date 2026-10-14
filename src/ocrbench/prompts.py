"""Prompts and output-token limits for every stage, in one place.

Defaults are below; `bench.prompts.<stage>` in the config overrides any field. The resolved
prompts are recorded in run.json (`prompts`), and the stages read them from there, so a resumed
or re-scored run always uses what it started with. A self-hosted backend's own request body
(`backends.<name>.request`) replaces the transcription prompt for that backend.
"""

from __future__ import annotations

import copy

DEFAULTS: dict[str, dict] = {
    "transcribe": {
        "system": "You are a precise OCR engine. You transcribe documents; you never summarise, translate or correct them.",
        "user": (
            "Transcribe this document page to Markdown. Reproduce all text exactly as written, in reading order, "
            "including every number, date, account number and table (as Markdown tables). Do not summarise, "
            "translate, correct or add anything. Output only the transcription."
        ),
        "max_tokens": 8192,
    },
    "extract": {
        "system": (
            "You extract structured data from documents. You copy values exactly as they appear in the "
            "document (same number formatting, same spelling). You answer with one JSON object and nothing else."
        ),
        # appended after the generated field list (document type, schema, input kind come from the run)
        "rules": (
            "Copy values verbatim. For list fields, return every matching item. "
            "If a value is genuinely absent, omit the key. Reply with JSON only."
        ),
        "max_tokens": 2048,
    },
    "summarize": {
        "system": (
            "You are a careful technical analyst. An engineering team must choose how to read documents. You are "
            "given key facts that were computed exactly from a benchmark. Your job is to interpret them: explain "
            "what they mean, the trade-offs, and what to choose for which goal. You never compute new numbers."
        ),
        "max_tokens": 12000,
        "temperature": 0.2,
    },
}


def resolve(bench: dict) -> dict[str, dict]:
    """Defaults overlaid with bench.prompts (field by field)."""
    out = copy.deepcopy(DEFAULTS)
    for stage, over in (bench.get("prompts") or {}).items():
        if stage not in out:
            raise ValueError(f"bench.prompts.{stage}: unknown stage ({', '.join(out)})")
        if over is None:  # `transcribe:` with nothing under it = no override
            continue
        unknown = set(over) - set(out[stage])
        if unknown:
            raise ValueError(f"bench.prompts.{stage}: unknown field(s) {sorted(unknown)}")
        out[stage].update(over)
    return out


def of(meta: dict, stage: str) -> dict:
    """The prompts a run recorded (runs from before prompts were recorded used the defaults)."""
    return {**DEFAULTS[stage], **((meta.get("prompts") or {}).get(stage) or {})}
