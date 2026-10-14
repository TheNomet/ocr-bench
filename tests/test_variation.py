"""Run-to-run variation: repeat records, extraction repeats, the scores.json `variation` block, its key facts,
the number check on them, and how reports show it."""

from __future__ import annotations

import asyncio
import json
import statistics as st
from pathlib import Path

import pytest
from PIL import Image

from ocrbench import config as config_mod
from ocrbench import extract, facts, reporting, runs, score, transcribe, view
from ocrbench.backends.base import Response
from ocrbench.report import RunPage, build_experiments_index, build_site
from ocrbench.summarize import build_prompt, unverified_numbers

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- records
class Echo:
    """Transcriber / extractor that answers with what the request asked for (deterministic)."""

    def __init__(self, name, docs=None):
        self.name, self.model, self.instance_type, self.docs = name, f"m:{name}", None, docs
        self.prompts: list[str] = []

    async def warmup(self):
        pass

    async def complete(self, req):
        self.prompts.append(req.prompt)
        if self.docs is not None and "extract these fields" in req.prompt:
            doc_id = req.prompt.split("Document type: ")[1].split(" ")[0]
            f = next(self.docs.glob(f"{doc_id}-*/fields.json"))
            return Response(json.dumps(json.loads(f.read_text())["fields"]), 10, 5)
        return Response("text", 1, 1, "stop")


def _lines(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text().splitlines()]


def test_transcript_records_carry_their_repeat(fake_docs, tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe, "get_backend", lambda c, n: Echo(n))
    cfg = {"bench": {"max_retries": 0}}
    asyncio.run(transcribe.run(cfg, fake_docs, tmp_path, backend="b", limit=2, repeat=3, warmup=False))  # pyright: ignore[reportArgumentType]
    recs = _lines(tmp_path / "transcripts.jsonl")
    assert sorted(r["repeat"] for r in recs) == [0, 0, 1, 1, 2, 2]
    files = [r["output_file"] for r in recs]
    assert len(set(files)) == 6 and all((tmp_path / f).exists() for f in files)
    assert all(".x" not in f for r, f in zip(recs, files, strict=True) if r["repeat"] == 0)  # unchanged names


def test_extraction_repeats_reuse_the_transcript(fake_docs, tmp_path, monkeypatch):
    ex = Echo("agent", fake_docs)
    monkeypatch.setattr(extract, "get_backend", lambda c, n: ex)
    manifest = json.loads((fake_docs / "manifest.json").read_text())
    (tmp_path / "outputs" / "ocr").mkdir(parents=True)
    with (tmp_path / "transcripts.jsonl").open("w") as fh:
        for d in manifest:
            for p in d["pages"]:
                rel = f"outputs/ocr/{d['id']}.p{p['n']}.clean.md"
                (tmp_path / rel).write_text((fake_docs / p["gt"]).read_text())
                rec = {"backend": "ocr", "doc_id": d["id"], "page": p["n"], "variant": "clean", "ok": True}
                fh.write(json.dumps({**rec, "concurrency": 1, "output_file": rel}) + "\n")
    cfg = {"bench": {"max_retries": 0, "extraction": {"agent": "agent", "concurrency": 2, "pipelines": []}}}
    meta = {"backends": {"ocr": {}}}
    pipes = ["ocr->agent", "image->agent"]
    asyncio.run(extract.run(cfg, fake_docs, tmp_path, meta=meta, pipelines=pipes, variant="clean", repeat=3))  # pyright: ignore[reportArgumentType]
    recs = _lines(tmp_path / "extractions.jsonl")
    assert len(recs) == 2 * 3 * len(manifest) and not (tmp_path / "extractions.partial.jsonl").exists()
    assert {r["repeat"] for r in recs} == {0, 1, 2} and all(r["ok"] for r in recs)
    assert len({r["output_file"] for r in recs}) == len(recs)
    assert all(r["output_file"].endswith(".clean.json") for r in recs if r["repeat"] == 0)
    text_prompts = [p for p in ex.prompts if "OCR transcript" in p]
    assert len(text_prompts) == 3 * len(manifest) and len(set(text_prompts)) == len(manifest)  # same input


