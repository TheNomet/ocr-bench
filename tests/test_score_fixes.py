"""Regression tests for scoring choices: unknown is not zero, every selected page counts, per-page latency."""

import json
import shutil

import pytest

from ocrbench.score import cost_basis, normalise, page_metrics, score, score_extraction, source_latency


def _write(d, name, rows):
    (d / name).write_text("".join(json.dumps(r) + "\n" for r in rows))


def _t(backend, doc, page, variant, conc, lat, ok=True, run_id="a", out=None, pt=None, ct=None):
    return {
        "run_id": run_id,
        "backend": backend,
        "model": "m",
        "doc_id": doc,
        "page": page,
        "variant": variant,
        "concurrency": conc,
        "started": 0,
        "latency_s": lat,
        "ok": ok,
        "retries": 0,
        "prompt_tokens": pt,
        "completion_tokens": ct,
        "finish_reason": "stop" if ok else None,
        "output_file": out if ok else None,
    }


def _ext(pipe, source, agent, doc, variant, ok=True, out=None, lat=2.0, pt=None, ct=None):
    return {
        "pipeline": pipe,
        "source": source,
        "agent": agent,
        "doc_id": doc,
        "variant": variant,
        "ok": ok,
        "error": None if ok else "boom",
        "output_file": out if ok else None,
        "latency_s": lat,
        "prompt_tokens": pt,
        "completion_tokens": ct,
    }


@pytest.fixture
def run(fake_docs, tmp_path):
    d = tmp_path / "run"
    shutil.copytree(fake_docs, d / "docs")
    (d / "outputs").mkdir()
    (d / "outputs" / "x.md").write_text("text")
    (d / "outputs" / "f.json").write_text("{}")
    for name in ("transcripts.jsonl", "runs.jsonl", "extractions.jsonl"):
        (d / name).write_text("")
    manifest = {m["id"]: m for m in json.loads((d / "docs" / "manifest.json").read_text())}
    return d, manifest


def _meta(**backends):
    return {"schema_version": 2, "id": "r", "backends": backends}


LLM = {"kind": "bedrock", "price_per_mtok": {"input": 3.0, "output": 15.0}, "postprocess": []}
GPU = {"kind": "selfhosted", "price_per_hour_usd": 1.2, "postprocess": []}


# ---- 1. end-to-end latency: per page, lowest concurrency available for that page, unknown if missing
def test_source_latency_per_page_fallback_and_latest(run):
    _, manifest = run
    doc = "rental_contract-00"  # two pages
    ts = [
        _t("o", doc, 1, "clean", 1, 5.0),
        _t("o", doc, 1, "clean", 1, 7.0, run_id="b"),  # re-run at the same concurrency: the latest counts
        _t("o", doc, 1, "clean", 8, 1.0, run_id="c"),
        _t("o", doc, 2, "clean", 8, 3.0, run_id="c"),  # page 2 only ran at concurrency 8: still counted
        _t("o", doc, 1, "degraded", 1, 4.0),
        _t("o", doc, 2, "degraded", 1, 9.0, ok=False),  # page 2 never succeeded
    ]
    lat = source_latency(ts, manifest)
    assert lat[("o", doc, "clean")] == pytest.approx(10.0)
    assert lat[("o", doc, "degraded")] is None


def test_end_to_end_unknown_not_undercounted(run):
    d, _ = run
    doc = "rental_contract-00"
    _write(d, "transcripts.jsonl", [_t("o", doc, 1, "clean", 1, 5.0, out="outputs/x.md")])
    _write(
        d,
        "extractions.jsonl",
        [_ext("o->agent", "o", "a", doc, "clean", out="outputs/f.json", lat=2.0, pt=100, ct=10)],
    )
    s = score(_meta(o=GPU, a=LLM), d)
    (ex,) = s["extraction"]["aggregate"]
    assert ex["mean_latency_s"] == 2.0 and ex["end_to_end_latency_s"] is None  # not 2.0 + 5.0 (page 2 missing)


