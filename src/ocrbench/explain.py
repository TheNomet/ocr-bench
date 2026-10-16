"""Static explanations shown in every report and run page (legend, glossary)."""

from __future__ import annotations

import re

PIPELINES = {
    "image": (
        "One step: the extraction agent reads the page images directly and returns the fields. "
        "This is the baseline: what you get without any separate OCR step."
    ),
}

ARROW = (
    "Pipeline names read `<input> -> <extractor>`. The left side is what the extracting model gets to "
    "read: `image` = the page images themselves, anything else = the text transcript produced by that "
    "backend in stage 1. The right side is the model that extracts the fields: `agent` = the run's "
    "default extraction agent, otherwise the named backend."
)

STAGES = [
    (
        "1. Transcribe",
        "Every page image (clean and degraded) is sent to each transcriber, at several concurrency "
        "levels. This measures how well and how fast each backend reads.",
    ),
    (
        "2. Extract",
        "An extractor pulls each document type's fields (totals, dates, names, checked boxes…) out of each "
        "pipeline's input: the run's extraction agent, or the model named after the arrow. Scored field by "
        "field against the ground truth.",
    ),
    ("3. Score", "Transcripts and fields are compared with the ground truth; latency and cost are aggregated."),
    (
        "4. Recommend",
        "An LLM reads the computed key facts and a short description of the setup, and writes a "
        "recommendation. It may not compute numbers. The numbers it writes in digits are checked against "
        "the facts, and any not found there are flagged as unverified; the check is automatic and can miss "
        "a claim, so the tables remain the reference.",
    ),
]

VARIANTS = {
    "clean": "straight render of the document, as a perfect scan would look",
    "degraded": "simulated phone photo: tilted, downscaled, blurred, noisy, warm tint, low-quality JPEG",
}

# What the "Run-to-run variation" section measures (shown only for runs that repeated something).
VARIATION = (
    "The same pages and documents were sent several times with identical settings, and each repeat is scored on "
    "its own. Some models accept only temperature 1 (no deterministic setting), so they sample and their answers "
    "change from run to run; this measures how much, so a difference between two models can be told apart from "
    "noise. A **range** is the worst to the best repeat; a gap between two models smaller than the larger of their "
    "two ranges is *within the run-to-run range* and not evidence that one is better. Repeats are measured at one "
    "concurrency (the lowest that has them, 1 in the example plan): sampling noise doesn't depend on how many "
    "requests run at once, so higher levels would only add cost. A transcript-based pipeline re-reads the same "
    "transcript in every repeat, so its range is the extractor's alone; an image-based pipeline's is the whole "
    "pipeline's. The main tables above score one repeat (the last)."
)

CHALLENGES = {
    "letter": "prose paragraphs with amounts and dates inside sentences",
    "receipt": "narrow monospace column, label and amount on opposite sides",
    "bank_statement": "dense 15-26 row table",
    "holiday_survey": "boxed key/value form filled in handwriting fonts, checkboxes",
    "invoice": "line-item table plus totals block",
    "payslip": "earnings/deductions table, several totals",
    "rental_contract": "two pages of dense numbered clauses",
    "newsletter": "two-column layout with an inline table",
}

# --------------------------------------------------------------------------- model names
# A minor version is one or two digits not followed by another digit, so the date in
# `claude-sonnet-4-20250514` is not read as minor version 2. Old ids put the version first: `claude-3-5-sonnet`.
_CLAUDE = re.compile(
    r"claude-(?P<fam>[a-z]+)-(?P<maj>\d+)(?:[-.](?P<min>\d{1,2})(?!\d))?(?:[-@](?P<date>\d{8}))?(?!\d)"
    r"|claude-(?P<maj2>\d+)(?:[-.](?P<min2>\d{1,2})(?!\d))?-(?P<fam2>[a-z]+)(?:[-@](?P<date2>\d{8}))?(?!\d)"
)


