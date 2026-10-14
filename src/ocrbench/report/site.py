"""Static, self-contained HTML viewer for benchmark results.

``build_site(docs_dir, run_dir, scores, out_dir, page)`` writes::

    out_dir/
      index.html              # summary, aggregate tables, document grid
      docs/<doc_id>.html      # per-document page: images, transcripts, diffs, fields
      assets/style.css
      assets/app.js
      assets/<doc_id>/...     # copied page images (same relative layout as docs_dir)
      assets/<doc_id>/p1.thumb.webp  # small WebP of each document's first clean page, for the grid

All links are relative so the folder can be zipped and opened via ``file://``.
Pillow (already used to render the documents) makes the thumbnails; everything else is the standard library.
"""

from __future__ import annotations

import contextlib
import difflib
import json
import re
import shutil
import statistics
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

from PIL import Image

from .. import metrics
from ..fileio import read_jsonl, read_text
from ..metrics import DASH, Metric, _num, fmt
from . import markdown_lite
from .shell import e, page_shell, write_static

VARIANTS = ("clean", "degraded")
THUMB_WIDTH = 600  # px; the grid shows thumbnails at ~280 px wide, so this stays sharp on 2x screens
THUMB_QUALITY = 75


# --------------------------------------------------------------------------- formatting


def _doc_href(doc_id: str) -> str:
    return quote(f"{doc_id}.html")


# --------------------------------------------------------------------------- tables


Col = Metric  # a table column is a metric from the shared registry
SPEED_COLS, TRANSCRIPTION_COLS, EXTRACTION_COLS = metrics.SPEED, metrics.TRANSCRIPTION, metrics.EXTRACTION


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
    return None if p is None else read_text(base / p)


def thumb_rel(rel: PurePosixPath) -> PurePosixPath:
    """`<doc>/p1.png` -> `<doc>/p1.thumb.webp`."""
    return rel.with_name(f"{rel.stem}.thumb.webp")


def write_thumb(src: Path, dst: Path, width: int = THUMB_WIDTH) -> None:
    """A WebP of *src* at most *width* px wide; skipped if *dst* is already newer than *src*."""
    if dst.is_file() and dst.stat().st_mtime >= src.stat().st_mtime:
        return
    with Image.open(src) as im:
        im.load()
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, "white")
            bg.paste(im, mask=im.getchannel("A"))
            im = bg
        elif im.mode != "RGB":
            im = im.convert("RGB")
        if im.width > width:
            im = im.resize((width, max(1, round(im.height * width / im.width))), Image.Resampling.LANCZOS)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".tmp")
        im.save(tmp, "WEBP", quality=THUMB_QUALITY, method=6)
        tmp.replace(dst)


def _image_size(path: Path) -> tuple[int, int] | None:
    try:
        with Image.open(path) as im:
            return im.size
    except OSError:
        return None


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


# --------------------------------------------------------------------------- site builder


@dataclass
class RunPage:
    """What a run page shows besides the score tables. Built by `reporting.write` from run.json, the
    key facts and summary.md, so the page and report.md say the same thing."""

    facts_md: str = ""
    recommendation: str = ""  # the LLM recommendation (Markdown); empty until the summarize stage ran
    recommendation_author: str = ""
    unverified_numbers: list[str] = field(default_factory=list)
    setup_md: str = ""
    plan_description: str = ""
    hide_backends: list[str] = field(default_factory=list)
    pipeline_labels: dict[str, str] = field(default_factory=dict)


_REC_HEADING = re.compile(r"^(#{1,6})[ \t]+Recommendation[ \t]*$", re.M)


def recommendation_md(md: str) -> str:
    """summary.md as shown under the page's "Recommendation" heading: its own "## Recommendation"
    section (the goal -> choice table) is renamed so the page doesn't nest Recommendation in Recommendation."""
    return _REC_HEADING.sub(r"\1 Choice by goal", md)


