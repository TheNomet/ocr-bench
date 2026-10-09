"""Static explanations shown in every report and run page (legend, glossary)."""

from __future__ import annotations

PIPELINES = {
    "image": (
        "One step: the extraction agent reads the page images directly and returns the fields. "
        "This is the baseline: what you get without any separate OCR step."
    ),
    "ocr": (
        "Two steps: the self-hosted OCR model transcribes every page to text, then the extraction agent "
        "reads that text (not the images) and returns the fields."
    ),
    "llm": (
        "Two steps: this LLM transcribes every page to text, then the extraction agent reads that text "
        "and returns the fields."
    ),
}

ARROW = (
    "Pipeline names read `<what produces the input> -> agent`. The left side is what the extraction "
    "agent gets to read: `image` = the page images themselves, anything else = the text transcript "
    "produced by that backend in stage 1. The agent is the same model in every pipeline, so the "
    "pipelines differ only in the input it sees."
)

STAGES = [
    (
        "1. Transcribe",
        "Every page image (clean and degraded) is sent to each transcriber, at several concurrency "
        "levels. This measures how well and how fast each backend reads.",
    ),
    (
        "2. Extract",
        "The extraction agent pulls each document type's fields (totals, dates, names, checked boxes…) "
        "out of each pipeline's input. Scored field by field against the ground truth.",
    ),
    ("3. Score", "Transcripts and fields are compared with the ground truth; latency and cost are aggregated."),
    (
        "4. Summarise",
        "An LLM reads the setup and all result tables and writes the findings below. It is told to "
        "use only the numbers provided; check them against the tables.",
    ),
]

VARIANTS = {
    "clean": "straight render of the document, as a perfect scan would look",
    "degraded": "simulated phone photo: tilted, downscaled, blurred, noisy, warm tint, low-quality JPEG",
}

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

METRICS = [
    ("CER", "character error rate after removing Markdown/HTML formatting; 0 = perfect. Sensitive to reading order."),
    ("CER median", "the typical page; the mean can be dominated by a single runaway page"),
    ("runaway", "pages where the output was more than 3x the page length (a repetition loop)"),
    (
        "word recall / precision",
        "share of the page's words found / share of output words that are on the page; ignores order",
    ),
    ("number recall", "share of the amounts, dates, account numbers etc. on the page that appear in the output"),
    ("invented numbers", "numbers of 3+ characters in the output that are not on the page"),
    (
        "field accuracy",
        "share of extracted fields equal to the ground truth (case, spaces and a kr/NOK prefix ignored)",
    ),
    ("end-to-end", "per document: transcription time of all its pages (concurrency-1 runs) plus the agent call"),
    (
        "$/1k",
        "LLMs: list price x tokens used. OCR model: GPU hourly price / measured throughput, i.e. with the GPU kept busy",
    ),
]


def pipeline_text(pipe: str, ocr_name: str = "ocr", extractor: str | None = None) -> str:
    src = pipe.split("->")[0]
    if src == "image":
        t = PIPELINES["image"]
    elif src == "ocr":
        t = PIPELINES["ocr"].replace("self-hosted OCR model", f"self-hosted OCR model ({ocr_name})")
    else:
        t = PIPELINES["llm"].replace("this LLM", f"`{src}`")
    return t + (f" Extractor: {extractor}." if extractor else "")


# --------------------------------------------------------------------------- model names
_CLAUDE = __import__("re").compile(r"claude-(?P<fam>[a-z]+)-(?P<maj>\d+)(?:-(?P<min>\d))?(?:-(?P<date>\d{8}))?")


def model_label(model_id: str | None) -> str:
    """'anthropic.claude-sonnet-4-5-20250929-v1:0' -> 'Claude Sonnet 4.5 (2025-09-29)'. Unknown ids pass through."""
    if not model_id:
        return "?"
    m = _CLAUDE.search(model_id)
    if not m:
        return model_id
    ver = m["maj"] + (f".{m['min']}" if m["min"] else "")
    date = m["date"]
    return f"Claude {m['fam'].capitalize()} {ver}" + (f" ({date[:4]}-{date[4:6]}-{date[6:]})" if date else "")


def ocr_label(ocr: dict) -> str:
    return f"{ocr.get('hf_repo', '?')} @ {(ocr.get('hf_revision') or '')[:8]}"


# Static description of the extraction agent; only the model is filled in from the run.
AGENT_INPUT = (
    "the document type's field list (name, type, one-line description), plus either the page images "
    "(`image->agent`) or the text transcript of every page, in order (`<transcriber>->agent`)"
)


AGENT_OUTPUT = "one JSON object with the field values copied verbatim; max 2,048 output tokens"


def agent_note(model_id: str | None, temperature: float | None = 0.0, others: list[str] | None = None) -> str:
    t = "provider default" if temperature is None else f"{temperature:g}"
    extra = (
        f" Pipelines that name another extractor ({', '.join(others)}) use that model with the same input."
        if others
        else " It is the same model in every pipeline."
    )
    return (
        f"The **agent** (default extractor) is {model_label(model_id)} (`{model_id}`), temperature {t}. "
        f"It gets {AGENT_INPUT}, and returns {AGENT_OUTPUT}. It never sees the ground truth.{extra}"
    )
