"""Every metric the reports show, defined once: column label, format, which direction is better,
tooltip and glossary text. report.md, the site tables and the glossary are all built from here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DASH = "–"


@dataclass(frozen=True)
class Metric:
    key: str  # field name in scores.json rows
    label: str  # column header
    kind: str = "text"  # pct | sec | rate | int | f1 | usd | text
    better: str | None = None  # "low" | "high" | None
    title: str = ""  # tooltip
    glossary: tuple[str, str] | None = None  # (term, explanation) for "Metrics" sections


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fmt(v: Any, kind: str) -> str:
    """Format a metric value by kind."""
    if kind == "text":
        return DASH if v is None or v == "" else str(v)
    n = _num(v)
    if n is None:
        return DASH
    if kind == "pct":
        return f"{n * 100:.1f}%"
    if kind == "sec":
        return f"{n:.2f}"
    if kind == "rate":
        return f"{n:.3f}"
    if kind == "int":
        return f"{n:,.0f}".replace(",", " ")
    if kind == "f1":
        return f"{n:.1f}"
    if kind == "usd":
        return f"${n:,.2f}"
    return str(v)


_ALL = [
    # identity columns
    Metric("backend", "Backend"),
    Metric("model", "Model"),
    Metric("variant", "Variant"),
    Metric("label", "Input → extractor"),
    Metric("pipeline", "Pipeline id"),
    Metric("concurrency", "Conc.", "int"),
    Metric("pages", "Pages", "int"),
    Metric("docs", "Docs", "int"),
    # speed
    Metric("ok", "OK", "int", "high"),
    Metric("errors", "Errors", "int", "low"),
    Metric("wall_s", "Wall (s)", "sec", "low"),
    Metric("pages_per_min", "Pages/min", "f1", "high"),
    Metric("p50_s", "p50 (s)", "sec", "low"),
    Metric("p95_s", "p95 (s)", "sec", "low"),
    Metric("max_s", "max (s)", "sec", "low"),
    Metric("mean_prompt_tokens", "Prompt tok", "int", None, "Mean prompt tokens per page"),
    Metric("mean_completion_tokens", "Compl. tok", "int", None, "Mean completion tokens per page"),
    Metric("retries", "Retries", "int", "low"),
    Metric(
        "cost_per_1k_pages_usd",
        "$/1k pages",
        "usd",
        "low",
        glossary=(
            "$/1k",
            "LLMs: list price x tokens used. Self-hosted models: GPU hourly price / best sustained throughput "
            "(GPU kept busy); the cost one request at a time is shown next to it",
        ),
    ),
    # transcription quality
    Metric(
        "coverage",
        "Coverage",
        "pct",
        "high",
        "Share of requests that succeeded",
        glossary=(
            "coverage",
            "share of pages or documents that got an answer. Failures count against the backend: "
            "CER 1 and no numbers found for a page, every field wrong for a document",
        ),
    ),
    Metric(
        "cer",
        "CER mean",
        "rate",
        "low",
        "Character error rate, mean over pages",
        glossary=(
            "CER",
            "character error rate after removing Markdown/HTML formatting; 0 = perfect. Sensitive to reading order.",
        ),
    ),
    Metric(
        "cer_median",
        "CER median",
        "rate",
        "low",
        "Character error rate of the typical page",
        glossary=("CER median", "the typical page; the mean can be dominated by a single runaway page"),
    ),
    Metric(
        "runaway",
        "Runaway",
        "int",
        "low",
        "Pages whose output is >3x the page length (repetition loop)",
        glossary=("runaway", "pages where the output was more than 3x the page length (a repetition loop)"),
    ),
    Metric("wer", "WER", "rate", "low", "Word error rate"),
    Metric(
        "word_recall",
        "Word recall",
        "pct",
        "high",
        glossary=(
            "word recall / precision",
            "share of the page's words found / share of output words that are on the page; ignores order",
        ),
    ),
    Metric("word_precision", "Word precision", "pct", "high"),
    Metric(
        "num_recall",
        "Number recall",
        "pct",
        "high",
        "Share of ground-truth numbers found",
        glossary=(
            "number recall",
            "share of the amounts, dates, account numbers etc. on the page that appear in the output",
        ),
    ),
    Metric("num_missing", "Numbers missing", "int", "low"),
    Metric(
        "num_hallucinated",
        "Invented numbers",
        "int",
        "low",
        "Numbers in the output that are not on the page",
        glossary=("invented numbers", "numbers of 3+ characters in the output that are not on the page"),
    ),
    # extraction
    Metric(
        "field_accuracy",
        "Field accuracy",
        "pct",
        "high",
        glossary=(
            "field accuracy",
            "share of extracted fields equal to the ground truth (case, spaces and a kr/NOK prefix ignored)",
        ),
    ),
    Metric("fields_correct", "Correct", "int"),
    Metric("fields_total", "Total", "int"),
    Metric("mean_latency_s", "Extractor latency (s)", "sec", "low", "Mean extraction latency per document"),
    Metric(
        "end_to_end_latency_s",
        "End-to-end (s)",
        "sec",
        "low",
        "Transcription + extraction latency per document",
        glossary=(
            "end-to-end",
            "per document: transcription time of all its pages (concurrency-1 runs) plus the extraction call",
        ),
    ),
    Metric("cost_per_1k_docs_usd", "$/1k docs", "usd", "low"),
]
REGISTRY: dict[str, Metric] = {m.key: m for m in _ALL}


def cols(*keys: str) -> list[Metric]:
    return [REGISTRY[k] for k in keys]


# Tables: the site shows every column; report.md the compact set.
SPEED = cols(
    "backend",
    "model",
    "concurrency",
    "pages",
    "ok",
    "errors",
    "wall_s",
    "pages_per_min",
    "p50_s",
    "p95_s",
    "max_s",
    "mean_prompt_tokens",
    "mean_completion_tokens",
    "retries",
    "cost_per_1k_pages_usd",
)
SPEED_MD = cols("backend", "concurrency", "pages", "ok", "pages_per_min", "p50_s", "p95_s", "cost_per_1k_pages_usd")
TRANSCRIPTION = cols(
    "backend",
    "variant",
    "pages",
    "coverage",
    "cer",
    "cer_median",
    "runaway",
    "wer",
    "word_recall",
    "word_precision",
    "num_recall",
    "num_missing",
    "num_hallucinated",
)
TRANSCRIPTION_MD = cols(
    "backend",
    "variant",
    "pages",
    "coverage",
    "cer",
    "cer_median",
    "runaway",
    "word_recall",
    "num_recall",
    "num_missing",
    "num_hallucinated",
)
EXTRACTION = cols(
    "label",
    "pipeline",
    "variant",
    "docs",
    "coverage",
    "field_accuracy",
    "fields_correct",
    "fields_total",
    "mean_latency_s",
    "end_to_end_latency_s",
    "cost_per_1k_docs_usd",
)
EXTRACTION_MD = cols(
    "pipeline",
    "variant",
    "docs",
    "coverage",
    "field_accuracy",
    "mean_latency_s",
    "end_to_end_latency_s",
    "cost_per_1k_docs_usd",
)

_GLOSSARY_ORDER = (
    "cer",
    "cer_median",
    "runaway",
    "word_recall",
    "num_recall",
    "num_hallucinated",
    "coverage",
    "field_accuracy",
    "end_to_end_latency_s",
    "cost_per_1k_pages_usd",
)
GLOSSARY: list[tuple[str, str]] = [REGISTRY[k].glossary for k in _GLOSSARY_ORDER]  # type: ignore[misc]


def markdown_table(rows: list[dict], columns: list[Metric]) -> list[str]:
    out = ["| " + " | ".join(c.label for c in columns) + " |", "|" + "---|" * len(columns)]
    out += ["| " + " | ".join(fmt(r.get(c.key), c.kind) for c in columns) + " |" for r in rows]
    return out