# ---- 2. unknown cost is not $0
def test_failed_or_unmetered_runs_have_no_cost(run):
    d, _ = run
    doc = "invoice-00"
    _write(
        d,
        "transcripts.jsonl",
        [
            _t("l", doc, 1, "clean", 1, 1.0, run_id="ok", out="outputs/x.md", pt=1000, ct=100),
            _t("l", doc, 1, "clean", 4, 1.0, run_id="fail", ok=False),
            _t("n", doc, 1, "clean", 1, 1.0, run_id="nousage", out="outputs/x.md"),
        ],
    )
    _write(
        d,
        "runs.jsonl",
        [
            {"run_id": "ok", "backend": "l", "model": "m", "concurrency": 1, "wall_s": 1},
            {"run_id": "fail", "backend": "l", "model": "m", "concurrency": 4, "wall_s": 1},
            {"run_id": "nousage", "backend": "n", "model": "m", "concurrency": 1, "wall_s": 1},
        ],
    )
    _write(d, "extractions.jsonl", [_ext("image->agent", "image", "a", doc, "clean", out="outputs/f.json")])
    s = score(_meta(l=LLM, n=LLM, a=LLM), d)
    speed = {(r["backend"], r["concurrency"]): r for r in s["speed"]}
    assert speed[("l", 4)]["cost_per_1k_pages_usd"] is None and speed[("l", 4)]["mean_prompt_tokens"] is None
    basis = s["method"]["cost_basis"]
    assert basis["l"]["cost_per_1k_pages_usd"] == pytest.approx((1000 * 3 + 100 * 15) / 1e6 * 1000)  # not halved
    assert basis["n"]["cost_per_1k_pages_usd"] is None and basis["n"]["basis"].startswith("unknown")
    (ex,) = s["extraction"]["aggregate"]
    assert ex["cost_per_1k_docs_usd"] is None  # extraction without usage: unknown, not $0


# ---- 4. pages a plan item selected but never attempted count as failed
def test_unattempted_pages_count_as_failed(run):
    d, manifest = run
    doc = "invoice-00"
    _write(d, "transcripts.jsonl", [_t("o", doc, 1, "clean", 1, 1.0, out="outputs/x.md")])
    meta = {**_meta(o=GPU), "plan_items": [{"backend": "o", "concurrency": 1, "variant": "clean"}]}
    s = score(meta, d)
    (agg,) = s["transcription"]["aggregate"]
    n_pages = sum(len(m["pages"]) for m in manifest.values())
    assert agg["pages"] == n_pages and agg["coverage"] == pytest.approx(1 / n_pages)
    assert "plan item" in s["method"]["expected_pages"]
    # a limit is respected, and old run.json without plan items keeps the attempted pages only
    meta["plan_items"] = [{"backend": "o", "variant": "clean", "limit": 2}]
    assert score(meta, d)["transcription"]["aggregate"][0]["pages"] == 2
    del meta["plan_items"]
    assert score(meta, d)["transcription"]["aggregate"][0]["pages"] == 1


# ---- 5. HTML entities
def test_html_entities_are_decoded():
    assert normalise("<td>Smith &amp; Co&nbsp;AS</td> &quot;x&quot;") == 'Smith & Co AS "x"'
    assert page_metrics("Smith & Co", [], "<p>Smith &amp; Co</p>")["cer"] == 0


# ---- 6. empty ground truth, no numbers, capped CER
def test_empty_ground_truth_and_cap():
    assert page_metrics("", [], "")["cer"] == 0
    m = page_metrics("", [], "lots of invented text")
    assert m["cer"] == 1.0 and m["wer"] == 1.0
    assert page_metrics("Hei der", [], "Hei der")["num_recall"] == 1.0  # nothing to miss
    assert page_metrics("ab", ["12"], "ab " * 50)["cer"] == 1.0  # runaway page: no worse than a failed one


# ---- 7. no run within the error threshold
def test_cost_basis_flags_when_nothing_is_sustained():
    rows = [
        {"backend": "o", "concurrency": 4, "pages": 100, "errors": 5, "pages_per_min": 20, "cost_per_1k_pages_usd": 1},
        {
            "backend": "o",
            "concurrency": 32,
            "pages": 100,
            "errors": 40,
            "pages_per_min": 60,
            "cost_per_1k_pages_usd": 0.3,
        },
    ]
    b = cost_basis(rows, {"o"})["o"]
    assert b["concurrency"] == 4 and b["meets_error_threshold"] is False and "no run stayed within" in b["basis"]
    rows[0]["errors"] = 0
    b = cost_basis(rows, {"o"})["o"]
    assert b["concurrency"] == 4 and b["meets_error_threshold"] is True and "best sustained" in b["basis"]


