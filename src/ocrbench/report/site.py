"""Static, self-contained HTML viewer for benchmark results.

``build_site(docs_dir, run_dir, scores, out_dir)`` writes::

    out_dir/
      index.html              # summary, aggregate tables, document grid
      docs/<doc_id>.html      # per-document page: images, transcripts, diffs, fields
      assets/style.css
      assets/app.js
      assets/<doc_id>/...     # copied page images (same relative layout as docs_dir)

All links are relative so the folder can be zipped and opened via ``file://``.
Only the standard library is used.
"""

from __future__ import annotations

import contextlib
import difflib
import html
import json
import shutil
import statistics
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

from . import markdown_lite
from .assets import CSS, JS

DASH = "–"
VARIANTS = ("clean", "degraded")


# --------------------------------------------------------------------------- formatting


def e(value: Any) -> str:
    """HTML-escape any value (``None`` → empty string)."""
    return html.escape("" if value is None else str(value), quote=True)


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fmt(v: Any, kind: str) -> str:
    """Format a metric value. Kinds: pct, sec, rate, int, f1, usd, text."""
    if kind == "text":
        return DASH if v is None or v == "" else str(v)
    n = _num(v)
    if n is None:
        return DASH
    if kind == "pct":
        return f"{n * 100:.1f}%"
    if kind == "sec":
        return f"{n:.2f}"
    if kind == "rate":
        return f"{n:.3f}"
    if kind == "int":
        return f"{n:,.0f}".replace(",", " ")
    if kind == "f1":
        return f"{n:.1f}"
    if kind == "usd":
        return f"${n:,.2f}"
    return str(v)


def _doc_href(doc_id: str) -> str:
    return quote(f"{doc_id}.html")


# --------------------------------------------------------------------------- tables


@dataclass(frozen=True)
class Col:
    key: str
    label: str
    kind: str = "text"
    better: str | None = None  # "low" | "high" | None
    title: str = ""


SPEED_COLS = [
    Col("backend", "Backend"),
    Col("model", "Model"),
    Col("concurrency", "Conc.", "int"),
    Col("pages", "Pages", "int"),
    Col("ok", "OK", "int", "high"),
    Col("errors", "Errors", "int", "low"),
    Col("wall_s", "Wall (s)", "sec", "low"),
    Col("pages_per_min", "Pages/min", "f1", "high"),
    Col("p50_s", "p50 (s)", "sec", "low"),
    Col("p95_s", "p95 (s)", "sec", "low"),
    Col("max_s", "max (s)", "sec", "low"),
    Col("mean_prompt_tokens", "Prompt tok", "int", None, "Mean prompt tokens per page"),
    Col("mean_completion_tokens", "Compl. tok", "int", None, "Mean completion tokens per page"),
    Col("retries", "Retries", "int", "low"),
    Col("cost_per_1k_pages_usd", "$/1k pages", "usd", "low"),
]

TRANSCRIPTION_COLS = [
    Col("backend", "Backend"),
    Col("variant", "Variant"),
    Col("pages", "Pages", "int"),
    Col("cer", "CER", "rate", "low", "Character error rate"),
    Col("wer", "WER", "rate", "low", "Word error rate"),
    Col("word_recall", "Word recall", "pct", "high"),
    Col("word_precision", "Word precision", "pct", "high"),
    Col("num_recall", "Number recall", "pct", "high", "Share of ground-truth numbers found"),
    Col("num_missing", "Numbers missing", "int", "low"),
    Col("num_hallucinated", "Numbers hallucinated", "int", "low"),
]

EXTRACTION_COLS = [
    Col("pipeline", "Pipeline"),
    Col("variant", "Variant"),
    Col("docs", "Docs", "int"),
    Col("field_accuracy", "Field accuracy", "pct", "high"),
    Col("fields_correct", "Correct", "int"),
    Col("fields_total", "Total", "int"),
    Col("mean_latency_s", "Agent latency (s)", "sec", "low", "Mean extraction-agent latency per document"),
    Col("end_to_end_latency_s", "End-to-end (s)", "sec", "low", "Transcription + extraction latency per document"),
    Col("cost_per_1k_docs_usd", "$/1k docs", "usd", "low"),
]


