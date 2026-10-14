import json
from pathlib import Path

import pytest

from ocrbench import explain, reporting, view
from ocrbench.report import RunCard, build_experiments_index
from ocrbench.report.markdown_lite import inline, render


@pytest.mark.parametrize(
    "model_id, want",
    [
        ("anthropic.claude-sonnet-4-5-20250929-v1:0", "Claude Sonnet 4.5 (2025-09-29)"),
        ("anthropic.claude-sonnet-4-20250514-v1:0", "Claude Sonnet 4 (2025-05-14)"),  # not "4.2"
        ("claude-sonnet-4.5", "Claude Sonnet 4.5"),
        ("anthropic/claude-sonnet-4.5", "Claude Sonnet 4.5"),
        ("anthropic.claude-opus-5-5", "Claude Opus 5.5"),
        ("claude-opus-4-1@20250805", "Claude Opus 4.1 (2025-08-05)"),
        (
            "arn:aws:bedrock:eu-west-1:000000000000:inference-profile/eu.anthropic.claude-haiku-4-5-20251001-v1:0",
            "Claude Haiku 4.5 (2025-10-01)",
        ),
        ("claude-3-5-sonnet-20240620", "Claude Sonnet 3.5 (2024-06-20)"),
        ("gpt-4o", "gpt-4o"),
    ],
)
def test_model_label(model_id, want):
    assert explain.model_label(model_id) == want


def _meta() -> dict:
    return {
        "id": "r",
        "created": "2026-01-02T03:04:05",
        "plan": "p",
        "region": "eu-west-1",
        "transcribers": ["ocr", "s"],
        "backends": {
            "ocr": {"kind": "selfhosted", "hf_repo": "x/Some-OCR", "temperature": 0.0},
            "s": {"kind": "bedrock", "model": "anthropic.claude-sonnet-5-5", "temperature": 0.0},
            "writer": {"kind": "bedrock", "model": "anthropic.claude-opus-5-5", "temperature": 1},
            "unused": {"kind": "bedrock", "model": "anthropic.claude-haiku-5-5", "temperature": 1},
        },
        "extraction": {"agent": "s", "pipelines": ["image->agent", "ocr->agent"]},
        "summary": {"agent": "writer"},
        "documents": {
            "count": 1,
            "pages": 1,
            "page_images": 2,
            "seed": 1,
            "types": {"invoice": {"description": "an invoice", "docs": 1, "pages": 1, "fields": ["total"]}},
        },
        "runner": {"mode": "local"},
        "prompts": {},
    }


def _scores(acc: float = 0.97) -> dict:
    agg = [
        {"pipeline": p, "variant": "clean", "field_accuracy": a, "end_to_end_latency_s": 2.5, "cost_per_1k_docs_usd": 1}
        for p, a in (("image->agent", acc), ("ocr->agent", 0.955))
    ]
    return {"speed": [], "transcription": {"aggregate": [], "pages": []}, "extraction": {"aggregate": agg, "docs": []}}


def test_sampling_counts_only_backends_with_shown_results():
    meta = _meta()
    assert view.sampling(meta) == []  # the writer and an unused backend sample, but show no results
    meta["backends"]["s"]["temperature"] = 1
    assert view.sampling(meta) == ["s"]
    meta["report"] = {"hide_backends": ["s"]}
    assert view.sampling(meta) == []


def test_same_model_unknown_models_are_not_the_same():
    meta = {"backends": {"a": {"kind": "bedrock"}, "b": {"kind": "bedrock"}}, "extraction": {"agent": "b"}}
    assert not view.same_model(meta, "a->agent")


def test_hidden_default_agent_without_agents_map_does_not_leak():
    meta = {
        "report": {"hide_backends": ["old"]},
        "transcribers": ["ocr"],
        "backends": {
            "ocr": {"kind": "selfhosted", "hf_repo": "x/ocr"},
            "old": {"kind": "bedrock", "model": "anthropic.claude-sonnet-4-5"},
            "new": {"kind": "bedrock", "model": "anthropic.claude-sonnet-5-5"},
        },
        "extraction": {"agent": "old", "pipelines": ["image->agent", "image->new", "ocr->new"]},  # old run: no agents
        "summary": {"agent": "new"},
    }
    fm = view.focused_meta(meta)
    assert fm["extraction"]["agent"] == "new"
    assert fm["extraction"]["agents"] == {"image->new": "new", "ocr->new": "new"}
    assert "old" not in {r.key for r in view.model_rows(fm)}


