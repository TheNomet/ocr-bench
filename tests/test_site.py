import json
import re
import struct
import zlib
from pathlib import Path

from ocrbench.report import build_site
from ocrbench.report.markdown_lite import render as render_md


def _png_1x1() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\xff\xff")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


# Minimal JPEG-ish bytes (SOI + EOI markers); the viewer only copies the file.
JPG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def make_fixture(tmp_path: Path) -> tuple[Path, Path, dict]:
    docs = tmp_path / "docs-out"
    run = tmp_path / "results" / "suite"
    png = _png_1x1()

    def page(doc_id: str, n: int) -> dict:
        d = docs / doc_id
        d.mkdir(parents=True, exist_ok=True)
        (d / f"p{n}.png").write_bytes(png)
        (d / f"p{n}.degraded.jpg").write_bytes(JPG)
        _write_json(d / f"p{n}.gt.json", {"numbers": ["48213"]})
        return {
            "n": n,
            "clean": f"{doc_id}/p{n}.png",
            "degraded": f"{doc_id}/p{n}.degraded.jpg",
            "gt": f"{doc_id}/p{n}.gt.txt",
            "gt_meta": f"{doc_id}/p{n}.gt.json",
            "width": 1,
            "height": 1,
        }

    inv_pages = [page("invoice-01", 1), page("invoice-01", 2)]
    (docs / "invoice-01" / "p1.gt.txt").write_text("Faktura nr. 48213\nTotal <due> 15 937,50 & more", encoding="utf-8")
    (docs / "invoice-01" / "p2.gt.txt").write_text("Side 2\nTakk for handelen", encoding="utf-8")
    form_pages = [page("form-01", 1)]
    (docs / "form-01" / "p1.gt.txt").write_text("Skjema\nNavn: Ola", encoding="utf-8")
    _write_json(
        docs / "invoice-01" / "fields.json",
        {"type": "invoice", "fields": {"invoice_number": "48213", "total_due": "15 937,50"}},
    )
    _write_json(
        docs / "form-01" / "fields.json", {"type": "form", "fields": {"name": "Ola", "selected_options": ["A", "C"]}}
    )
    _write_json(
        docs / "manifest.json",
        [
            {
                "id": "invoice-01",
                "type": "invoice",
                "title": "Faktura nr. 48213",
                "language": "nb",
                "pages": inv_pages,
                "fields": "invoice-01/fields.json",
                "pdf": "invoice-01/doc.pdf",
            },
            {
                "id": "form-01",
                "type": "form",
                "title": "Søknad <skjema>",
                "language": "nb",
                "pages": form_pages,
                "fields": "form-01/fields.json",
                "pdf": "form-01/doc.pdf",
            },
        ],
    )
    _write_json(
        docs / "schemas.json",
        {
            "invoice": {
                "description": "Invoice",
                "fields": {
                    "invoice_number": {"type": "string", "description": "Invoice number"},
                    "total_due": {"type": "string", "description": "Total amount due"},
                    "due_date": {"type": "string", "description": "Due date"},
                },
            },
            "form": {
                "description": "Form",
                "fields": {
                    "name": {"type": "string", "description": "Applicant name"},
                    "selected_options": {"type": "list", "description": "Checked boxes"},
                },
            },
        },
    )

    # Run dir: raw outputs with HTML special chars.
    o1 = "outputs/ocr/invoice-01.p1.clean.r1.md"
    o2 = "outputs/llm/invoice-01.p1.clean.r1.md"
    o3 = "outputs/ocr/form-01.p1.degraded.r1.md"
    (run / "outputs/ocr").mkdir(parents=True)
    (run / "outputs/llm").mkdir(parents=True)
    (run / o1).write_text("# Faktura nr. 48213\n<script>alert(1)</script> Total 15 937,50", encoding="utf-8")
    (run / o2).write_text("Faktura nr. 48218 Total <due> 15 937,50 & more", encoding="utf-8")
    (run / o3).write_text("Skjema Navn: Ola", encoding="utf-8")
    ex1 = "outputs/extract/ocr__agent/invoice-01.clean.json"
    _write_json(run / ex1, {"invoice_number": "48213", "total_due": "15 937,00"})
    with (run / "transcripts.jsonl").open("w") as f:
        for backend, doc_id, n, variant, of in [
            ("ocr", "invoice-01", 1, "clean", o1),
            ("llm", "invoice-01", 1, "clean", o2),
            ("ocr", "form-01", 1, "degraded", o3),
        ]:
            f.write(
                json.dumps(
                    {
                        "run_id": "1",
                        "backend": backend,
                        "doc_id": doc_id,
                        "page": n,
                        "variant": variant,
                        "ok": True,
                        "output_file": of,
                    }
                )
                + "\n"
            )

    scores = {
        "tag": "suite",
        "generated": "2026-10-08T17:00:00",
        "summary_md": "# Summary\n\nThe **ocr** backend is `fast`.\n\n- point one\n- point <two>\n\n| a | b |\n|---|---|\n| 1 | 2 |",
        "speed": [
            {
                "backend": "ocr",
                "model": "baidu/Unlimited-OCR@sagemaker:x",
                "concurrency": 1,
                "pages": 3,
                "ok": 3,
                "errors": 0,
                "wall_s": 10.0,
                "pages_per_min": 18.0,
                "p50_s": 3.1,
                "p95_s": 6.2,
                "max_s": 7.0,
                "mean_prompt_tokens": 1192,
                "mean_completion_tokens": 490,
                "retries": 0,
                "cost_per_1k_pages_usd": 0.55,
            },
            {
                "backend": "llm",
                "model": "claude",
                "concurrency": 1,
                "pages": 3,
                "ok": 3,
                "errors": 0,
                "wall_s": 20.0,
                "pages_per_min": 9.0,
                "p50_s": 6.0,
                "p95_s": 8.0,
                "max_s": 9.0,
                "mean_prompt_tokens": 1500,
                "mean_completion_tokens": 500,
                "retries": 1,
                "cost_per_1k_pages_usd": None,
            },
        ],
        "transcription": {
            "aggregate": [
                {
                    "backend": "ocr",
                    "variant": "clean",
                    "pages": 1,
                    "cer": 0.03,
                    "wer": 0.06,
                    "word_recall": 0.95,
                    "word_precision": 0.96,
                    "num_recall": 1.0,
                    "num_missing": 0,
                    "num_hallucinated": 0,
                },
                {
                    "backend": "llm",
                    "variant": "clean",
                    "pages": 1,
                    "cer": 0.05,
                    "wer": 0.1,
                    "word_recall": 0.9,
                    "word_precision": 0.9,
                    "num_recall": 0.5,
                    "num_missing": 1,
                    "num_hallucinated": 1,
                },
            ],
            "pages": [
                {
                    "backend": "ocr",
                    "doc_id": "invoice-01",
                    "page": 1,
                    "variant": "clean",
                    "cer": 0.01,
                    "wer": 0.02,
                    "word_recall": 0.99,
                    "word_precision": 0.98,
                    "num_recall": 1.0,
                    "num_missing": 0,
                    "num_hallucinated": 0,
                    "hallucinated_examples": [],
                    "missing_examples": [],
                    "output_file": o1,
                    "clean_text": "Faktura nr. 48213 <script>alert(1)</script> Total 15 937,50",
                },
                {
                    "backend": "llm",
                    "doc_id": "invoice-01",
                    "page": 1,
                    "variant": "clean",
                    "cer": 0.05,
                    "wer": 0.1,
                    "word_recall": 0.9,
                    "word_precision": 0.9,
                    "num_recall": 0.5,
                    "num_missing": 1,
                    "num_hallucinated": 1,
                    "hallucinated_examples": ["48218"],
                    "missing_examples": ["48213"],
                    "output_file": o2,
                    "clean_text": "Faktura nr. 48218 Total <due> 15 937,50 & more",
                },
            ],
        },
        "extraction": {
            "aggregate": [
                {
                    "pipeline": "ocr->agent",
                    "variant": "clean",
                    "docs": 1,
                    "field_accuracy": 0.5,
                    "fields_total": 2,
                    "fields_correct": 1,
                    "mean_latency_s": 4.1,
                    "end_to_end_latency_s": 7.3,
                    "cost_per_1k_docs_usd": None,
                }
            ],
            "docs": [
                {
                    "pipeline": "ocr->agent",
                    "doc_id": "invoice-01",
                    "variant": "clean",
                    "accuracy": 0.5,
                    "fields": {
                        "invoice_number": {"truth": "48213", "predicted": "48213", "correct": True},
                        "total_due": {"truth": "15 937,50", "predicted": "15 937,00", "correct": False},
                    },
                    "output_file": ex1,
                }
            ],
        },
    }
    return docs, run, scores


