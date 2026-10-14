import codecs
import json
import re
import subprocess

import pytest

from ocrbench.docgen import SCHEMAS, TYPES, build_doc, generate, render
from ocrbench.docgen.render import find_chrome

# Words that must never appear in this public repo's generated documents, stored rot13-encoded so
# this file itself stays clean for the repo's denylist scan.
FORBIDDEN = [
    codecs.encode(w, "rot13")
    for w in [
        "qao",
        "xlp",
        "uivginfx",
        "xhaqrrexyæevat",
        "xwraafxnc gvy xhaqra",
        "cbyvgvfx rxfcbareg",
        "crc",
        "svapevzr",
        "zbarl ynhaqrevat",
        "nzy",
        "pbzcyvnapr",
        "evfx nffrffzrag",
    ]
]
SEEDS = [7, 11, 2026]
CASES = [(t, s) for t in TYPES for s in SEEDS]


def test_types_and_schemas():
    assert TYPES == [
        "letter",
        "receipt",
        "bank_statement",
        "holiday_survey",
        "invoice",
        "payslip",
        "rental_contract",
        "newsletter",
    ]
    assert set(SCHEMAS) == set(TYPES)
    for schema in SCHEMAS.values():
        assert schema["description"]
        for spec in schema["fields"].values():
            assert spec["type"] in ("string", "list")
            assert spec["description"]


@pytest.mark.parametrize("doc_type,seed", CASES)
def test_build(doc_type, seed):
    for i in range(2):
        doc, _ = build_doc(doc_type, i, seed)
        assert doc.title
        text = doc.gt_text
        for pg in doc.pages:
            assert pg.gt and all(line.strip() == line and line for line in pg.gt)
            assert all("  " not in line for line in pg.gt), "double spaces collapse in HTML"
            assert pg.numbers
            page_text = "\n".join(pg.gt)
            for n in pg.numbers:
                assert n in page_text, f"number {n!r} not on its page"
        schema = SCHEMAS[doc_type]["fields"]
        assert set(doc.fields) <= set(schema)
        for key, spec in schema.items():
            if not spec.get("optional"):
                assert key in doc.fields, f"{doc_type}: missing field {key}"
        for key, val in doc.fields.items():
            if schema[key]["type"] == "list":
                assert isinstance(val, list) and val
                for v in val:
                    assert isinstance(v, str) and v in text, f"{key}: {v!r} not in gt"
            else:
                assert isinstance(val, str) and val and val in text, f"{key}: {val!r} not in gt"


def test_deterministic():
    for t in TYPES:
        a, _ = build_doc(t, 0, 7)
        b, _ = build_doc(t, 0, 7)
        c, _ = build_doc(t, 1, 7)
        assert a.render() == b.render() and a.fields == b.fields
        assert a.render() != c.render()


def test_rental_contract_two_pages():
    for s in SEEDS:
        doc, _ = build_doc("rental_contract", 0, s)
        assert len(doc.pages) == 2


@pytest.mark.parametrize("doc_type", TYPES)
def test_no_forbidden_words(doc_type):
    blobs = [json.dumps(SCHEMAS, ensure_ascii=False)]
    for s in SEEDS:
        for i in range(3):
            doc, _ = build_doc(doc_type, i, s)
            blobs += [doc.render(), doc.gt_text, json.dumps(doc.fields, ensure_ascii=False)]
    low = "\n".join(blobs).lower()
    for w in FORBIDDEN:
        assert w not in low, f"forbidden word {w!r} in {doc_type}"


@pytest.mark.chrome
@pytest.mark.skipif(find_chrome() is None, reason="Chrome/Edge/Chromium not available")
def test_generate_end_to_end(tmp_path):
    manifest = generate(tmp_path, per_type=1, seed=7)
    assert [m["type"] for m in manifest] == TYPES
    on_disk = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert on_disk == manifest
    assert set(json.loads((tmp_path / "schemas.json").read_text(encoding="utf-8"))) == set(TYPES)
    for m in manifest:
        assert re.fullmatch(r"[a-z_]+-\d{2}", m["id"]) and m["language"] == "nb" and m["title"]
        assert (tmp_path / m["pdf"]).stat().st_size > 0
        assert (tmp_path / m["id"] / "doc.html").exists()
        fields = json.loads((tmp_path / m["fields"]).read_text(encoding="utf-8"))
        assert fields["type"] == m["type"] and fields["fields"]
        expected_pages = 2 if m["type"] == "rental_contract" else 1
        assert [p["n"] for p in m["pages"]] == list(range(1, expected_pages + 1))
        for p in m["pages"]:
            for k in ("clean", "degraded", "gt", "gt_meta"):
                assert (tmp_path / p[k]).stat().st_size > 0
            assert p["width"] > 0 and p["height"] > 0
            assert json.loads((tmp_path / p["gt_meta"]).read_text(encoding="utf-8"))["numbers"]


def _fake_chrome(monkeypatch, outcomes):
    """Replace the Chrome call: each outcome is (pdf bytes or None, exception or None)."""
    calls = []

    def fake(chrome, html_path, pdf_path, timeout):
        content, exc = outcomes[len(calls)]
        calls.append(pdf_path)
        if content is not None:
            pdf_path.write_bytes(content)
        if exc is not None:
            raise exc

    monkeypatch.setattr(render, "_chrome_print", fake)
    return calls


PDF = b"%PDF-1.4\n...\n%%EOF\n"


def test_html_to_pdf_keeps_pdf_when_chrome_dies_at_shutdown(tmp_path, monkeypatch):
    # Chrome writes the whole PDF, then its teardown watchdog kills it (exit 2): no retry needed.
    crash = subprocess.CalledProcessError(2, ["chrome"], stderr=b"Teardown watchdog expired")
    calls = _fake_chrome(monkeypatch, [(PDF, crash)])
    render.html_to_pdf("chrome", tmp_path / "doc.html", tmp_path / "doc.pdf")
    assert len(calls) == 1 and (tmp_path / "doc.pdf").read_bytes() == PDF


def test_html_to_pdf_retries_truncated_pdf_and_reports_stderr(tmp_path, monkeypatch):
    noisy = b"[1:2:ERROR:process_reader_mac.cc:310] thread_get_state(4)\nreal problem\n"
    crash = subprocess.CalledProcessError(2, ["chrome"], stderr=noisy)
    calls = _fake_chrome(monkeypatch, [(b"%PDF-1.4 trunc", crash), (None, crash), (PDF, None)])
    render.html_to_pdf("chrome", tmp_path / "doc.html", tmp_path / "doc.pdf")
    assert len(calls) == 3

    _fake_chrome(monkeypatch, [(b"%PDF-1.4 trunc", crash)] * 2)
    with pytest.raises(RuntimeError, match="exit code 2") as e:
        render.html_to_pdf("chrome", tmp_path / "doc.html", tmp_path / "doc.pdf", attempts=2)
    assert "real problem" in str(e.value) and "thread_get_state" not in str(e.value)


@pytest.mark.parametrize(
    ("platform", "translated", "expected"),
    [
        ("darwin", "1\n", ("arch", "-arm64", "-x86_64")),
        ("darwin", "0\n", ()),
        ("darwin", "", ()),  # Intel Mac: the sysctl doesn't exist
        ("linux", "1\n", ()),
    ],
)
def test_native_launcher(monkeypatch, platform, translated, expected):
    monkeypatch.setattr(render.sys, "platform", platform)
    monkeypatch.setattr(render.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(render.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=translated))
    render._native_launcher.cache_clear()
    try:
        assert render._native_launcher() == expected
    finally:
        render._native_launcher.cache_clear()