def test_writer_details_from_summary_backend():
    meta = _meta()
    meta["summary"] = {
        "agent": "s",
        "backend": {"kind": "bedrock", "model": "anthropic.claude-sonnet-5-5", "reasoning_effort": "medium"},
    }
    rows = view.model_rows(meta)
    s_rows = [r for r in rows if r.key == "s"]
    assert [r.roles for r in s_rows] == [[view.ROLE_TRANSCRIBER, view.ROLE_AGENT], [view.ROLE_WRITER]]
    assert s_rows[1].reasoning_effort == "medium" and s_rows[1].label == "Claude Sonnet 5.5"
    # recorded only under summary.backend (not a stage 1-3 backend)
    meta["summary"] = {"agent": "w2", "backend": {"kind": "bedrock", "model": "anthropic.claude-opus-5-5"}}
    (w,) = [r for r in view.model_rows(meta) if r.key == "w2"]
    assert w.roles == [view.ROLE_WRITER] and w.model_id == "anthropic.claude-opus-5-5"
    # same settings as in stages 1-3: one row with both roles
    meta["summary"] = {"agent": "s", "backend": dict(meta["backends"]["s"])}
    assert [r.roles for r in view.model_rows(meta) if r.key == "s"] == [
        [view.ROLE_TRANSCRIBER, view.ROLE_AGENT, view.ROLE_WRITER]
    ]


def _run_dir(tmp_path: Path, rid: str, summary: str) -> Path:
    d = tmp_path / "runs" / rid
    d.mkdir(parents=True)
    (d / "summary.md").write_text(summary, encoding="utf-8")
    (d / "summary.json").write_text(json.dumps({"backend": "writer", "unverified_numbers": []}), encoding="utf-8")
    return d


SUMMARY = (
    "## Bottom line\nChoose `image->agent` (97.0%).\n\n## Recommendation\n| goal | choice |\n|---|---|\n| a | b |\n"
)


def test_report_rechecks_numbers_against_current_facts(tmp_path):
    d = _run_dir(tmp_path, "r", SUMMARY)
    md = reporting.report_md(_meta(), _scores(), d)
    assert "Unverified numbers" not in md
    stale = reporting.report_md(_meta(), _scores(acc=0.95), d)  # re-scored: 97.0% is no longer a fact
    assert "> **Unverified numbers** (not found in the key facts): 97.0" in stale
    assert json.loads((d / "summary.json").read_text())["unverified_numbers"] == []  # left untouched


def test_report_md_has_one_recommendation_heading(tmp_path):
    md = reporting.report_md(_meta(), _scores(), _run_dir(tmp_path, "r", SUMMARY))
    assert md.count("## Recommendation") == 1 and "## Choice by goal" in md


def test_report_md_no_sampling_note_for_writer_only(tmp_path):
    md = reporting.report_md(_meta(), _scores(), _run_dir(tmp_path, "r", SUMMARY))
    assert "temperature above 0" not in md


def test_comparison_missing_variant_is_a_dash():
    c = {"runs": ["a", "b"], "extraction": {"x": {"a": {"degraded": 0.5}}}, "reading": {}, "selfhosted_speed": {}}
    assert "| x | – / 50.0% | – |" in reporting.comparison_md(c)


def _card(rid: str, created: str | None, bottom: str = "", bad: list[str] | None = None) -> RunCard:
    m = {**_meta(), "id": rid, "created": created}
    return RunCard(meta=m, card_meta=m, scores=_scores(), headline={}, bottom_line=bottom, unverified_numbers=bad or [])


def test_comparison_and_index_order_by_created(tmp_path):
    old, new = _card("z-old", "2026-01-01T00:00:00"), _card("a-new", "2026-02-01T00:00:00", "Pick it.")
    assert reporting.comparison([old, new])["runs"] == ["a-new", "z-old"]
    html = build_experiments_index([old, new], tmp_path).read_text()
    assert 'href="a-new/index.html">a-new</a>, the newest run' in html
    assert html.index('<div class="run-id">a-new') < html.index('<div class="run-id">z-old')


def test_index_bottom_line_shows_unverified_warning(tmp_path):
    html = build_experiments_index([_card("r", "2026-01-01", "Costs $9/1k docs.", ["9"])], tmp_path).read_text()
    assert html.count("Unverified numbers (not in the key facts): 9") == 2  # latest box and run card


def test_run_card_rechecks_bottom_line(tmp_path):
    d = _run_dir(tmp_path, "r", "## Bottom line\nChoose `image->agent` (97.0%), $9/1k docs.\n")
    assert reporting.run_card(_meta(), _scores(), d).unverified_numbers == ["9"]


def test_index_prunes_stale_run_pages_only(tmp_path):
    site = tmp_path / "site"
    for name in ("r", "gone"):
        (site / name / "docs").mkdir(parents=True)
        (site / name / "assets").mkdir()
        (site / name / "index.html").write_text("x")
    (site / "notes").mkdir()
    (site / "notes" / "index.html").write_text("keep")
    build_experiments_index([_card("r", "2026-01-01")], site)
    assert (site / "r" / "index.html").exists() and not (site / "gone").exists()
    assert (site / "notes" / "index.html").exists() and (site / "assets").is_dir()
    (site / "gone" / "docs").mkdir(parents=True)
    (site / "gone" / "assets").mkdir()
    (site / "gone" / "index.html").write_text("x")
    build_experiments_index([_card("r", "2026-01-01")], site, prune=False)
    assert (site / "gone").exists()


def test_markdown_lite_strips_nul():
    assert inline("a\x000\x00b `c`") == "a0b <code>c</code>"
    assert render("x \x005\x00 y") == "<p>x 5 y</p>"