def test_build_site(tmp_path: Path) -> None:
    docs, run, scores = make_fixture(tmp_path)
    out = tmp_path / "site"
    index = build_site(docs, run, scores, out)

    assert index == out / "index.html"
    assert index.is_file()
    inv = out / "docs" / "invoice-01.html"
    form = out / "docs" / "form-01.html"
    assert inv.is_file() and form.is_file()

    # Images and static assets copied.
    for rel in [
        "invoice-01/p1.png",
        "invoice-01/p1.degraded.jpg",
        "invoice-01/p2.png",
        "form-01/p1.png",
        "form-01/p1.degraded.jpg",
        "style.css",
        "app.js",
    ]:
        assert (out / "assets" / rel).is_file(), rel
    assert (out / "assets/invoice-01/p1.png").read_bytes() == (docs / "invoice-01/p1.png").read_bytes()

    idx_html = index.read_text(encoding="utf-8")
    inv_html = inv.read_text(encoding="utf-8")
    form_html = form.read_text(encoding="utf-8")

    # Key content.
    assert "ocr-bench — suite" in idx_html
    for s in [
        "invoice-01",
        "form-01",
        "Speed",
        "Transcription quality",
        "Field extraction",
        "baidu/Unlimited-OCR@sagemaker:x",
        "ocr-&gt;agent",
        "<strong>ocr</strong>",
        "<code>fast</code>",
        "<li>point &lt;two&gt;</li>",
        "<th>a</th>",
        "50.0%",
        "0.030",
        "3.10",
        'href="docs/invoice-01.html"',
        'src="assets/invoice-01/p1.png"',
        "Søknad &lt;skjema&gt;",
    ]:
        assert s in idx_html, s
    assert "–" in idx_html  # null cost
    assert "<script>alert" not in idx_html

    assert "ocr" in inv_html and "llm" in inv_html
    assert "✓" in inv_html and "✗" in inv_html
    assert "15 937,00" in inv_html
    assert "Total amount due" in inv_html  # schema description tooltip
    assert 'href="../index.html"' in inv_html and 'href="form-01.html"' in inv_html
    assert 'href="invoice-01.html"' in form_html
    assert 'src="../assets/invoice-01/p1.degraded.jpg"' in inv_html
    assert "Page 2" in inv_html
    # Diff markup: llm's 48218 vs truth 48213.
    assert "<del>48213</del>" in inv_html and "<ins>48218</ins>" in inv_html
    # Unscored transcript still offered (raw output fallback).
    assert "Skjema Navn: Ola" in form_html

    # HTML special chars in transcripts/GT are escaped, never injected.
    for page in (inv_html, form_html, idx_html):
        assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in inv_html
    assert "Total &lt;due&gt; 15 937,50 &amp; more" in inv_html

    # Every href/src is relative (no absolute paths, no external URLs).
    for page in (idx_html, inv_html, form_html):
        for url in re.findall(r'(?:href|src)="([^"]*)"', page):
            assert not url.startswith(("/", "http:", "https:", "file:")), url
            assert str(tmp_path) not in url


def test_empty_scores(tmp_path: Path) -> None:
    docs, run, _ = make_fixture(tmp_path)
    scores = {
        "tag": "empty",
        "generated": "",
        "summary_md": "",
        "speed": [],
        "transcription": {"aggregate": [], "pages": []},
        "extraction": {"aggregate": [], "docs": []},
    }
    index = build_site(docs, run, scores, tmp_path / "site")
    html = index.read_text(encoding="utf-8")
    assert "No speed data." in html and "No extraction scores." in html
    assert (tmp_path / "site/docs/invoice-01.html").is_file()


def test_markdown_lite_escapes() -> None:
    out = render_md("## T <x>\n\nhello **b** `<c>` & d")
    assert "<h3>T &lt;x&gt;</h3>" in out
    assert "<strong>b</strong>" in out and "<code>&lt;c&gt;</code>" in out and "&amp; d" in out
    assert render_md("") == ""
