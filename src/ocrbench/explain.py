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


def pipeline_text(pipe: str, ocr_name: str = "ocr") -> str:
    src = pipe.split("->")[0]
    if src == "image":
        return PIPELINES["image"]
    if src == "ocr":
        return PIPELINES["ocr"].replace("self-hosted OCR model", f"self-hosted OCR model ({ocr_name})")
    return PIPELINES["llm"].replace("this LLM", f"`{src}`")
