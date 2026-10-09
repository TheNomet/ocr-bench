import json
from pathlib import Path

import pytest

from ocrbench import config as config_mod
from ocrbench.backends import Response
from ocrbench.backends.openai_compat import _der_to_raw
from ocrbench.backends.vllm_ocr import strip_grounding
from ocrbench.extract import parse_json
from ocrbench.score import field_correct, normalise, page_metrics

ROOT = Path(__file__).resolve().parents[1]


def test_example_config_loads(monkeypatch):
    monkeypatch.delenv("AWS_REGION", raising=False)
    cfg = config_mod.load(ROOT / "config" / "example.yaml")
    assert cfg.bucket == f"{cfg.prefix}-artifacts"
    assert cfg.endpoint_name.endswith("-ocr")
    assert cfg.plan("smoke")
    for t in cfg["bench"]["transcribers"]:
        assert t == "ocr" or t in cfg["llm_backends"]


def test_env_expansion(tmp_path, monkeypatch):
    p = tmp_path / "c.yaml"
    p.write_text("name_prefix: abc-${X:-dflt}\naws: {account_id: '1', region: '${R}'}\nocr: {}\nbench: {}\n")
    monkeypatch.setenv("R", "eu-north-1")
    cfg = config_mod.load(p)
    assert cfg.prefix == "abc-dflt" and cfg.region == "eu-north-1"
    monkeypatch.delenv("R")
    with pytest.raises(KeyError):
        config_mod.load(p)


def test_strip_grounding():
    raw = "<|det|>text [1, 2, 3, 4]<|/det|>Hei <|ref|>verden<|/ref|>"
    assert strip_grounding(raw) == "Hei verden"


def test_normalise_and_metrics():
    assert normalise("| a | b |\n|---|---|\n**c**") == "a b c"
    m = page_metrics("Sum 1 250,00 kr", ["1 250,00"], "Sum 1 250,00 kr")
    assert m["cer"] == 0 and m["num_recall"] == 1 and m["num_hallucinated"] == 0
    m = page_metrics("Sum 1 250,00 kr", ["1 250,00"], "Sum 1 260,00 kr")
    assert m["num_recall"] == 0 and m["num_hallucinated"] >= 1


def test_field_correct():
    assert field_correct("15 937,50", "kr 15 937,50")
    assert field_correct(["Bading", "Spa"], ["spa", "Bading"])
    assert not field_correct(["Bading"], ["Bading", "Spa"])
    assert not field_correct("12.07.2026", None)


def test_parse_json():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Here you go: {"a": "x"} thanks') == {"a": "x"}
    assert parse_json('{"a": 1}\n\nNote: {"b": 2}') == {"a": 1}


def test_der_to_raw():
    r, s = (5).to_bytes(1, "big"), (0x80 | 1).to_bytes(1, "big")
    der = bytes([0x30, 2 + len(r) + 2 + len(s) + 1, 0x02, 1]) + r + bytes([0x02, 2, 0]) + s
    raw = _der_to_raw(der)
    assert len(raw) == 64 and raw[31] == 5 and raw[63] == 0x81


class GroundTruthBackend:
    """Fake transcriber/agent: returns the ground truth (transcribe) or fields (extract)."""

    def __init__(self, name, docs):
        self.name, self.model, self.docs = name, f"fake:{name}", docs
        self.instance_type = None

    async def warmup(self):
        pass

    async def complete(self, req):
        if req.images:
            img = req.images[0]
            doc_dir = img.parent
            if "extract these fields" in req.prompt:
                return Response(json.dumps(json.loads((doc_dir / "fields.json").read_text())["fields"]), 10, 5)
            n = img.name.split(".")[0]
            return Response((doc_dir / f"{n}.gt.txt").read_text(), 100, 50, "stop")
        doc_id = req.prompt.split("Document type: ")[1].split(" ")[0]
        f = next(self.docs.glob(f"{doc_id}-*/fields.json"))
        return Response(json.dumps(json.loads(f.read_text())["fields"]), 10, 5)


@pytest.fixture(scope="module")
def tiny_docs(tmp_path_factory):
    from ocrbench.docgen.render import find_chrome

    if not find_chrome():
        pytest.skip("Chrome not available")
    from ocrbench.docgen import generate

    d = tmp_path_factory.mktemp("docs")
    generate(d, per_type=1, types=["invoice", "holiday_survey"])
    return d