# ---- 8. a value invented for a field the document doesn't have counts as wrong
def test_invented_optional_field_counts_as_wrong(tmp_path):
    def scored(pred):
        (tmp_path / "f.json").write_text(json.dumps(pred, ensure_ascii=False))
        rec = _ext("image->agent", "image", "a", "letter-02", "clean", out="f.json")
        return score_extraction(rec, {"sender_name": "Bjørn", "amounts": ["150,00"]}, tmp_path, SCHEMA)

    SCHEMA = ["sender_name", "date", "recipient", "amounts", "tags"]
    base = {"sender_name": "Bjørn", "amounts": ["150,00"]}
    s = scored({**base, "recipient": "Kjære naboer"})
    assert (s["fields_total"], s["fields_correct"]) == (3, 2)
    assert s["fields"]["recipient"] == {"truth": None, "predicted": "Kjære naboer", "correct": False, "invented": True}
    s = scored({**base, "tags": ["x"]})  # an invented list counts too
    assert (s["fields_total"], s["fields_correct"]) == (3, 2)
    # omitted, null, blank or empty list: nothing invented, the denominator is unchanged
    for pred in (base, {**base, "recipient": None, "date": " ", "tags": []}, {**base, "tags": [""]}):
        s = scored(pred)
        assert (s["fields_total"], s["fields_correct"]) == (2, 2) and set(s["fields"]) == set(base)
    # without a schema (old runs): ground-truth fields only
    rec = _ext("image->agent", "image", "a", "letter-02", "clean", out="f.json")
    (tmp_path / "f.json").write_text(json.dumps({**base, "recipient": "x"}))
    assert score_extraction(rec, base, tmp_path)["fields_total"] == 2


def test_invented_field_in_score(run):
    d, manifest = run
    doc = "invoice-00"
    fields_file = d / "docs" / manifest[doc]["fields"]
    gt = json.loads(fields_file.read_text())
    kid = gt["fields"].pop("kid")  # the document has no KID: the schema still lists it
    fields_file.write_text(json.dumps(gt))
    (d / "outputs" / "f.json").write_text(json.dumps({**gt["fields"], "kid": kid}))
    _write(d, "extractions.jsonl", [_ext("image->agent", "image", "a", doc, "clean", out="outputs/f.json")])
    s = score(_meta(a=LLM), d)
    (ex,) = s["extraction"]["aggregate"]
    n = len(gt["fields"])
    assert (ex["fields_total"], ex["fields_correct"]) == (n + 1, n)
    assert s["method"]["invented_optional_fields"].startswith("count as wrong")


# ---- 9. documents a pipeline's source was expected to cover but never extracted count as failed
def test_unattempted_extractions_count_as_failed(run):
    d, manifest = run
    doc = "invoice-00"
    _write(d, "transcripts.jsonl", [_t("o", doc, 1, "clean", 1, 1.0, out="outputs/x.md")])
    (d / "outputs" / "f.json").write_text(
        json.dumps(json.loads((d / "docs" / manifest[doc]["fields"]).read_text())["fields"])
    )
    _write(
        d,
        "extractions.jsonl",
        [
            _ext("image->agent", "image", "a", doc, "clean", out="outputs/f.json"),
            _ext("o->agent", "o", "a", doc, "clean", out="outputs/f.json"),
        ],
    )
    meta = {
        **_meta(o=GPU, p=GPU, a=LLM),
        "plan_items": [{"backend": "o", "variant": "clean", "limit": 2}, {"backend": "p", "variant": "both"}],
        "extraction": {"agent": "a", "pipelines": ["o->agent", "image->agent", "p->agent"]},
    }
    s = score(meta, d)
    agg = {(a["pipeline"], a["variant"]): a for a in s["extraction"]["aggregate"]}
    o_docs = {r["doc_id"] for r in s["transcription"]["pages"] if r["backend"] == "o"}
    assert agg[("o->agent", "clean")]["docs"] == len(o_docs) == 2  # o's selection: its first 2 pages
    assert agg[("o->agent", "clean")]["coverage"] == pytest.approx(1 / 2)
    n = len(manifest)  # image: every selected document (p selected all of them, both variants)
    assert agg[("image->agent", "clean")]["docs"] == agg[("image->agent", "degraded")]["docs"] == n
    assert (
        agg[("image->agent", "degraded")]["coverage"] == 0 and agg[("image->agent", "degraded")]["field_accuracy"] == 0
    )
    assert agg[("p->agent", "clean")]["coverage"] == 0  # planned pipeline whose pass never wrote a record
    missing = next(x for x in s["extraction"]["docs"] if x["pipeline"] == "p->agent")
    assert missing["agent"] == "a" and missing["source"] == "p" and missing["fields_correct"] == 0
    assert missing["fields_total"] > 0
    assert "never attempted" in s["method"]["expected_documents"]
    # old run.json without plan items, or no extraction at all: attempted documents only
    del meta["plan_items"]
    s = score(meta, d)
    assert {a["docs"] for a in s["extraction"]["aggregate"]} == {1}
    assert "record" in s["method"]["expected_documents"]
    meta["plan_items"] = [{"backend": "o", "variant": "clean"}]
    _write(d, "extractions.jsonl", [])
    assert score(meta, d)["extraction"]["aggregate"] == []
