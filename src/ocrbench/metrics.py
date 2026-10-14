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
            "(fastest run with at most 1% errors, GPU kept busy; if none, the run with the fewest errors, flagged); "
            "the cost one request at a time is shown next to it. Unknown token usage = no cost, never $0",
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
            "share of pages or documents that got an answer. Failures, and selected pages or documents never "
            "attempted, count against the backend: "
            "CER 1 and no numbers found for a page, every field wrong for a document. Cheapest and fastest "
            "pipeline only compare pipelines with at least 99% coverage",
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
            "character error rate after removing Markdown/HTML formatting; 0 = perfect, capped at 1 per page "
            "(a failed or runaway page). Sensitive to reading order.",
        ),
    ),
    Metric(
        "cer_median",
        "CER median",
        "rate",
        "low",
        "Character error rate of the typical page",
        glossary=("CER median", "the typical page; the mean also counts each failed or runaway page as 1"),
    ),
    Metric(
        "runaway",
        "Runaway",
        "int",
        "low",
        "Pages whose output is >3x the page length (repetition loop)",
        glossary=("runaway", "pages where the output was more than 3x the page length (a repetition loop)"),
    ),
    Metric(
        "truncated",
        "Token limit",
        "int",
        "low",
        "Pages whose output stopped at the output-token limit (cut off, or empty)",
        glossary=(
            "token limit",
            "pages where the model used its whole output-token budget, so the text is cut off or empty "
            "(an empty one is a failed page, CER 1)",
        ),
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
            "share of extracted fields equal to the ground truth (case, spaces and a kr/NOK prefix ignored). "
            "A value given for an optional field the document doesn't have counts as one wrong field",
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
            "per document: transcription time of all its pages (each page's lowest-concurrency transcript) plus "
            "the extraction call",
        ),
    ),
    Metric("cost_per_1k_docs_usd", "$/1k docs", "usd", "low"),
    # run-to-run variation (scores["variation"]): one row per transcriber or pipeline and variant
    Metric("repeats", "Repeats", "int", None, "Identical repeats compared"),
    Metric("repeat", "Repeat", "int", None, "Repeat index (0 = first)"),
    *(
        Metric(
            f"{key}_{stat}",
            f"{label}: {name}",
            kind,
            "low" if stat == "std" else None,
            f"{label} of each repeat: {title}",
        )
        for key, label, kind in (
            ("cer", "CER mean", "rate"),
            ("cer_median", "CER median", "rate"),
            ("word_recall", "Word recall", "pct"),
            ("num_recall", "Number recall", "pct"),
            ("field_accuracy", "Field accuracy", "pct"),
        )
        for stat, name, title in (
            ("mean", "mean of repeats", "mean over the repeats"),
            ("min", "min", "worst-to-best range, lower end"),
            ("max", "max", "worst-to-best range, upper end"),
            ("std", "std", "sample standard deviation over the repeats"),
        )
    ),
    Metric(
        "pages_differing",
        "Pages not identical",
        "pct",
        "low",
        "Share of pages whose scored text differs between repeats",
        glossary=(
            "pages not identical",
            "share of pages whose normalised transcript (as scored) is not the same in every repeat; a failed "
            "request counts as a different answer",
        ),
    ),
    Metric(
        "page_cer_range_mean",
        "Per-page CER range",
        "rate",
        "low",
        "Mean over pages of max - min CER across repeats",
        glossary=("per-page CER range", "for each page, its highest minus its lowest CER over the repeats; averaged"),
    ),
    Metric("answers", "Answers", "int", None, "(document, field) answers compared between repeats"),
    Metric(
        "flip_rate",
        "Answers changed",
        "pct",
        "low",
        "Share of (document, field) answers not the same in every repeat",
        glossary=(
            "answers changed",
            "share of (document, field) answers whose normalised value is not the same in every repeat (a failed "
            "extraction or an omitted field is no value)",
        ),
    ),
    Metric(
        "correct_flip_rate",
        "Right/wrong flips",
        "pct",
        "low",
        "Share of answers right in some repeats and wrong in others",
        glossary=("right/wrong flips", "share of (document, field) answers right in some repeats and wrong in others"),
    ),
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
    "truncated",
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
    "truncated",
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
    "truncated",
    "word_recall",
    "num_recall",
    "num_hallucinated",
    "coverage",
    "field_accuracy",
    "end_to_end_latency_s",
    "cost_per_1k_pages_usd",
)
GLOSSARY: list[tuple[str, str]] = [REGISTRY[k].glossary for k in _GLOSSARY_ORDER]  # type: ignore[misc]