def test_end_to_end_run_folder(tiny_docs, tmp_path, monkeypatch):
    """A full run in runs/<id>/: transcribe -> extract -> score -> summarize, then report + experiments index."""
    from ocrbench import cli, extract, runs, summarize, transcribe

    cfg = config_mod.load(ROOT / "config" / "example.yaml")
    cfg.raw["runner"]["mode"] = "local"
    cfg.raw["bench"]["plans"]["t"] = [{"backend": "ocr", "concurrency": 2}]
    cfg.raw["bench"]["extraction"]["pipelines"] = ["ocr->agent", "image->agent"]
    monkeypatch.setattr(runs, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    fake = {}

    def get(c, n):
        return fake.setdefault(n, GroundTruthBackend(n, tiny_docs))

    monkeypatch.setattr(transcribe, "get_backend", get)
    monkeypatch.setattr(extract, "get_backend", get)

    class Writer(GroundTruthBackend):
        async def complete(self, req):
            assert "## Bottom line" in req.prompt and "# Key facts" in req.prompt and "cheapest pipeline" in req.prompt
            return Response("## Bottom line\nUse image->agent, 1234 is made up.\n\n## Caveats\nx", 900, 40)

    monkeypatch.setattr(summarize, "get_backend", lambda c, n: Writer(n, tiny_docs))
    monkeypatch.setattr(runs, "record_endpoint", lambda c, d: None)

    class A:
        plan, id, stages, docs = "t", None, "all", str(tiny_docs)

    cli.cmd_run(cfg, A())
    (d,) = (tmp_path / "runs").iterdir()
    meta = runs.load(d)
    assert set(meta["stages"]) == {"transcribe", "extract", "score", "summarize"}
    assert (d / "docs" / "manifest.json").exists() and (d / "config.yaml").exists()
    s = json.loads((d / "scores.json").read_text())
    assert all(a["field_accuracy"] == 1 for a in s["extraction"]["aggregate"])
    report = (d / "report.md").read_text()
    for needle in (
        "## Findings",
        "LLM-written",
        "## Setup",
        "### GPU",
        "### Pipelines (the arrows)",
        "### Document types",
        "`image->agent`",
        "holiday_survey",
        "## Results",
    ):
        assert needle in report, needle
    index = (tmp_path / "site" / "index.html").read_text()
    assert meta["id"] in index and "Use image-&gt;agent" in index and "pipeline</th>" in index
    assert "extraction agent" in index and "Reads the document type" in index
    run_page = (tmp_path / "site" / meta["id"] / "index.html").read_text()
    assert "all experiments" in run_page and 'id="legend"' in run_page

    # resume: only re-summarize
    class B:
        plan, id, stages, docs = None, meta["id"], "summarize", str(tiny_docs)

    cli.cmd_run(cfg, B())
    assert "summarize" in runs.load(d)["stages"]


def test_unverified_numbers():
    from ocrbench.summarize import unverified_numbers

    src = "accuracy 98.8% at $7.44/1k docs, ~307 pages/hour, 1 686 pages"
    assert unverified_numbers("98.8% and 99% and $7.44 and 307 and 1,686", src) == []
    assert unverified_numbers("costs 3x more, 280 pages/hour", src) == ["280"]


def test_temperature_override():
    from ocrbench.backends.base import Backend, Request

    b = Backend()
    assert b.temperature(Request("s", "p")) == 0.0
    b._init_temperature({"temperature": 1})
    assert b.temperature(Request("s", "p")) == 1
    b2 = Backend()
    b2._init_temperature({"temperature": None})
    assert b2.temperature(Request("s", "p")) is None


def test_named_extractor_pipeline(tiny_docs, tmp_path, monkeypatch):
    import asyncio

    from ocrbench import extract

    cfg = config_mod.load(ROOT / "config" / "example.yaml")
    used = []

    def get(c, n):
        used.append(n)
        return GroundTruthBackend(n, tiny_docs)

    monkeypatch.setattr(extract, "get_backend", get)
    asyncio.run(extract.run(cfg, tiny_docs, tmp_path, pipelines=["image->agent", "image->bedrock-haiku"]))
    recs = [json.loads(x) for x in (tmp_path / "extractions.jsonl").read_text().splitlines()]
    assert {r["agent"] for r in recs} == {cfg["bench"]["extraction"]["agent"], "bedrock-haiku"}
    assert sorted(set(used)) == sorted({cfg["bench"]["extraction"]["agent"], "bedrock-haiku"})
