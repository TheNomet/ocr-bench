"""Stage 4: an LLM reads the run setup + scored tables and writes the findings (summary.md)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from .backends import Request, get_backend
from .config import Config
from .transcribe import call_with_retries

SYSTEM = (
    "You are a careful technical analyst writing the findings section of a benchmark report for an "
    "engineering team that must choose a document-reading setup. You only state numbers that appear "
    "in the data you are given, and you say so when the data cannot answer a question."
)

INSTRUCTIONS = """Write the findings in Markdown with exactly these sections:

## Bottom line
2-4 sentences: which setup to choose for which goal, and the single most important reason.

## What was tested
One short paragraph: documents, backends (with model names), GPU host and its price, what the pipelines are.

## Speed
Single-request latency and throughput under concurrency; where the self-hosted GPU saturates.

## Accuracy
Transcription (clean vs degraded; numbers found and invented; runaway outputs) and field extraction per pipeline.
Name the field/document types that fail most and what that suggests.

## Cost
Per 1,000 pages/documents. For the self-hosted model, state that its figure assumes the GPU is kept busy and
compute roughly the pages/hour above which it is cheaper than each LLM option (show the arithmetic).

## Recommendation
A short table: goal (e.g. highest accuracy, lowest latency, lowest cost at volume, second-opinion checking) ->
choice -> why. Then 2-4 caveats about how far these results generalise (synthetic documents, sample size,
single GPU host, list prices).

Rules: no invented numbers; round sensibly; refer to pipelines by their exact names; keep it under ~600 words."""


def build_prompt(meta: dict, scores: dict) -> str:
    def slim(rows, keys):
        return [{k: r.get(k) for k in keys if k in r} for r in rows]

    misses: dict[str, dict[str, int]] = {}
    for d in scores["extraction"]["docs"]:
        for f, x in d["fields"].items():
            if not x["correct"]:
                key = f"{d['doc_id'].rsplit('-', 1)[0]}.{f}"
                misses.setdefault(d["pipeline"], {}).setdefault(key, 0)
                misses[d["pipeline"]][key] += 1
    data = {
        "setup": {
            k: meta.get(k)
            for k in (
                "documents",
                "ocr",
                "llm_backends",
                "transcribers",
                "extraction",
                "plan_items",
                "region",
                "runner",
            )
        },
        "speed": slim(
            scores["speed"],
            [
                "backend",
                "model",
                "concurrency",
                "pages",
                "ok",
                "errors",
                "pages_per_min",
                "p50_s",
                "p95_s",
                "max_s",
                "cost_per_1k_pages_usd",
            ],
        ),
        "transcription": scores["transcription"]["aggregate"],
        "extraction": scores["extraction"]["aggregate"],
        "most_missed_fields": {p: dict(sorted(m.items(), key=lambda x: -x[1])[:8]) for p, m in misses.items()},
    }
    from .explain import ARROW, METRICS

    glossary = "\n".join(f"- {k}: {v}" for k, v in METRICS)
    return (
        f"{INSTRUCTIONS}\n\nPipeline naming: {ARROW}\n\nMetric definitions:\n{glossary}\n\n"
        f"Data (JSON):\n```json\n{json.dumps(data, ensure_ascii=False, indent=1, default=str)}\n```"
    )


def run(cfg: Config, run_dir: Path, meta: dict, scores: dict) -> str:
    name = (cfg["bench"].get("summary") or {}).get("agent") or cfg["bench"]["extraction"]["agent"]
    be = get_backend(cfg, name)

    async def go():
        await be.warmup()
        resp, _ = await call_with_retries(
            lambda: be.complete(Request(SYSTEM, build_prompt(meta, scores), max_tokens=3000, temperature=0.2)),
            cfg["bench"].get("max_retries", 4),
        )
        return resp

    resp = asyncio.run(go())
    text = resp.text.strip()
    header = (
        f"<!-- LLM-written by {name} ({be.model}) from scores.json; "
        f"{resp.prompt_tokens} in / {resp.completion_tokens} out tokens -->\n"
    )
    (run_dir / "summary.md").write_text(header + text + "\n", encoding="utf-8")
    print(f"summary written by {name}: {len(text)} chars", flush=True)
    return text