_GPT = re.compile(r"(?:^|[/.])gpt-(?P<ver>\d+(?:\.\d+)?)(?:-(?P<tier>[a-z]+))?(?:-(?P<date>\d{4}-\d{2}-\d{2}))?$")


def model_label(model_id: str | None) -> str:
    """'anthropic.claude-sonnet-4-5-20250929-v1:0' -> 'Claude Sonnet 4.5 (2025-09-29)',
    'openai/gpt-6.1-sol' -> 'GPT-6.1 Sol'. Unknown ids pass through."""
    if not model_id:
        return "?"
    if g := _GPT.search(model_id):
        tier = f" {g['tier'].capitalize()}" if g["tier"] else ""
        return f"GPT-{g['ver']}{tier}" + (f" ({g['date']})" if g["date"] else "")
    m = _CLAUDE.search(model_id)
    if not m:
        return model_id
    fam, maj, minor, date = (
        (m["fam"], m["maj"], m["min"], m["date"]) if m["fam"] else m.group("fam2", "maj2", "min2", "date2")
    )
    ver = maj + (f".{minor}" if minor else "")
    return f"Claude {fam.capitalize()} {ver}" + (f" ({date[:4]}-{date[4:6]}-{date[6:]})" if date else "")


# Static description of the extraction agent; only the model is filled in from the run.
AGENT_INPUT = (
    "the document type's field list (name, type, one-line description), plus either the page images "
    "(`image->agent`) or the text transcript of every page, in order (`<transcriber>->agent`)"
)


# The same input without the `->agent` pipeline names, for runs whose shown pipelines all name their extractor.
EXTRACTOR_INPUT = (
    "the document type's field list (name, type, one-line description), plus either the page images "
    "or the text transcript of every page, in order"
)


def agent_output(max_tokens: int = 2048) -> str:
    return f"one JSON object with the field values copied verbatim; max {max_tokens:,} output tokens"


def agent_note(
    model_id: str | None,
    temperature: float | None = 0.0,
    others: list[str] | None = None,
    label: str | None = None,
    max_tokens: int = 2048,
) -> str:
    t = "provider default" if temperature is None else f"{temperature:g}"
    extra = (
        f" Pipelines that name another extractor ({', '.join(others)}) use that model with the same input."
        if others
        else " It is the same model in every pipeline."
    )
    return (
        f"The **agent** (default extractor) is {label or model_label(model_id)} (`{model_id}`), temperature {t}. "
        f"It gets {AGENT_INPUT}, and returns {agent_output(max_tokens)}. It never sees the ground truth.{extra}"
    )


def extractors_note(extractors: list[tuple[str, str, str | None, float | None]], max_tokens: int = 2048) -> str:
    """agent_note for a run page whose default agent is hidden: the extractors the shown pipelines use,
    as (label, backend key, model id, temperature)."""

    def one(lbl: str, key: str, model_id: str | None, temperature: float | None) -> str:
        t = "provider default" if temperature is None else f"{temperature:g}"
        return f"{lbl} (`{key}`: `{model_id}`, temperature {t})"

    return (
        f"Extractors: {', '.join(one(*x) for x in extractors)}. Each pipeline names its extractor after the "
        f"arrow. The extractor gets {EXTRACTOR_INPUT}, and returns {agent_output(max_tokens)}. It never sees "
        "the ground truth."
    )


INTRO = "\n".join(
    [
        "**What is this?** ocr-bench answers a practical question: what is the best way to read documents "
        "automatically and pull out the data we need (amounts, dates, names, checked boxes)? There are two families "
        "of approaches:",
        "",
        "- **Send the page image straight to an LLM** and ask it for the fields (one step).",
        "- **First turn the page into text**, with a self-hosted OCR model on our own GPU or with an LLM, and then let "
        "an LLM extract the fields from that text (two steps).",
        "",
        "Each experiment below runs the same synthetic documents through several of these setups and measures "
        "**accuracy** (against exact ground truth), **speed** and **cost**. A run page shows the setup, the results, "
        "an LLM-written recommendation, and every document with what each model read.",
    ]
)