def _best_cells(rows: list[dict], cols: list[Col], group_key: str | None) -> set[tuple[int, str]]:
    """(row index, column key) pairs holding the best value of their column within their group."""
    groups: dict[Any, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        groups[r.get(group_key) if group_key else None].append(i)
    best: set[tuple[int, str]] = set()
    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        for c in cols:
            if not c.better:
                continue
            vals = [(i, _num(rows[i].get(c.key))) for i in idxs]
            vals = [(i, v) for i, v in vals if v is not None]
            if len(vals) < 2:
                continue
            target = min(v for _, v in vals) if c.better == "low" else max(v for _, v in vals)
            if all(v == target for _, v in vals):
                continue  # no differentiation → no highlight
            best.update((i, c.key) for i, v in vals if v == target)
    return best


def render_table(rows: list[dict], cols: list[Col], group_key: str | None = None, empty: str = "No data.") -> str:
    if not rows:
        return f'<p class="empty">{e(empty)}</p>'
    best = _best_cells(rows, cols, group_key)
    head = "".join(f'<th title="{e(c.title)}">{e(c.label)}</th>' if c.title else f"<th>{e(c.label)}</th>" for c in cols)
    body = []
    for i, r in enumerate(rows):
        cells = []
        for c in cols:
            v = r.get(c.key)
            classes = []
            if c.kind != "text":
                classes.append("num")
                n = _num(v)
                data_v = "" if n is None else repr(n)
            else:
                data_v = None
            if (i, c.key) in best:
                classes.append("best")
            cls = f' class="{" ".join(classes)}"' if classes else ""
            dv = f' data-v="{e(data_v)}"' if data_v is not None else ""
            cells.append(f"<td{cls}{dv}>{e(fmt(v, c.kind))}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    note = ""
    if any(c.better for c in cols) and best:
        label = {"variant": "variant", "concurrency": "concurrency level"}.get(group_key or "", "group")
        note = f'<p class="muted small">Highlighted: best value per column within each {e(label)}. Click a header to sort.</p>'
    elif rows:
        note = '<p class="muted small">Click a header to sort.</p>'
    return (
        f'<div class="table-wrap"><table class="sortable"><thead><tr>{head}</tr></thead>'
        f"<tbody>{''.join(body)}</tbody></table></div>{note}"
    )


# --------------------------------------------------------------------------- IO helpers


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _safe_rel(rel: str | None) -> PurePosixPath | None:
    """A relative path without '..' or absolute components, or None."""
    if not rel:
        return None
    p = PurePosixPath(str(rel).replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts:
        return None
    return p


def _read_under(base: Path, rel: str | None) -> str | None:
    p = _safe_rel(rel)
    return None if p is None else _read_text(base / p)


# --------------------------------------------------------------------------- diff


def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def word_diff(truth: str, hypothesis: str) -> str:
    """HTML word diff of *hypothesis* against *truth* (deletions = missing from hypothesis)."""
    t = _normalize(truth)
    h = _normalize(hypothesis)
    # If the scored text was case-folded, fold the ground truth too so the diff is meaningful.
    if h == h.lower() and t != t.lower():
        t = t.lower()
    a, b = t.split(), h.split()
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    out: list[str] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            out.append(e(" ".join(a[i1:i2])))
        else:
            if i2 > i1:
                out.append(f"<del>{e(' '.join(a[i1:i2]))}</del>")
            if j2 > j1:
                out.append(f"<ins>{e(' '.join(b[j1:j2]))}</ins>")
    return " ".join(out)


# --------------------------------------------------------------------------- page shell


def _page(title: str, body: str, prefix: str) -> str:
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{e(title)}</title>"
        f'<link rel="stylesheet" href="{prefix}assets/style.css">'
        f"</head><body><main>\n{body}\n</main>"
        f'<script src="{prefix}assets/app.js"></script></body></html>\n'
    )


# --------------------------------------------------------------------------- site builder


class _Site:
    def __init__(self, docs_dir: Path, run_dir: Path, scores: dict, out_dir: Path) -> None:
        self.docs_dir = Path(docs_dir)
        self.run_dir = Path(run_dir)
        self.scores = scores or {}
        self.out_dir = Path(out_dir)

        manifest = _read_json(self.docs_dir / "manifest.json", [])
        self.manifest: list[dict] = [d for d in manifest if isinstance(d, dict) and d.get("id")]
        self.schemas: dict = _read_json(self.docs_dir / "schemas.json", {}) or {}
        self.tag = str(self.scores.get("tag") or "")

        tr = self.scores.get("transcription") or {}
        ex = self.scores.get("extraction") or {}
        self.speed: list[dict] = list(self.scores.get("speed") or [])
        self.tr_agg: list[dict] = list(tr.get("aggregate") or []) if isinstance(tr, dict) else []
        self.tr_pages: list[dict] = list(tr.get("pages") or []) if isinstance(tr, dict) else []
        self.ex_agg: list[dict] = list(ex.get("aggregate") or []) if isinstance(ex, dict) else []
        self.ex_docs: list[dict] = list(ex.get("docs") or []) if isinstance(ex, dict) else []

        # (doc_id, page, variant) -> {backend: scored page entry}
        self.page_scores: dict[tuple[str, int, str], dict[str, dict]] = defaultdict(dict)
        for p in self.tr_pages:
            key = (str(p.get("doc_id")), int(p.get("page") or 0), str(p.get("variant")))
            self.page_scores[key].setdefault(str(p.get("backend")), p)

        # Unscored transcripts (fallback so raw output is still viewable).
        self.page_raw: dict[tuple[str, int, str], dict[str, dict]] = defaultdict(dict)
        for t in _read_jsonl(self.run_dir / "transcripts.jsonl"):
            if not t.get("ok") or not t.get("output_file"):
                continue
            key = (str(t.get("doc_id")), int(t.get("page") or 0), str(t.get("variant")))
            self.page_raw[key].setdefault(str(t.get("backend")), t)

        self.backend_order = self._order(
            [str(s.get("backend")) for s in self.speed]
            + [str(a.get("backend")) for a in self.tr_agg]
            + [str(p.get("backend")) for p in self.tr_pages]
            + sorted({b for d in self.page_raw.values() for b in d})
        )

        self.doc_ex: dict[str, list[dict]] = defaultdict(list)
        for d in self.ex_docs:
            self.doc_ex[str(d.get("doc_id"))].append(d)

    @staticmethod
    def _order(items: list[str]) -> list[str]:
        seen: dict[str, None] = {}
        for it in items:
            if it and it != "None":
                seen.setdefault(it, None)
        return list(seen)

    # ------------------------------------------------------------------ assets

    def copy_assets(self) -> None:
        assets = self.out_dir / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        (assets / "style.css").write_text(CSS.lstrip(), encoding="utf-8")
        (assets / "app.js").write_text(JS.lstrip(), encoding="utf-8")
        for doc in self.manifest:
            for page in doc.get("pages") or []:
                for v in VARIANTS:
                    rel = _safe_rel(page.get(v))
                    if rel is None:
                        continue
                    src = self.docs_dir / rel
                    if not src.is_file():
                        continue
                    dst = assets / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)

    def asset_href(self, rel: str | None, prefix: str) -> str | None:
        p = _safe_rel(rel)
        if p is None or not (self.out_dir / "assets" / p).is_file():
            return None
        return prefix + "assets/" + "/".join(quote(part) for part in p.parts)

    # ------------------------------------------------------------------ index

    def doc_cer_by_backend(self, doc_id: str) -> dict[str, float]:
        vals: dict[str, list[float]] = defaultdict(list)
        for p in self.tr_pages:
            if str(p.get("doc_id")) == doc_id:
                n = _num(p.get("cer"))
                if n is not None:
                    vals[str(p.get("backend"))].append(n)
        return {b: statistics.fmean(vals[b]) for b in self.backend_order if vals.get(b)}

    def doc_acc_by_pipeline(self, doc_id: str) -> dict[str, float]:
        vals: dict[str, list[float]] = defaultdict(list)
        for d in self.doc_ex.get(doc_id, []):
            n = _num(d.get("accuracy"))
            if n is not None:
                vals[str(d.get("pipeline"))].append(n)
        return {k: statistics.fmean(v) for k, v in sorted(vals.items())}

    def render_index(self) -> str:
        parts = [f"<h1>ocr-bench — {e(self.tag)}</h1>"]
        gen = self.scores.get("generated")
        if gen:
            parts.append(f'<p class="muted">Generated {e(gen)}</p>')
        summary = markdown_lite.render(str(self.scores.get("summary_md") or ""))
        if summary:
            parts.append(f'<div class="summary">{summary}</div>')

        parts.append("<h2>Speed</h2>")
        parts.append(render_table(self.speed, SPEED_COLS, "concurrency", "No speed data."))
        parts.append("<h2>Transcription quality</h2>")
        parts.append(render_table(self.tr_agg, TRANSCRIPTION_COLS, "variant", "No transcription scores."))
        parts.append("<h2>Field extraction</h2>")
        parts.append(render_table(self.ex_agg, EXTRACTION_COLS, "variant", "No extraction scores."))

        parts.append(f"<h2>Documents ({len(self.manifest)})</h2>")
        if not self.manifest:
            parts.append('<p class="empty">No documents in manifest.</p>')
            return _page(f"ocr-bench — {self.tag}", "\n".join(parts), "")

        types = self._order([str(d.get("type") or "") for d in self.manifest])
        buttons = ['<button class="type-filter active" data-type="">All</button>']
        for t in types:
            count = sum(1 for d in self.manifest if str(d.get("type") or "") == t)
            buttons.append(f'<button class="type-filter" data-type="{e(t)}">{e(t)} ({count})</button>')
        parts.append(
            '<div class="toolbar">'
            + "".join(buttons)
            + '<input type="search" id="doc-search" placeholder="Search id, title, type…" aria-label="Search documents">'
            + "</div>"
        )

        cards = []
        for doc in self.manifest:
            doc_id = str(doc["id"])
            pages = doc.get("pages") or []
            first = pages[0] if pages else {}
            thumb = self.asset_href(first.get("clean"), "")
            thumb_html = (
                f'<img src="{e(thumb)}" alt="{e(doc_id)} page 1" loading="lazy">'
                if thumb
                else '<span class="muted small" style="padding:1rem">no image</span>'
            )
            badges = [f'<span class="badge type">{e(doc.get("type"))}</span>']
            for b, cer in self.doc_cer_by_backend(doc_id).items():
                badges.append(
                    f'<span class="badge" title="Mean CER over pages and variants">{e(b)} CER {e(fmt(cer, "rate"))}</span>'
                )
            for p, acc in self.doc_acc_by_pipeline(doc_id).items():
                badges.append(
                    f'<span class="badge ext" title="Mean field accuracy over variants">{e(p)} {e(fmt(acc, "pct"))}</span>'
                )
            search = " ".join(
                str(x) for x in (doc_id, doc.get("type"), doc.get("title"), doc.get("language")) if x
            ).lower()
            n_pages = len(pages)
            cards.append(
                f'<a class="card" href="docs/{_doc_href(doc_id)}" data-type="{e(doc.get("type") or "")}" data-search="{e(search)}">'
                f'<div class="thumb">{thumb_html}</div>'
                f'<div class="body"><div class="mono">{e(doc_id)}</div>'
                f'<div class="title">{e(doc.get("title") or "")}</div>'
                f'<div class="muted">{n_pages} page{"s" if n_pages != 1 else ""}</div>'
                f'<div class="badges">{"".join(badges)}</div></div></a>'
            )
        parts.append(f'<div class="grid" id="doc-grid">{"".join(cards)}</div>')
        return _page(f"ocr-bench — {self.tag}", "\n".join(parts), "")

    # ------------------------------------------------------------------ doc page

    def render_metrics(self, s: dict) -> str:
        def examples(key: str) -> str:
            ex = s.get(key) or []
            if not ex:
                return ""
            return " (" + ", ".join(f"<code>{e(x)}</code>" for x in ex[:10]) + ("…" if len(ex) > 10 else "") + ")"

        items = [
            f"<span><b>CER</b> {e(fmt(s.get('cer'), 'rate'))}</span>",
            f"<span><b>WER</b> {e(fmt(s.get('wer'), 'rate'))}</span>",
            f"<span><b>Word recall</b> {e(fmt(s.get('word_recall'), 'pct'))}</span>",
            f"<span><b>Word precision</b> {e(fmt(s.get('word_precision'), 'pct'))}</span>",
            f"<span><b>Number recall</b> {e(fmt(s.get('num_recall'), 'pct'))}</span>",
        ]
        line2 = [
            f"<div><b>Missing numbers</b> {e(fmt(s.get('num_missing'), 'int'))}{examples('missing_examples')}</div>",
            f"<div><b>Hallucinated numbers</b> {e(fmt(s.get('num_hallucinated'), 'int'))}{examples('hallucinated_examples')}</div>",
        ]
        return f'<div class="metrics"><div>{"".join(items)}</div>{"".join(line2)}</div>'

    def render_backend_panel(
        self, backend: str, scored: dict | None, raw_entry: dict | None, gt_text: str | None
    ) -> str:
        out = []
        output_file = (scored or {}).get("output_file") or (raw_entry or {}).get("output_file")
        if scored is not None:
            out.append(self.render_metrics(scored))
            clean_text = scored.get("clean_text")
            if clean_text is not None and gt_text is not None:
                out.append("<h3>Word diff vs ground truth</h3>")
                out.append(
                    '<p class="muted small"><del>missing</del> = in ground truth only, '
                    "<ins>extra</ins> = in output only (normalized text as scored).</p>"
                )
                out.append(f'<div class="diff mono">{word_diff(gt_text, str(clean_text))}</div>')
            elif clean_text is not None:
                out.append(f"<h3>Scored text</h3><pre>{e(clean_text)}</pre>")
        else:
            out.append('<p class="muted small">Not scored — raw output only.</p>')
        raw = _read_under(self.run_dir, output_file)
        label = e(output_file or "")
        if raw is None:
            out.append(f'<p class="muted small">Raw output file not found: <code>{label}</code></p>')
        else:
            out.append(f"<details><summary>Raw output <code>{label}</code></summary><pre>{e(raw)}</pre></details>")
        return "".join(out)

    def render_page_section(self, doc: dict, page: dict, prefix: str) -> str:
        doc_id = str(doc["id"])
        n = int(page.get("n") or 0)
        gt_text = _read_under(self.docs_dir, page.get("gt"))
        available = [v for v in VARIANTS if page.get(v)] or ["clean"]
        sec_id = f"p{n}"

        toggles = "".join(
            f'<button class="vbtn{" active" if i == 0 else ""}" data-variant="{v}">{v}</button>'
            for i, v in enumerate(available)
        )
        imgs = []
        for i, v in enumerate(available):
            href = self.asset_href(page.get(v), prefix)
            hidden = "" if i == 0 else " hidden"
            if href:
                imgs.append(
                    f'<div class="vpane" data-variant="{v}"{hidden}><a href="{e(href)}" target="_blank" rel="noopener">'
                    f'<img src="{e(href)}" alt="{e(doc_id)} page {n} {v}" loading="lazy"></a></div>'
                )
            else:
                imgs.append(f'<div class="vpane muted" data-variant="{v}"{hidden}>Image not available.</div>')

        panes = []
        for i, v in enumerate(available):
            key = (doc_id, n, v)
            scored = self.page_scores.get(key, {})
            raw = self.page_raw.get(key, {})
            backends = [b for b in self.backend_order if b in scored or b in raw]
            labels = ["Ground truth"] + backends
            tab_buttons = "".join(
                f'<button class="{"active" if j == 0 else ""}">{e(lbl)}'
                + (
                    f' <span class="small">({e(fmt(scored[lbl].get("cer"), "rate"))})</span>'
                    if j and lbl in scored
                    else ""
                )
                + "</button>"
                for j, lbl in enumerate(labels)
            )
            panels = [
                '<div class="tabpanel">'
                + (
                    f'<pre class="mono">{e(gt_text)}</pre>'
                    if gt_text is not None
                    else '<p class="empty">Ground truth not found.</p>'
                )
                + "</div>"
            ]
            for b in backends:
                panels.append(
                    f'<div class="tabpanel" hidden>{self.render_backend_panel(b, scored.get(b), raw.get(b), gt_text)}</div>'
                )
            hidden = "" if i == 0 else " hidden"
            panes.append(
                f'<div class="vpane" data-variant="{v}"{hidden}><div class="tabset">'
                f'<div class="tabs">{tab_buttons}</div>{"".join(panels)}</div>'
                + ("" if backends else f'<p class="muted small">No transcripts for this page ({e(v)}).</p>')
                + "</div>"
            )

        return (
            f'<section class="page" id="{sec_id}"><h2>Page {n}</h2>'
            f'<div class="page-grid"><div class="img-col"><div class="sticky">'
            f'<div class="toolbar">{toggles}<span class="muted small">click image for full size</span></div>'
            f"{''.join(imgs)}</div></div>"
            f'<div class="text-col">{"".join(panes)}</div></div></section>'
        )

    @staticmethod
    def _field_value(v: Any) -> str:
        if v is None:
            return DASH
        if isinstance(v, list):
            return ", ".join(str(x) for x in v) if v else "[]"
        if isinstance(v, dict):
            return json.dumps(v, ensure_ascii=False)
        return str(v)

    def render_fields(self, doc: dict) -> str:
        doc_id = str(doc["id"])
        doc_type = str(doc.get("type") or "")
        schema_fields: dict = (
            ((self.schemas.get(doc_type) or {}).get("fields") or {}) if isinstance(self.schemas, dict) else {}
        )
        truth_doc = _read_json(self.docs_dir / _safe_rel(doc.get("fields")), {}) if _safe_rel(doc.get("fields")) else {}
        truth: dict = (truth_doc or {}).get("fields") or {}
        entries = sorted(self.doc_ex.get(doc_id, []), key=lambda d: (str(d.get("pipeline")), str(d.get("variant"))))

        names = list(schema_fields)
        for src in [truth] + [d.get("fields") or {} for d in entries]:
            for k in src:
                if k not in names:
                    names.append(k)

        parts = ["<h2>Extracted fields</h2>"]
        if not names:
            parts.append('<p class="empty">No schema or ground-truth fields for this document.</p>')
            return "".join(parts)

        head = ["<th>Field</th>", "<th>Truth</th>"]
        for d in entries:
            head.append(
                f"<th>{e(d.get('pipeline'))} · {e(d.get('variant'))}"
                f'<br><span class="small muted">{e(fmt(d.get("accuracy"), "pct"))}</span></th>'
            )
        rows = []
        for name in names:
            desc = (
                (schema_fields.get(name) or {}).get("description")
                if isinstance(schema_fields.get(name), dict)
                else None
            )
            ftype = (schema_fields.get(name) or {}).get("type") if isinstance(schema_fields.get(name), dict) else None
            tip = " — ".join(x for x in (ftype, desc) if x)
            name_cell = (
                f'<td class="mono" title="{e(tip)}">{e(name)}</td>' if tip else f'<td class="mono">{e(name)}</td>'
            )
            cells = [name_cell, f"<td>{e(self._field_value(truth.get(name)))}</td>"]
            for d in entries:
                f = (d.get("fields") or {}).get(name)
                if not isinstance(f, dict):
                    cells.append(f'<td class="muted">{DASH}</td>')
                    continue
                correct = f.get("correct")
                val = e(self._field_value(f.get("predicted")))
                if correct is True:
                    cells.append(f'<td class="ok"><span class="mark">✓</span>{val}</td>')
                elif correct is False:
                    cells.append(f'<td class="bad"><span class="mark">✗</span>{val}</td>')
                else:
                    cells.append(f"<td>{val}</td>")
            rows.append("<tr>" + "".join(cells) + "</tr>")
        parts.append(
            f'<div class="table-wrap"><table><thead><tr>{"".join(head)}</tr></thead>'
            f"<tbody>{''.join(rows)}</tbody></table></div>"
        )
        if not entries:
            parts.append('<p class="muted small">No extraction results for this document.</p>')
        for d in entries:
            of = d.get("output_file")
            raw = _read_under(self.run_dir, of)
            title = f"Raw JSON — {d.get('pipeline')} · {d.get('variant')}"
            if raw is None:
                parts.append(f'<p class="muted small">{e(title)}: file not found <code>{e(of or "")}</code></p>')
                continue
            with contextlib.suppress(ValueError):
                raw = json.dumps(json.loads(raw), indent=2, ensure_ascii=False)
            parts.append(f"<details><summary>{e(title)} <code>{e(of)}</code></summary><pre>{e(raw)}</pre></details>")
        return "".join(parts)

    def render_doc(self, idx: int) -> str:
        doc = self.manifest[idx]
        doc_id = str(doc["id"])
        prefix = "../"
        nav = ['<a href="../index.html">← index</a>']
        if idx > 0:
            prev_id = str(self.manifest[idx - 1]["id"])
            nav.append(f'<a href="{_doc_href(prev_id)}">‹ {e(prev_id)}</a>')
        if idx < len(self.manifest) - 1:
            next_id = str(self.manifest[idx + 1]["id"])
            nav.append(f'<a href="{_doc_href(next_id)}">{e(next_id)} ›</a>')
        pages = doc.get("pages") or []
        jump = " ".join(f'<a href="#p{int(p.get("n") or 0)}">p{int(p.get("n") or 0)}</a>' for p in pages)
        header = (
            '<header class="doc"><div>'
            f'<h1 class="mono">{e(doc_id)}</h1>'
            f'<div><span class="badge type">{e(doc.get("type"))}</span> {e(doc.get("title") or "")}'
            + (f' <span class="muted small">· {e(doc.get("language"))}</span>' if doc.get("language") else "")
            + f' <span class="muted small">· {len(pages)} page{"s" if len(pages) != 1 else ""}</span></div>'
            + (f'<div class="small">Pages: {jump} · <a href="#fields">fields</a></div>' if pages else "")
            + f'</div><nav class="docnav">{"".join(nav)}</nav></header>'
        )
        body = [header]
        body.extend(self.render_page_section(doc, p, prefix) for p in pages)
        body.append(f'<section id="fields">{self.render_fields(doc)}</section>')
        body.append('<p class="small"><a href="../index.html">← back to index</a></p>')
        return _page(f"{doc_id} — ocr-bench {self.tag}", "\n".join(body), prefix)

    # ------------------------------------------------------------------ build

    def build(self) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.copy_assets()
        docs_out = self.out_dir / "docs"
        docs_out.mkdir(parents=True, exist_ok=True)
        for i, doc in enumerate(self.manifest):
            (docs_out / f"{doc['id']}.html").write_text(self.render_doc(i), encoding="utf-8")
        index = self.out_dir / "index.html"
        index.write_text(self.render_index(), encoding="utf-8")
        return index


def build_site(docs_dir: Path, run_dir: Path, scores: dict, out_dir: Path) -> Path:
    """Write a static, self-contained HTML viewer into *out_dir* and return the index path.

    *docs_dir* holds ``manifest.json``/``schemas.json`` and the page images; *run_dir*
    holds ``transcripts.jsonl`` and the raw ``outputs/``; *scores* is the dict saved as
    ``scores.json`` (see docs/data-formats.md). Images are copied to ``out_dir/assets``.
    """
    return _Site(Path(docs_dir), Path(run_dir), scores, Path(out_dir)).build()
