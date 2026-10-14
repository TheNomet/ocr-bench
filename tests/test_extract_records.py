"""Stage 2 records: written as they finish, a crash keeps the previous pass, bad inputs are failures."""

from __future__ import annotations

import asyncio
import json

import pytest

from ocrbench import extract
from ocrbench.backends.base import Response

CFG = {"bench": {"max_retries": 0, "extraction": {"agent": "agent", "concurrency": 2, "pipelines": []}}}


class Fields:
    """Extractor that answers with the document's true fields."""

    def __init__(self, name, docs):
        self.name, self.model, self.docs = name, f"m:{name}", docs

    async def warmup(self):
        pass

    async def complete(self, req):
        doc_id = req.prompt.split("Document type: ")[1].split(" ")[0]
        f = next(self.docs.glob(f"{doc_id}-*/fields.json"))
        return Response(json.dumps(json.loads(f.read_text())["fields"]), 10, 5)


def _run(docs, run_dir, pipelines, meta=None):
    meta = meta or {"backends": {}}
    asyncio.run(extract.run(CFG, docs, run_dir, meta=meta, pipelines=pipelines, variant="clean"))  # pyright: ignore[reportArgumentType]


def _lines(p):
    return [json.loads(x) for x in p.read_text().splitlines()]


def test_rerun_rotates_only_after_the_new_pass(fake_docs, tmp_path, monkeypatch):
    monkeypatch.setattr(extract, "get_backend", lambda c, n: Fields(n, fake_docs))
    _run(fake_docs, tmp_path, ["image->agent"])
    first = _lines(tmp_path / "extractions.jsonl")
    assert first and all(r["ok"] for r in first) and not (tmp_path / "extractions.partial.jsonl").exists()

    calls = []

    def get(c, n):  # the second pipeline's extractor can't be created: the stage aborts
        calls.append(n)
        if n == "broken":
            raise RuntimeError("no such backend")
        return Fields(n, fake_docs)

    monkeypatch.setattr(extract, "get_backend", get)
    with pytest.raises(RuntimeError):
        _run(fake_docs, tmp_path, ["image->agent", "image->broken"])
    assert _lines(tmp_path / "extractions.jsonl") == first  # the previous complete pass is intact
    assert not (tmp_path / "extractions.prev.jsonl").exists()
    partial = _lines(tmp_path / "extractions.partial.jsonl")  # finished work was written as it completed
    assert len(partial) == len(first) and {r["pipeline"] for r in partial} == {"image->agent"}

    monkeypatch.setattr(extract, "get_backend", lambda c, n: Fields(n, fake_docs))
    _run(fake_docs, tmp_path, ["image->agent", "image->other"])
    assert _lines(tmp_path / "extractions.prev.jsonl") == first
    assert {r["pipeline"] for r in _lines(tmp_path / "extractions.jsonl")} == {"image->agent", "image->other"}
    assert not (tmp_path / "extractions.partial.jsonl").exists()


def test_missing_transcript_file_is_a_failed_extraction(fake_docs, tmp_path, monkeypatch):
    monkeypatch.setattr(extract, "get_backend", lambda c, n: Fields(n, fake_docs))
    manifest = json.loads((fake_docs / "manifest.json").read_text())
    (tmp_path / "outputs" / "ocr").mkdir(parents=True)
    with (tmp_path / "transcripts.jsonl").open("w") as fh:
        for d in manifest:
            for p in d["pages"]:
                rel = f"outputs/ocr/{d['id']}.p{p['n']}.clean.md"
                if d is not manifest[0]:  # the first document's files are "lost"
                    (tmp_path / rel).write_text((fake_docs / p["gt"]).read_text())
                rec = {"backend": "ocr", "doc_id": d["id"], "page": p["n"], "variant": "clean"}
                fh.write(json.dumps({**rec, "ok": True, "concurrency": 1, "output_file": rel}) + "\n")
    _run(fake_docs, tmp_path, ["ocr->agent"], meta={"backends": {"ocr": {}}})
    recs = {r["doc_id"]: r for r in _lines(tmp_path / "extractions.jsonl")}
    assert len(recs) == len(manifest)
    bad = recs[manifest[0]["id"]]
    assert not bad["ok"] and "building the request" in bad["error"] and bad["latency_s"] == 0.0
    assert all(r["ok"] for k, r in recs.items() if k != manifest[0]["id"])