class _Site:
    def __init__(self, docs_dir: Path, run_dir: Path, scores: dict, out_dir: Path, page: RunPage) -> None:
        self.page = page
        labels = page.pipeline_labels
        if labels:
            scores = {**(scores or {})}
            ex = dict(scores.get("extraction") or {})
            ex["aggregate"] = [
                {**a, "label": labels.get(a.get("pipeline"), a.get("pipeline"))} for a in ex.get("aggregate") or []
            ]
            scores["extraction"] = ex
        self.docs_dir = Path(docs_dir)
        self.run_dir = Path(run_dir)
        self.scores = scores or {}
        self.out_dir = Path(out_dir)

        manifest = _read_json(self.docs_dir / "manifest.json", [])
        self.manifest: list[dict] = [d for d in manifest if isinstance(d, dict) and d.get("id")]
        self.schemas: dict = _read_json(self.docs_dir / "schemas.json", {}) or {}
        self.tag = str(self.scores.get("run_id") or "")

        tr = self.scores.get("transcription") or {}
        ex = self.scores.get("extraction") or {}
        self.speed: list[dict] = list(self.scores.get("speed") or [])
        self.tr_agg: list[dict] = list(tr.get("aggregate") or [])
        self.tr_pages: list[dict] = list(tr.get("pages") or [])
        self.ex_agg: list[dict] = list(ex.get("aggregate") or [])
        self.ex_docs: list[dict] = list(ex.get("docs") or [])

        # (doc_id, page, variant) -> {backend: scored page entry}
        self.page_scores: dict[tuple[str, int, str], dict[str, dict]] = defaultdict(dict)
        for p in self.tr_pages:
            key = (str(p.get("doc_id")), int(p.get("page") or 0), str(p.get("variant")))
            self.page_scores[key].setdefault(str(p.get("backend")), p)

        # Unscored transcripts (fallback so raw output is still viewable).
        self.page_raw: dict[tuple[str, int, str], dict[str, dict]] = defaultdict(dict)
        hidden_be = set(self.page.hide_backends)
        for t in read_jsonl(self.run_dir / "transcripts.jsonl"):
            if not t.get("ok") or not t.get("output_file") or t.get("backend") in hidden_be:
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
        """Copy the page images, and write a thumbnail of each document's first clean page for the grid."""
        assets = write_static(self.out_dir)
        for doc in self.manifest:
            for i, page in enumerate(doc.get("pages") or []):
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
                    if i == 0 and v == "clean":
                        write_thumb(src, assets / thumb_rel(rel))

    def asset_href(self, rel: str | None, prefix: str) -> str | None:
        p = _safe_rel(rel)
        if p is None or not (self.out_dir / "assets" / p).is_file():
            return None
        return prefix + "assets/" + "/".join(quote(part) for part in p.parts)

    def thumb_img(self, rel: str | None, alt: str) -> str:
        """<img> for the document grid: the small thumbnail if there is one, else the full image."""
        p = _safe_rel(rel)
        if p is None:
            return '<span class="muted small" style="padding:1rem">no image</span>'
        for candidate in (thumb_rel(p), p):
            href = self.asset_href(str(candidate), "")
            if href:
                size = _image_size(self.out_dir / "assets" / candidate)
                wh = f' width="{size[0]}" height="{size[1]}"' if size else ""
                return f'<img src="{e(href)}" alt="{e(alt)}"{wh} loading="lazy" decoding="async">'
        return '<span class="muted small" style="padding:1rem">no image</span>'

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

    def _crumbs(self) -> list[tuple[str, str | None]]:
        return [("Experiments", "../index.html"), (self.tag, None)]

    def render_header(self) -> list[str]:
        pg = self.page
        parts = [f'<div class="eyebrow">Experiment</div><h1>{e(self.tag)}</h1>']
        if pg.plan_description:
            parts.append(f'<p class="plan-desc">{markdown_lite.inline(pg.plan_description)}</p>')
        parts.append(
            '<p class="muted">One experiment. New here? The <a href="../index.html#guide">main page</a> explains '
            "what the benchmark does, what the pipelines and metrics mean, and the document types.</p>"
            '<nav class="toc small">Jump to: <a href="#facts">Key facts</a> &middot; '
            '<a href="#recommendation">Recommendation</a> &middot; <a href="#setup">Setup</a> &middot; '
            '<a href="#results">Results</a> &middot; <a href="#documents">Documents</a></nav>'
        )
        if pg.facts_md:  # its "## Key facts" is a top-level section of the page: <h2>
            parts.append(f'<section id="facts" class="facts">{markdown_lite.render(pg.facts_md, shift=0)}</section>')
        parts.append('<h2 id="recommendation">Recommendation</h2>')
        if pg.recommendation:
            if pg.unverified_numbers:
                parts.append(
                    f'<p class="warn">Unverified numbers (not in the key facts): {e(", ".join(pg.unverified_numbers))}</p>'
                )
            parts.append(
                f'<p class="muted small">{e(pg.recommendation_author)}. Check the numbers against the tables below.</p>'
            )
            parts.append(f'<div class="summary">{markdown_lite.render(recommendation_md(pg.recommendation))}</div>')
        else:
            parts.append('<p class="empty">No recommendation yet: run the summarize stage.</p>')
        parts.append(f'<section id="setup">{markdown_lite.render(pg.setup_md, shift=0)}</section>')
        return parts

    def render_index(self) -> str:
        parts = self.render_header()
        parts.append('<h2 id="results">Results</h2>')
        parts.append("<h3>Speed and cost (stage 1)</h3>")
        parts.append(render_table(self.speed, SPEED_COLS, "concurrency", "No speed data."))
        parts.append("<h3>Transcription quality (stage 1)</h3>")
        parts.append(render_table(self.tr_agg, TRANSCRIPTION_COLS, "variant", "No transcription scores."))
        parts.append("<h3>Field extraction (stage 2)</h3>")
        parts.append(render_table(self.ex_agg, EXTRACTION_COLS, "variant", "No extraction scores."))

        parts.append(f'<h2 id="documents">Documents ({len(self.manifest)})</h2>')
        parts.append(self.render_doc_grid())
        return page_shell(f"ocr-bench — {self.tag}", "\n".join(parts), "", "../index.html", self._crumbs())

    def render_doc_grid(self) -> str:
        if not self.manifest:
            return '<p class="empty">No documents in manifest.</p>'
        parts = []
        types = self._order([str(d.get("type") or "") for d in self.manifest])
        buttons = ['<button class="type-filter active" data-type="" aria-pressed="true">All</button>']
        for t in types:
            count = sum(1 for d in self.manifest if str(d.get("type") or "") == t)
            buttons.append(
                f'<button class="type-filter" data-type="{e(t)}" aria-pressed="false">{e(t)} ({count})</button>'
            )
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
            thumb_html = self.thumb_img(first.get("clean"), f"{doc_id} page 1")
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
        return "\n".join(parts)

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
                out.append(f"<h3>Word diff vs ground truth: {e(backend)}</h3>")
                out.append(
                    '<p class="muted small"><del>missing</del> = in ground truth only, '
                    "<ins>extra</ins> = in output only (normalized text as scored).</p>"
                )
                out.append(f'<div class="diff mono">{word_diff(gt_text, str(clean_text))}</div>')
            elif clean_text is not None:
                out.append(f"<h3>Scored text: {e(backend)}</h3><pre>{e(clean_text)}</pre>")
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
            f'<button class="vbtn{" active" if i == 0 else ""}" data-variant="{v}" '
            f'aria-pressed="{"true" if i == 0 else "false"}">{v}</button>'
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
            ids = [f"{sec_id}-{v}-{j}" for j in range(len(labels))]
            tab_buttons = []
            for j, lbl in enumerate(labels):
                cer = (
                    f' <span class="small">({e(fmt(scored[lbl].get("cer"), "rate"))})</span>'
                    if j and lbl in scored
                    else ""
                )
                tab_buttons.append(
                    f'<button role="tab" id="tab-{ids[j]}" aria-controls="panel-{ids[j]}"'
                    + (
                        ' class="active" aria-selected="true" tabindex="0"'
                        if j == 0
                        else ' aria-selected="false" tabindex="-1"'
                    )
                    + f">{e(lbl)}{cer}</button>"
                )
            gt_html = (
                f'<pre class="mono">{e(gt_text)}</pre>'
                if gt_text is not None
                else '<p class="empty">Ground truth not found.</p>'
            )
            bodies = [gt_html] + [self.render_backend_panel(b, scored.get(b), raw.get(b), gt_text) for b in backends]
            panels = [
                f'<div class="tabpanel" role="tabpanel" id="panel-{ids[j]}" aria-labelledby="tab-{ids[j]}" '
                f'tabindex="0"{" hidden" if j else ""}>{html}</div>'
                for j, html in enumerate(bodies)
            ]
            hidden = "" if i == 0 else " hidden"
            panes.append(
                f'<div class="vpane" data-variant="{v}"{hidden}><div class="tabset">'
                f'<div class="tabs" role="tablist" aria-label="Transcripts, page {n} {v}">{"".join(tab_buttons)}</div>'
                f"{''.join(panels)}</div>"
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
        fields_rel = _safe_rel(doc.get("fields"))
        truth_doc = _read_json(self.docs_dir / fields_rel, {}) if fields_rel else {}
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
        return page_shell(
            f"{doc_id} — ocr-bench {self.tag}",
            "\n".join(body),
            prefix,
            "../../index.html",
            [("Experiments", "../../index.html"), (self.tag, "../index.html"), (doc_id, None)],
        )

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


def build_site(docs_dir: Path, run_dir: Path, scores: dict, out_dir: Path, page: RunPage) -> Path:
    """Write a static, self-contained HTML viewer into *out_dir* and return the index path.

    *docs_dir* holds ``manifest.json``/``schemas.json`` and the page images; *run_dir*
    holds ``transcripts.jsonl`` and the raw ``outputs/``; *scores* is the dict saved as
    ``scores.json`` (see docs/data-formats.md). Images are copied to ``out_dir/assets``.
    """
    return _Site(Path(docs_dir), Path(run_dir), scores, Path(out_dir), page).build()