def test_last_repeat_is_scored_whatever_order_it_finished_in():
    def t(run, rep, conc=1, ok=True):
        return {"run_id": run, "repeat": rep, "concurrency": conc, "ok": ok}

    cur = None
    for r in (t("a", 2), t("a", 0), t("a", 1)):
        cur = score.pick_transcript(cur, r)
    assert cur == t("a", 2)
    assert score.pick_transcript(cur, t("b", 0)) == t("b", 0)  # a later run replaces it
    assert score.pick_transcript(cur, t("a", 3, ok=False)) == cur
    assert score.pick_transcript(t("x", 0, conc=1), t("y", 0, conc=4)) == t("x", 0, conc=1)
    # older records (no repeat field): the latest at the lowest concurrency, as before
    old = [{"concurrency": 1, "ok": True, "n": i} for i in range(3)]
    cur = None
    for r in old:
        cur = score.pick_transcript(cur, r)
    assert cur == old[-1]

    m = {"d": {}}
    e = [
        {"pipeline": "p", "doc_id": "d", "variant": "clean", "ok": True, "repeat": 2, "n": 0},
        {"pipeline": "p", "doc_id": "d", "variant": "clean", "ok": True, "repeat": 1, "n": 1},
        {"pipeline": "p", "doc_id": "d", "variant": "clean", "ok": False, "repeat": 3, "n": 2},
    ]
    assert score.latest_extractions(e, m)[("p", "d", "clean")]["n"] == 0  # the last successful repeat


# --------------------------------------------------------------------------- the variation block
SCHEMAS = {
    "invoice": {
        "description": "Invoice",
        "fields": {
            "total": {"type": "string", "description": "Total"},
            "note": {"type": "string", "description": "Note", "optional": True},
        },
    }
}