# Run-to-run variation tables (only for runs that repeated something). The site shows every column.
VARIATION_TRANSCRIPTION = cols(
    "backend",
    "variant",
    "concurrency",
    "repeats",
    "pages",
    "cer_mean",
    "cer_min",
    "cer_max",
    "cer_std",
    "cer_median_mean",
    "cer_median_min",
    "cer_median_max",
    "word_recall_min",
    "word_recall_max",
    "num_recall_mean",
    "num_recall_min",
    "num_recall_max",
    "num_recall_std",
    "pages_differing",
    "page_cer_range_mean",
)
VARIATION_TRANSCRIPTION_MD = cols(
    "backend",
    "variant",
    "repeats",
    "cer_mean",
    "cer_min",
    "cer_max",
    "cer_std",
    "num_recall_min",
    "num_recall_max",
    "pages_differing",
    "page_cer_range_mean",
)
VARIATION_EXTRACTION = cols(
    "label",
    "pipeline",
    "variant",
    "repeats",
    "docs",
    "field_accuracy_mean",
    "field_accuracy_min",
    "field_accuracy_max",
    "field_accuracy_std",
    "answers",
    "flip_rate",
    "correct_flip_rate",
)
VARIATION_EXTRACTION_MD = cols(
    "pipeline",
    "variant",
    "repeats",
    "field_accuracy_mean",
    "field_accuracy_min",
    "field_accuracy_max",
    "field_accuracy_std",
    "flip_rate",
    "correct_flip_rate",
)
REPEAT_TRANSCRIPTION = cols(
    "backend", "variant", "repeat", "pages", "coverage", "cer", "cer_median", "word_recall", "num_recall", "truncated"
)
REPEAT_EXTRACTION = cols(
    "pipeline",
    "variant",
    "repeat",
    "docs",
    "coverage",
    "field_accuracy",
    "fields_correct",
    "fields_total",
    "mean_latency_s",
)
VARIATION_GLOSSARY: list[tuple[str, str]] = [
    (
        "run-to-run range",
        "worst to best result over identical repeats (min–max of the per-repeat means); a gap between two pipelines "
        "or readers smaller than the larger of their two ranges is within the run-to-run range",
    ),
    ("std", "sample standard deviation (n-1) of the per-repeat means"),
    *(REGISTRY[k].glossary for k in ("pages_differing", "page_cer_range_mean", "flip_rate", "correct_flip_rate")),  # type: ignore[misc]
]


def per_repeat_rows(entries: list[dict], key: str) -> list[dict]:
    """One row per repeat of every scores["variation"] entry, carrying the entry's `key` (backend or pipeline)
    and variant, and `group` (key|variant) for highlighting the best repeat of each."""
    return [
        {key: a[key], "variant": a["variant"], "group": f"{a[key]}|{a['variant']}", **r}
        for a in entries
        for r in a.get("per_repeat") or []
    ]


def markdown_table(rows: list[dict], columns: list[Metric]) -> list[str]:
    out = ["| " + " | ".join(c.label for c in columns) + " |", "|" + "---|" * len(columns)]
    out += ["| " + " | ".join(fmt(r.get(c.key), c.kind) for c in columns) + " |" for r in rows]
    return out
