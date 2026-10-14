import pytest

from ocrbench import summarize
from ocrbench.summarize import build_prompt, unverified_numbers

SRC = "accuracy 98.8% at $7.44/1k docs, ~307 pages/hour, 1 686 pages, 4 vCPU, at concurrency 32"


@pytest.mark.parametrize("text", ["5% better", "5%.", "-5%", "up 5 %"])
def test_percent_is_checked_before_any_following_char(text):
    assert unverified_numbers(text, SRC) == ["5"]


@pytest.mark.parametrize("text", ["$6/1k docs", "3 s/doc", "5 pages/hour", "2 ms", "6 per hour", "USD 6"])
def test_single_digits_with_a_unit_are_checked(text):
    assert unverified_numbers(text, SRC) != []


@pytest.mark.parametrize(
    "text",
    [
        "step 1, then step 2",
        "1. first\n2. second",
        "Claude Sonnet 4.5 and Opus 5.5 and Llama 3.1",
        "`opus-5.5` and `sonnet-4.5->agent`",
        "opus-5.5 wrote it",
        "4 vCPU",
    ],
)
def test_counts_model_names_and_keys_are_not_claims(text):
    assert unverified_numbers(text, SRC) == []


def test_version_digits_never_verify_a_claim():
    src = "Claude Sonnet 4.5 (2025-09-29) reads `sonnet-4.5`"
    assert unverified_numbers("4.5 s/doc", src) == ["4.5"]
    assert unverified_numbers("29 pages/min", src) == ["29"]  # a date's digits are not a fact
    assert unverified_numbers("released 2025-09-29", src) == []
    assert unverified_numbers("released 2025-10-01", src) == ["2025-10-01"]


def test_factors_and_percentages_only_match_their_own_kind():
    src = "invented numbers: 3 | 85; most accurate 97.0%"
    assert unverified_numbers("3x more", src) == ["3"]  # a count of 3 doesn't make a ratio of 3
    assert unverified_numbers("3 times faster", src) == ["3"]
    assert unverified_numbers("85%", src) == ["85"]
    assert unverified_numbers("97%", src) == []  # rounding of 97.0%
    assert unverified_numbers("2.1x faster", "speed-up 2.1x") == []
    assert unverified_numbers("Claude Haiku 98.2% on degraded", src) == ["98.2"]  # a percent is not a version


def test_concurrency_verifies_only_a_concurrency():
    assert unverified_numbers("32 pages/min", SRC) == ["32"]
    assert unverified_numbers("at concurrency 32", SRC) == []


def test_instructions_ask_for_digits():
    assert "in digits" in summarize.INSTRUCTIONS and "twice as fast" in summarize.INSTRUCTIONS


def _meta() -> dict:
    return {
        "id": "r",
        "transcribers": ["ocr", "s"],
        "backends": {
            "ocr": {"kind": "selfhosted", "hf_repo": "x/Some-OCR"},
            "s": {"kind": "bedrock", "model": "anthropic.claude-sonnet-5-5"},
        },
        "extraction": {"agent": "s", "pipelines": ["image->agent", "ocr->agent", "s->agent"]},
        "documents": {"count": 1, "page_images": 2, "types": {"invoice": {}}},
    }


def _scores() -> dict:
    agg = [
        {"pipeline": p, "variant": "clean", "field_accuracy": a, "end_to_end_latency_s": 2.5, "cost_per_1k_docs_usd": 1}
        for p, a in (("image->agent", 0.97), ("ocr->agent", 0.955), ("s->agent", 0.912))
    ]
    return {"speed": [], "transcription": {"aggregate": [], "pages": []}, "extraction": {"aggregate": agg, "docs": []}}


def test_prompt_names_hidden_pipelines_without_their_numbers():
    prompt, source = build_prompt(_meta(), _scores())
    assert "Pipelines run but not shown: `s->agent`" in prompt and "against itself" in prompt
    assert "91.2" not in prompt  # the hidden pipeline's result stays out of the facts
    assert unverified_numbers("`s->agent` got 91.2%", source) == ["91.2"]


def test_prompt_names_pipelines_hidden_as_repeats():
    meta = _meta()
    meta["report"] = {"hide_backends": ["old"]}
    meta["backends"]["old"] = {"kind": "bedrock", "model": "anthropic.claude-haiku-4-5"}
    meta["extraction"]["pipelines"].append("image->old")
    prompt, _ = build_prompt(meta, _scores())
    assert "`image->old`: run, but left out of the key facts because they use `old`" in prompt
