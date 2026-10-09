"""Synthetic Norwegian (bokmål) document generator with exact ground truth.

    from ocrbench.docgen import generate
    manifest = generate(Path("docs-out"), per_type=6, seed=7)

See docs/data-formats.md (section 1) for the output layout.
"""

from __future__ import annotations

import json
import random
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .builder import Doc
from .types import MODULES

TYPES: list[str] = list(MODULES)
SCHEMAS: dict[str, dict] = {k: m.SCHEMA for k, m in MODULES.items()}

__all__ = ["TYPES", "SCHEMAS", "Doc", "build_doc", "generate"]


def build_doc(doc_type: str, index: int, seed: int = 7) -> tuple[Doc, random.Random]:
    """Build one document deterministically. Returns the Doc and its Random (used later for degrading)."""
    if doc_type not in MODULES:
        raise ValueError(f"unknown document type {doc_type!r}; expected one of {TYPES}")
    r = random.Random(f"{seed}-{doc_type}-{index}")
    doc = MODULES[doc_type].build(r)
    if not doc.title:
        doc.title = doc.pages[0].gt[0]
    return doc, r


def _dump(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_doc(out_dir: Path, doc_type: str, index: int, seed: int, scale: float, chrome: str) -> dict:
    from .render import degrade, html_to_pdf, pdf_to_images

    doc, r = build_doc(doc_type, index, seed)
    doc_id = f"{doc_type}-{index:02d}"
    d = out_dir / doc_id
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    (d / "doc.html").write_text(doc.render(), encoding="utf-8")
    html_to_pdf(chrome, d / "doc.html", d / "doc.pdf")
    images = pdf_to_images(d / "doc.pdf", scale)
    if len(images) != len(doc.pages):
        raise RuntimeError(
            f"{doc_id}: PDF has {len(images)} pages but the document defines {len(doc.pages)}; "
            "content overflowed a page (shorten it or adjust the CSS)"
        )
    pages = []
    for n, (img, pg) in enumerate(zip(images, doc.pages, strict=True), start=1):
        img.save(d / f"p{n}.png", optimize=True)
        bad, quality = degrade(img, r)
        bad.save(d / f"p{n}.degraded.jpg", quality=quality)
        (d / f"p{n}.gt.txt").write_text("\n".join(pg.gt) + "\n", encoding="utf-8")
        _dump(d / f"p{n}.gt.json", {"numbers": pg.numbers})
        pages.append(
            {
                "n": n,
                "clean": f"{doc_id}/p{n}.png",
                "degraded": f"{doc_id}/p{n}.degraded.jpg",
                "gt": f"{doc_id}/p{n}.gt.txt",
                "gt_meta": f"{doc_id}/p{n}.gt.json",
                "width": img.width,
                "height": img.height,
            }
        )
    _dump(d / "fields.json", {"type": doc_type, "fields": doc.fields})
    return {
        "id": doc_id,
        "type": doc_type,
        "title": doc.title,
        "language": "nb",
        "pages": pages,
        "fields": f"{doc_id}/fields.json",
        "pdf": f"{doc_id}/doc.pdf",
    }


def generate(
    out_dir: Path,
    per_type: int = 6,
    seed: int = 7,
    scale: float = 2.0,
    types: list[str] | None = None,
    workers: int = 4,
) -> list[dict]:
    """Render `per_type` documents of each type into `out_dir`; writes and returns the manifest."""
    from .render import find_chrome

    chosen = list(types) if types else TYPES
    unknown = [t for t in chosen if t not in MODULES]
    if unknown:
        raise ValueError(f"unknown document type(s) {unknown}; expected some of {TYPES}")
    chrome = find_chrome()
    if not chrome:
        raise RuntimeError("No Chrome / Edge / Chromium found for HTML -> PDF rendering")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs = [(t, i) for t in chosen for i in range(per_type)]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        manifest = list(pool.map(lambda job: _write_doc(out_dir, job[0], job[1], seed, scale, chrome), jobs))
    _dump(out_dir / "manifest.json", manifest)
    _dump(out_dir / "schemas.json", {t: SCHEMAS[t] for t in chosen})
    return manifest
