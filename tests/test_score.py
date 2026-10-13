import json

from ocrbench.score import cost_basis, field_correct, norm_value, number_found, page_metrics, score


def test_numbers_match_whole_tokens():
    assert number_found("1 250,00", "Sum 1 250,00 kr")
    assert not number_found("12", "konto 1234")
    assert not number_found("250,00", "1 250,001")
    assert number_found("455 46 959", "Telefon: 455 46 959")
    assert not number_found("455 46 959", "455 46 958")


def test_invented_numbers_not_hidden_by_neighbours():
    m = page_metrics("Konto 123 45 678", ["123 45 678"], "Konto 123 45 678 og 4567")
    assert m["num_hallucinated"] == 1 and m["hallucinated_examples"] == ["4567"]


def test_currency_normalisation():
    assert norm_value("kr. 1 234,50") == norm_value("1 234,50") == norm_value("1 234,50 kr")
    assert norm_value("NOK 500") == "500"
    assert norm_value("500,-") == "500"
    assert norm_value("Kristian") == "kristian"  # a name starting with "kr" is left alone
    assert field_correct("15 937,50", "kr 15 937,50")
    assert not field_correct("15 937,50", "15 937,60")


def _write(d, name, rows):
    (d / name).write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_failures_count_and_cost_basis(fake_docs, tmp_path):
    import shutil

    d = tmp_path / "run"
    shutil.copytree(fake_docs, d / "docs")
    manifest = json.loads((d / "docs" / "manifest.json").read_text())
    doc = manifest[0]
    gt = (d / "docs" / doc["pages"][0]["gt"]).read_text()
    (d / "outputs").mkdir()
    (d / "outputs" / "good.md").write_text(gt)
    base = {
        "backend": "ocr",
        "model": "m",
        "doc_id": doc["id"],
        "page": 1,
        "started": 0,
        "retries": 0,
        "prompt_tokens": None,
        "completion_tokens": None,
        "finish_reason": "stop",
    }
    _write(
        d,
        "transcripts.jsonl",
        [
            {
                **base,
                "run_id": "a",
                "variant": "clean",
                "concurrency": 1,
                "latency_s": 6,
                "ok": True,
                "output_file": "outputs/good.md",
            },
            {
                **base,
                "run_id": "a",
                "variant": "degraded",
                "concurrency": 1,
                "latency_s": 6,
                "ok": False,
                "output_file": None,
            },
            {
                **base,
                "run_id": "b",
                "variant": "clean",
                "concurrency": 16,
                "latency_s": 30,
                "ok": True,
                "output_file": "outputs/good.md",
            },
        ],
    )
    _write(
        d,
        "runs.jsonl",
        [
            {"run_id": "a", "backend": "ocr", "model": "m", "concurrency": 1, "wall_s": 60},
            {"run_id": "b", "backend": "ocr", "model": "m", "concurrency": 16, "wall_s": 6},
        ],
    )
    _write(
        d,
        "extractions.jsonl",
        [
            {
                "pipeline": "ocr->agent",
                "source": "ocr",
                "agent": "a",
                "doc_id": doc["id"],
                "variant": "degraded",
                "ok": False,
                "error": "missing transcript",
                "output_file": None,
                "latency_s": 0,
            }
        ],
    )
    # a v1 run.json (before `backends`): score() upgrades it
    meta = {"id": "r", "ocr": {"price_per_hour_usd": 1.2}, "llm_backends": {"a": {"price_per_mtok": None}}}
    s = score(meta, d)
    agg = {(a["backend"], a["variant"]): a for a in s["transcription"]["aggregate"]}
    assert agg[("ocr", "degraded")]["coverage"] == 0 and agg[("ocr", "degraded")]["cer"] == 1.0
    assert agg[("ocr", "clean")]["cer"] == 0
    (ex,) = s["extraction"]["aggregate"]
    assert ex["coverage"] == 0 and ex["field_accuracy"] == 0
    b = s["method"]["cost_basis"]["ocr"]
    assert b["concurrency"] == 16 and b["cost_at_concurrency_1"] > b["cost_per_1k_pages_usd"]


def test_cost_basis_skips_error_prone_runs():
    rows = [
        {
            "backend": "ocr",
            "concurrency": 4,
            "pages": 100,
            "errors": 0,
            "pages_per_min": 20,
            "cost_per_1k_pages_usd": 1.0,
        },
        {
            "backend": "ocr",
            "concurrency": 32,
            "pages": 100,
            "errors": 10,
            "pages_per_min": 30,
            "cost_per_1k_pages_usd": 0.7,
        },
    ]
    assert cost_basis(rows, {"ocr"})["ocr"]["concurrency"] == 4
