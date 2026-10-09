import asyncio
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


def test_end_to_end_local(tiny_docs, tmp_path, monkeypatch):
    from ocrbench import extract, reporting, score, transcribe

    cfg = config_mod.load(ROOT / "config" / "example.yaml")
    fake = {}
    monkeypatch.setattr(transcribe, "get_backend", lambda c, n: fake.setdefault(n, GroundTruthBackend(n, tiny_docs)))
    monkeypatch.setattr(extract, "get_backend", lambda c, n: fake.setdefault(n, GroundTruthBackend(n, tiny_docs)))
    out = tmp_path / "run"
    asyncio.run(transcribe.run(cfg, tiny_docs, out, backend="ocr", concurrency=2))
    asyncio.run(extract.run(cfg, tiny_docs, out, pipelines=["ocr->agent", "image->agent"]))
    s = score.score(cfg, tiny_docs, out, "t")
    assert s["transcription"]["aggregate"] and all(a["cer"] == 0 for a in s["transcription"]["aggregate"])
    assert {a["pipeline"] for a in s["extraction"]["aggregate"]} == {"ocr->agent", "image->agent"}
    assert all(a["field_accuracy"] == 1 for a in s["extraction"]["aggregate"])
    idx = reporting.write(s, tiny_docs, out, tmp_path / "site")
    assert idx.exists() and (out / "report.md").read_text().startswith("# ocr-bench")
