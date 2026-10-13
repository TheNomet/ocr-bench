"""Shared fixtures. `fake_docs` builds a real document set (ground truth, fields, schemas) without
Chrome: page images are placeholder PNGs, which is all the pipeline tests need."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from ocrbench.docgen import SCHEMAS, build_doc


def make_docs(out: Path, types=("invoice", "holiday_survey", "rental_contract"), per_type: int = 1) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for t in types:
        for i in range(per_type):
            doc, _ = build_doc(t, i)
            doc_id = f"{t}-{i:02d}"
            d = out / doc_id
            d.mkdir(parents=True, exist_ok=True)
            pages = []
            for n, pg in enumerate(doc.pages, start=1):
                Image.new("RGB", (40, 56), "white").save(d / f"p{n}.png")
                Image.new("RGB", (30, 42), "#eee").save(d / f"p{n}.degraded.jpg")
                (d / f"p{n}.gt.txt").write_text("\n".join(pg.gt) + "\n", encoding="utf-8")
                (d / f"p{n}.gt.json").write_text(json.dumps({"numbers": pg.numbers}), encoding="utf-8")
                pages.append(
                    {
                        "n": n,
                        "clean": f"{doc_id}/p{n}.png",
                        "degraded": f"{doc_id}/p{n}.degraded.jpg",
                        "gt": f"{doc_id}/p{n}.gt.txt",
                        "gt_meta": f"{doc_id}/p{n}.gt.json",
                        "width": 40,
                        "height": 56,
                    }
                )
            (d / "fields.json").write_text(
                json.dumps({"type": t, "fields": doc.fields}, ensure_ascii=False), encoding="utf-8"
            )
            manifest.append(
                {
                    "id": doc_id,
                    "type": t,
                    "title": doc.title,
                    "language": "nb",
                    "pages": pages,
                    "fields": f"{doc_id}/fields.json",
                    "pdf": f"{doc_id}/doc.pdf",
                }
            )
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (out / "schemas.json").write_text(json.dumps({t: SCHEMAS[t] for t in types}, ensure_ascii=False), encoding="utf-8")
    return out


@pytest.fixture(scope="module")
def fake_docs(tmp_path_factory) -> Path:
    return make_docs(tmp_path_factory.mktemp("docs"))


def pytest_configure(config):
    config.addinivalue_line("markers", "chrome: renders documents with headless Chrome (slow, needs a working Chrome)")