def make_run(tmp_path: Path) -> tuple[dict, Path]:
    """Two one-page documents. Transcriber `b`: 3 repeats at concurrency 1 (`solo`: one run, no repeats).
    Pipeline `image->x`: 3 extraction repeats. Values chosen so every figure is easy to work out by hand."""
    run = tmp_path / "run"
    docs = run / "docs"
    manifest = []
    for doc_id, gt, nums, total in (("invoice-00", "Sum 100", ["100"], "100"), ("invoice-01", "Hei", [], "5")):
        d = docs / doc_id
        d.mkdir(parents=True)
        Image.new("RGB", (4, 4), "white").save(d / "p1.png")
        Image.new("RGB", (4, 4), "white").save(d / "p1.degraded.jpg")
        (d / "p1.gt.txt").write_text(gt)
        (d / "p1.gt.json").write_text(json.dumps({"numbers": nums}))
        (d / "fields.json").write_text(json.dumps({"type": "invoice", "fields": {"total": total}}))
        page = {"n": 1, "clean": f"{doc_id}/p1.png", "degraded": f"{doc_id}/p1.degraded.jpg"}
        page |= {"gt": f"{doc_id}/p1.gt.txt", "gt_meta": f"{doc_id}/p1.gt.json"}
        manifest.append(
            {"id": doc_id, "type": "invoice", "title": "<T>", "pages": [page], "fields": f"{doc_id}/fields.json"}
        )
    (docs / "manifest.json").write_text(json.dumps(manifest))
    (docs / "schemas.json").write_text(json.dumps(SCHEMAS))

    def out(rel: str, text: str) -> str:
        (run / rel).parent.mkdir(parents=True, exist_ok=True)
        (run / rel).write_text(text)
        return rel

    tr = []
    for rep, a, b in ((0, "Sum 100", "Hei"), (1, "Sum 10", "Hei"), (2, None, "Hei")):
        for doc_id, text in (("invoice-00", a), ("invoice-01", b)):
            rec = {"run_id": "r1", "backend": "b", "doc_id": doc_id, "page": 1, "variant": "clean"}
            rec |= {"concurrency": 1, "latency_s": 1.0, "repeat": rep, "ok": text is not None}
            rec["output_file"] = out(f"outputs/b/{doc_id}.p1.clean.r1.x{rep}.md", text) if text else None
            tr.append(rec)
    for doc_id in ("invoice-00", "invoice-01"):
        rec = {"run_id": "r2", "backend": "solo", "doc_id": doc_id, "page": 1, "variant": "clean"}
        tr.append(
            {
                **rec,
                "concurrency": 1,
                "latency_s": 1.0,
                "ok": True,
                "output_file": out(f"outputs/solo/{doc_id}.md", "Hei"),
            }
        )
    (run / "transcripts.jsonl").write_text("".join(json.dumps(r) + "\n" for r in tr))

    ex = []
    answers = {
        0: {"invoice-00": {"total": "100"}, "invoice-01": {"total": "5"}},
        1: {"invoice-00": {"total": "100", "note": "hi"}, "invoice-01": {"total": "6"}},
        2: {"invoice-00": {"total": " 100 "}, "invoice-01": None},
    }
    for rep, per_doc in answers.items():
        for doc_id, fields in per_doc.items():
            rec = {"pipeline": "image->x", "source": "image", "agent": "x", "doc_id": doc_id, "variant": "clean"}
            rec |= {
                "repeat": rep,
                "ok": fields is not None,
                "latency_s": 2.0,
                "prompt_tokens": 1,
                "completion_tokens": 1,
            }
            rel = f"outputs/extract/image__x/{doc_id}.clean{f'.x{rep}' if rep else ''}.json"
            rec["output_file"] = out(rel, json.dumps(fields)) if fields is not None else None
            ex.append(rec)
    (run / "extractions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in ex))
    meta = {
        "schema_version": runs.SCHEMA_VERSION,
        "id": "var-run",
        "plan_items": [
            {"backend": "b", "concurrency": 1, "variant": "clean", "repeat": 3},
            {"backend": "solo", "concurrency": 1, "variant": "clean"},
        ],
        "transcribers": ["b", "solo"],
        "backends": {k: {"kind": "bedrock", "postprocess": []} for k in ("b", "solo", "x")},
        "extraction": {"agent": "x", "pipelines": ["image->x"], "agents": {"image->x": "x"}, "repeat": 3},
        "documents": {
            "count": 2,
            "pages": 2,
            "page_images": 4,
            "types": {"invoice": {"docs": 2, "pages": 2, "description": "Invoice", "fields": ["total", "note"]}},
        },
        "plan": "variation",
        "runner": {"mode": "local"},
    }
    return meta, run


def test_variation_block_by_hand(tmp_path):
    meta, run = make_run(tmp_path)
    s = score.score(meta, run)
    # main tables: one repeat per page / document (the last successful one), as without repeats
    (agg,) = [a for a in s["transcription"]["aggregate"] if a["backend"] == "b"]
    assert agg["cer"] == pytest.approx((1 / 7 + 0) / 2) and agg["coverage"] == 1
    (eagg,) = s["extraction"]["aggregate"]
    assert eagg["field_accuracy"] == 0.5 and eagg["coverage"] == 1

    (t,) = s["variation"]["transcription"]  # `solo` has no repeats: not in it
    assert (t["backend"], t["variant"], t["concurrency"], t["repeats"], t["pages"]) == ("b", "clean", 1, 3, 2)
    cers = [0.0, 1 / 14, 0.5]
    assert [r["cer"] for r in t["per_repeat"]] == pytest.approx(cers)
    assert [r["coverage"] for r in t["per_repeat"]] == [1, 1, 0.5]
    assert [r["num_recall"] for r in t["per_repeat"]] == [1, 0.5, 0.5]
    assert [r["word_recall"] for r in t["per_repeat"]] == [1, 0.75, 0.5]
    assert t["cer_min"] == 0 and t["cer_max"] == 0.5 and t["cer_mean"] == pytest.approx(st.mean(cers))
    assert t["cer_std"] == pytest.approx(st.stdev(cers))  # sample std
    assert t["pages_differing"] == 0.5 and t["page_cer_range_mean"] == 0.5

    (e,) = s["variation"]["extraction"]
    assert [r["field_accuracy"] for r in e["per_repeat"]] == pytest.approx([1, 1 / 3, 0.5])
    assert [r["fields_total"] for r in e["per_repeat"]] == [2, 3, 2]  # repeat 1 invented `note`
    assert e["field_accuracy_min"] == pytest.approx(1 / 3) and e["field_accuracy_max"] == 1
    assert (
        e["answers"] == 3 and e["flip_rate"] == pytest.approx(2 / 3) and e["correct_flip_rate"] == pytest.approx(2 / 3)
    )
    assert s["method"]["variation"]["std"].startswith("sample")


def test_token_limit_pages_are_counted_per_repeat_and_in_the_facts(tmp_path):
    from ocrbench import facts

    meta, run = make_run(tmp_path)
    recs = _lines(run / "transcripts.jsonl")
    for r in recs:
        if r["backend"] == "b" and r["doc_id"] == "invoice-00" and r["repeat"] == 1:
            r["finish_reason"] = "length"  # the "Sum 10" page was cut off at the token limit
    (run / "transcripts.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    s = score.score(meta, run)
    (t,) = s["variation"]["transcription"]
    assert [r["truncated"] for r in t["per_repeat"]] == [0, 1, 0]
    # main tables: repeat 2 failed on that page, so the cut-off repeat 1 is the one scored
    assert facts.reading_facts(s["transcription"]["aggregate"])["b"]["clean"]["token_limit_pages"] == 1
    agg = [{**a, "truncated": 2} for a in s["transcription"]["aggregate"] if a["backend"] == "b"]
    assert facts.reading_facts(agg)["b"]["clean"]["token_limit_pages"] == 2


def test_no_variation_block_without_repeats(tmp_path):
    meta, run = make_run(tmp_path)
    keep = lambda r: r.get("repeat", 0) == 0  # noqa: E731
    for f in ("transcripts.jsonl", "extractions.jsonl"):
        (run / f).write_text("".join(json.dumps(r) + "\n" for r in _lines(run / f) if keep(r)))
    meta["plan_items"][0].pop("repeat")
    meta["extraction"]["repeat"] = 1
    s = score.score(meta, run)
    assert "variation" not in s and "variation" not in s["method"]


def test_killed_repeat_counts_as_failed(tmp_path):
    meta, run = make_run(tmp_path)
    meta["extraction"]["repeat"] = 4  # repeat 3 never ran
    meta["plan_items"][0]["repeat"] = 4
    s = score.score(meta, run)
    (t,) = s["variation"]["transcription"]
    (e,) = s["variation"]["extraction"]
    assert t["repeats"] == 4 and t["per_repeat"][3]["coverage"] == 0 and t["per_repeat"][3]["cer"] == 1
    assert e["repeats"] == 4 and e["per_repeat"][3]["coverage"] == 0 and e["per_repeat"][3]["field_accuracy"] == 0


def test_schema_upgrade_defaults_extraction_repeat():
    v2 = {"schema_version": 2, "id": "r", "backends": {}, "prompts": {}, "extraction": {"agent": "a", "pipelines": []}}
    m = runs.upgrade(v2)
    assert m["schema_version"] == runs.SCHEMA_VERSION == 3 and m["extraction"]["repeat"] == 1
    assert "repeat" not in v2["extraction"] and runs.upgrade(m) is m


@pytest.mark.parametrize(
    "extra, msg",
    [
        ("  extraction: {plan_repeat: {p: 0}}\n", "plan_repeat.p must be an integer >= 1"),
        ("  extraction: {plan_repeat: {p: '3'}}\n", "plan_repeat.p must be an integer >= 1"),
        ("  extraction: {plan_repeat: [3]}\n", "plan_repeat must map"),
        ("  plans: {p: [{backend: m, repeat: 0}]}\n", "repeat must be an integer >= 1"),
    ],
)
def test_repeat_config_validation(tmp_path, extra, msg):
    p = tmp_path / "c.yaml"
    p.write_text(
        "name_prefix: abc\naws: {account_id: '1', region: r}\nbackends: {m: {kind: bedrock}}\nbench:\n" + extra
    )
    with pytest.raises(ValueError, match=msg):
        config_mod.load(p)


def test_example_variation_plan_is_recorded(fake_docs, tmp_path, monkeypatch):
    cfg = config_mod.load(ROOT / "config" / "example.yaml")
    monkeypatch.setattr(runs, "RUNS", tmp_path / "runs")
    assert cfg.extraction_repeat("variation") == 3 and cfg.extraction_repeat("suite") == 1
    meta = runs.load(runs.create(cfg, "variation", fake_docs))
    assert meta["extraction"]["repeat"] == 3 and all(it["repeat"] == 3 for it in meta["plan_items"])
    assert meta["extraction"]["pipelines"] == ["image->sonnet-4.5", "image->haiku-4.5", "haiku-4.5->sonnet-4.5"]
    assert runs.load(runs.create(cfg, "smoke", fake_docs))["extraction"]["repeat"] == 1


# --------------------------------------------------------------------------- key facts + number check
def _var(*pipes: tuple[str, float, float, float]) -> dict:
    return {
        "transcription": [
            {
                "backend": b,
                "variant": "clean",
                "concurrency": 1,
                "repeats": 3,
                "cer_mean": m,
                "cer_min": lo,
                "cer_max": hi,
                "num_recall_min": 0.97,
                "num_recall_max": 0.98,
                "pages_differing": 0.5,
            }
            for b, m, lo, hi in (("rd-a", 0.041, 0.038, 0.045), ("rd-b", 0.070, 0.066, 0.072))
        ],
        "extraction": [
            {
                "pipeline": p,
                "variant": "clean",
                "repeats": 3,
                "field_accuracy_mean": m,
                "field_accuracy_min": lo,
                "field_accuracy_max": hi,
                "flip_rate": 0.02,
            }
            for p, m, lo, hi in pipes
        ],
    }


VAR = _var(("image->a", 0.97, 0.965, 0.976), ("image->b", 0.962, 0.96, 0.964), ("image->c", 0.90, 0.89, 0.91))


def test_variation_facts_and_verdicts():
    f = facts.variation_facts(VAR)
    pairs = {(x["a"], x["b"]): x for x in f["pipeline_pairs"]}
    ab = pairs[("image->a", "image->b")]
    assert ab["gap"] == pytest.approx(0.008) and ab["larger_range"] == pytest.approx(0.011) and not ab["outside"]
    assert pairs[("image->a", "image->c")]["outside"] and pairs[("image->b", "image->c")]["outside"]
    (rd,) = f["reader_pairs"]
    assert rd["gap"] == pytest.approx(0.029) and rd["outside"]
    md = "\n".join(facts._md_variation(f, {}))
    assert "| `image->a` | clean | 3 | 97.0% | 96.5%–97.6% | 1.1 pts | 2.0% |" in md
    assert (
        "| clean | `image->a` vs `image->b` | field accuracy | 0.8 pts | 1.1 pts | within the run-to-run range |" in md
    )
    assert "**outside** the run-to-run range" in md and "| `rd-a` | clean | 3 | 0.041 | 0.038–0.045 | 0.007 |" in md


def _meta_scores() -> tuple[dict, dict]:
    meta = {
        "id": "r",
        "transcribers": ["rd-a", "rd-b"],
        "backends": {
            "rd-a": {"kind": "openai_compatible", "model": "anthropic.claude-sonnet-5-5", "temperature": 1},
            "rd-b": {"kind": "openai_compatible", "model": "anthropic.claude-haiku-5-5", "temperature": 1},
            "a": {"kind": "bedrock", "model": "m-a"},
            "b": {"kind": "bedrock", "model": "m-b"},
            "c": {"kind": "bedrock", "model": "m-c"},
        },
        "extraction": {"agent": "a", "pipelines": ["image->a", "image->b", "image->c"], "repeat": 3},
        "documents": {"count": 1, "page_images": 2, "types": {"invoice": {}}},
    }
    agg = [
        {"pipeline": p, "variant": "clean", "field_accuracy": a, "end_to_end_latency_s": 2.5, "cost_per_1k_docs_usd": 1}
        for p, a in (("image->a", 0.97), ("image->b", 0.962), ("image->c", 0.9))
    ]
    scores = {
        "speed": [],
        "transcription": {"aggregate": [], "pages": []},
        "extraction": {"aggregate": agg, "docs": []},
        "variation": VAR,
    }
    return meta, scores


def test_summary_may_quote_the_variation_facts_and_nothing_else():
    meta, scores = _meta_scores()
    prompt, source = build_prompt(meta, scores)
    assert "Run-to-run variation (identical repeats" in prompt and "Run-to-run check" in prompt
    ok = (
        "`image->a` and `image->b` differ by 0.8 points, within the run-to-run range (1.1 pts); `image->a` "
        "ranged 96.5%–97.6% and `rd-a` read with CER 0.038–0.045. Each page was sent 3 times at concurrency 1."
    )
    assert unverified_numbers(ok, source) == []
    assert unverified_numbers("`image->a` varies by 2.4 points between runs", source) == ["2.4"]
    assert unverified_numbers("a spread of 0.012 in CER", source) == ["0.012"]


def test_hidden_pipelines_leave_the_variation_block():
    meta, scores = _meta_scores()
    meta["report"] = {"hide_backends": ["c", "rd-b"]}
    fm, vis = view.focus(meta, scores)
    assert [a["pipeline"] for a in vis["variation"]["extraction"]] == ["image->a", "image->b"]
    assert [a["backend"] for a in vis["variation"]["transcription"]] == ["rd-a"]
    assert "image->c" not in facts.to_md(facts.compute(fm, vis))
    meta["report"] = {"hide_backends": ["a", "b", "c", "rd-a", "rd-b"]}
    assert "variation" not in view.visible(meta, scores)  # nothing left to show: no section


# --------------------------------------------------------------------------- reports
def test_report_and_site_show_the_variation_section(tmp_path):
    meta, run = make_run(tmp_path)
    s = score.score(meta, run)
    md = reporting.results_md(s)
    assert "### Run-to-run variation" in md and "accept only temperature 1" in md and "Answers changed" in md
    assert "Field extraction, per repeat:" in md and "| image->x | clean | 2 |" in md
    assert "**pages not identical**" in md
    assert "### Run-to-run variation" not in reporting.results_md({**s, "variation": None})
    report = reporting.report_md(meta, s, run)
    assert "Run-to-run variation (identical repeats" in report  # key facts

    page = RunPage(pipeline_labels={"image->x": "page images → <X>"})
    idx = build_site(run / "docs", run, s, tmp_path / "site" / "var-run", page)
    html = idx.read_text()
    assert 'id="variation"' in html and 'href="#variation"' in html and "Right/wrong flips" in html
    assert "page images → &lt;X&gt;" in html and "<X>" not in html
    plain = build_site(run / "docs", run, {**s, "variation": None}, tmp_path / "site2", RunPage()).read_text()
    assert 'id="variation"' not in plain and 'href="#variation"' not in plain

    card = reporting.run_card(meta, s, run)
    index = build_experiments_index([card], tmp_path / "site").read_text()
    assert "run-to-run check &middot; 3 repeats" in index and "var-run" in index
