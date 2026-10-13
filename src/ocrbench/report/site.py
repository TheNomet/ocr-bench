"""Static, self-contained HTML viewer for benchmark results.

``build_site(docs_dir, run_dir, scores, out_dir, page)`` writes::

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
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

from .. import metrics
from ..metrics import DASH, Metric, _num, fmt
from . import markdown_lite
from .assets import CSS, JS

VARIANTS = ("clean", "degraded")


# --------------------------------------------------------------------------- formatting


def e(value: Any) -> str:
    """HTML-escape any value (``None`` → empty string)."""
    return html.escape("" if value is None else str(value), quote=True)


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


FAVICON = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E"
    "%3Crect width='32' height='32' rx='8' fill='%234f46e5'/%3E%3Cpath d='M9 9h10l4 4v10H9z' fill='white'/%3E"
    "%3Cpath d='M12 16h8M12 20h6' stroke='%234f46e5' stroke-width='2'/%3E%3C/svg%3E"
)


def _repo_url() -> str | None:
    """https URL of the repo's `origin` remote, for the top-bar link (None if unavailable)."""
    import subprocess

    try:
        url = subprocess.run(
            ["git", "remote", "get-url", "origin"], capture_output=True, text=True, cwd=Path(__file__).resolve().parent
        ).stdout.strip()
    except OSError:
        return None
    if url.startswith("git@"):
        url = "https://" + url[4:].replace(":", "/", 1)
    url = url.removesuffix(".git")
    return url if url.startswith("https://github.com/") else None


GITHUB_ICON = (
    '<svg viewBox="0 0 16 16" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M8 0C3.58 0 '
    "0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-"
    ".23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07"
    "-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-"
    ".27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-"
    "3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"
    '"/></svg>'
)
REPO_URL = _repo_url()


def _page(title: str, body: str, prefix: str, home: str, crumbs: list[tuple[str, str | None]]) -> str:
    """`home`: the experiments index, relative to this page (the brand links there). `crumbs`: the trail
    after the brand, [(label, href or None)], hrefs relative to this page."""
    trail = "".join(
        '<span class="sep">/</span>' + (f'<a href="{h}">{e(lbl)}</a>' if h else f"<span>{e(lbl)}</span>")
        for lbl, h in crumbs
    )
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{e(title)}</title>"
        f'<link rel="icon" href="{FAVICON}">'
        f'<link rel="stylesheet" href="{prefix}assets/style.css">'
        "</head><body>"
        f'<header class="topbar"><div class="topbar-in"><a class="brand" href="{home}">'
        '<span class="logo"></span>ocr-bench</a><nav class="crumbs">'
        f"{trail}</nav>"
        + (
            f'<a class="gh" href="{e(REPO_URL)}" title="Source on GitHub">{GITHUB_ICON}<span>GitHub</span></a>'
            if REPO_URL
            else ""
        )
        + "</div></header>"
        f"<main>\n{body}\n</main>"
        '<footer class="foot">Generated by ocr-bench · synthetic documents only · numbers in tables are computed; '
        "recommendation text is LLM-written</footer>"
        f'<script src="{prefix}assets/app.js"></script></body></html>\n'
    )


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
        self.tag = str(self.scores.get("run_id") or self.scores.get("tag") or "")

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
        hidden_be = set(self.page.hide_backends)
        for t in _read_jsonl(self.run_dir / "transcripts.jsonl"):
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

    def _crumbs(self) -> list[tuple[str, str | None]]:
        return [("Experiments", "../index.html"), (self.tag, None)]

    def render_header(self) -> list[str]:
        pg = self.page
        parts = [f'<div class="eyebrow">Experiment</div><h1>{e(self.tag)}</h1>']
        if pg.plan_description:
            parts.append(f'<p class="plan-desc">{markdown_lite.inline(pg.plan_description)}</p>')
        gen = self.scores.get("generated")
        if gen:
            parts.append(f'<p class="muted small">Scored {e(str(gen).replace("T", " ")[:16])}</p>')
        parts.append(
            '<p class="muted">One experiment. New here? The <a href="../index.html#guide">main page</a> explains '
            "what the benchmark does, what the pipelines and metrics mean, and the document types.</p>"
            '<nav class="toc small">Jump to: <a href="#facts">Key facts</a> &middot; '
            '<a href="#recommendation">Recommendation</a> &middot; <a href="#setup">Setup</a> &middot; '
            '<a href="#results">Results</a> &middot; <a href="#documents">Documents</a></nav>'
        )
        if pg.facts_md:
            parts.append(f'<section id="facts" class="facts">{markdown_lite.render(pg.facts_md)}</section>')
        parts.append('<h2 id="recommendation">Recommendation</h2>')
        if pg.recommendation:
            if pg.unverified_numbers:
                parts.append(
                    f'<p class="warn">Unverified numbers (not in the key facts): {e(", ".join(pg.unverified_numbers))}</p>'
                )
            parts.append(
                f'<p class="muted small">{e(pg.recommendation_author)}. Check the numbers against the tables below.</p>'
            )
            parts.append(f'<div class="summary">{markdown_lite.render(pg.recommendation)}</div>')
        else:
            parts.append('<p class="empty">No recommendation yet: run the summarize stage.</p>')
        parts.append(f'<section id="setup">{markdown_lite.render(pg.setup_md)}</section>')
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
        return _page(f"ocr-bench — {self.tag}", "\n".join(parts), "", "../index.html", self._crumbs())

    def render_doc_grid(self) -> str:
        if not self.manifest:
            return '<p class="empty">No documents in manifest.</p>'
        parts = []
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
        return _page(
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


@dataclass
class RunCard:
    """One run on the experiments index. `card_meta` is run.json as the run page describes it (hidden
    backends removed); `headline` comes from reporting.headline; `bottom_line` from summary.md."""

    meta: dict
    card_meta: dict
    scores: dict
    headline: dict
    bottom_line: str = ""


def _card_models(m: dict) -> str:
    from .. import view
    from ..explain import AGENT_INPUT

    notes = {
        view.ROLE_AGENT: f"Reads {AGENT_INPUT}; returns the fields as JSON.",
        view.ROLE_WRITER: "Writes the bottom line and recommendation from the computed key facts",
    }
    rows = []
    for mr in view.model_rows(m):
        note = " ".join(notes[x] for x in mr.roles if x in notes)
        if view.ROLE_WRITER in mr.roles:
            note += f"; reasoning effort {mr.reasoning_effort}." if mr.reasoning_effort else "."
        chip = f'<span class="chip warmchip">temperature {mr.temperature:g}</span>' if mr.temperature else ""
        rows.append(
            f'<tr><td class="role">{e(", ".join(mr.roles))}</td><td><code>{e(mr.key)}</code></td><td>{chip}'
            f'<div class="mname">{e(mr.label)}</div><div class="mid">{e(mr.detail)}</div>'
            + (f'<div class="mnote">{markdown_lite.inline(note)}</div>' if note else "")
            + "</td></tr>"
        )
    return f'<table class="models">{"".join(rows)}</table>'


def _card_gpu(m: dict) -> str:
    from .. import view

    sh = view.selfhosted_in_run(m)
    if not sh:
        return '<p class="muted">No self-hosted model in this run.</p>'
    parts = []
    for name in sh:
        b = view.spec(m, name)
        price = b.get("price_per_hour_usd")
        price_s = f"${price:.2f}/h" if isinstance(price, (int, float)) else "?"
        parts.append(
            (f'<div class="kv"><span>Model</span><code>{e(name)}</code></div>' if len(sh) > 1 else "")
            + f'<div class="kv"><span>Instance</span><code>{e(b.get("instance_type") or "?")}</code></div>'
            f'<div class="kv"><span>GPU</span><span>{e(b.get("gpu") or "?")}</span></div>'
            f'<div class="kv"><span>Host</span><span>{e(b.get("host") or "?")}</span></div>'
            f'<div class="kv"><span>Price</span><span><b>{e(price_s)}</b> '
            '<span class="muted">on-demand, billed while idle too</span></span></div>'
        )
    return "".join(parts) + f'<div class="kv"><span>Region</span><span>{e(m.get("region"))}</span></div>'


def _card_results(m: dict, h: dict) -> str:
    from .. import view

    best = {v: (h.get("best") or {}).get(v, {}).get("pipeline") for v in VARIANTS}
    rows = []
    for p, vs in (h.get("pipelines") or {}).items():
        cells = []
        for v in VARIANTS:
            x = vs.get(v)
            cls = ' class="win"' if x and best.get(v) == p else ""
            cells.append(f"<td{cls}>{e(fmt(x['acc'], 'pct')) if x else '&ndash;'}</td>")
        c = vs.get("clean") or vs.get("degraded") or {}
        rows.append(
            f'<tr><td title="{e(p)}">{e(view.pipeline_label(m, p))}</td>{"".join(cells)}'
            f"<td>{e(fmt(c.get('s'), 'sec'))} s</td><td>{e(fmt(c.get('cost'), 'usd'))}</td></tr>"
        )
    if not rows:
        return '<p class="muted">not scored yet</p>'
    return (
        '<table class="mini"><thead><tr><th>input → extractor</th><th>clean</th><th>degraded</th>'
        "<th>time / doc</th><th>$ / 1k docs</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
        '<p class="muted small">Field accuracy; best per variant in green. Time and cost: clean pages.</p>'
    )


def _run_card(r: RunCard) -> str:
    m, docs = r.card_meta, r.card_meta.get("documents") or {}
    created = (m.get("created") or "")[:16].replace("T", " ")
    desc = m.get("plan_description") or ""
    return (
        '<details class="run-card">'
        '<summary><span class="chev" aria-hidden="true"></span>'
        f'<div class="run-sum"><div class="run-id">{e(m["id"])}</div>'
        + (f'<div class="plan-desc">{markdown_lite.inline(desc)}</div>' if desc else "")
        + f'<div class="chips"><span class="chip">plan {e(m.get("plan"))}</span>'
        f'<span class="chip">{e(docs.get("count"))} docs &middot; {e(docs.get("page_images"))} page images</span>'
        f'<span class="chip">{e(created)}</span></div></div>'
        f'<a class="open" href="{quote(m["id"])}/index.html">Open run &rarr;</a></summary>'
        + (
            '<div class="bottom-line"><div class="label">Bottom line <span class="muted">(LLM comment)</span></div>'
            f"{markdown_lite.render(r.bottom_line)}</div>"
            if r.bottom_line
            else ""
        )
        + '<div class="run-grid">'
        f'<div class="block wide"><h4>Models</h4>{_card_models(m)}</div>'
        f'<div class="block"><h4>GPU (self-hosted models)</h4>{_card_gpu(m)}</div>'
        f'<div class="block"><h4>Results</h4>{_card_results(m, r.headline)}</div>'
        "</div></details>"
    )


def _guide_sections(guide_md: str) -> tuple[str, str]:
    """(intro, "How the benchmark works" body) of the shared guide."""
    head, sep, rest = guide_md.partition("## How the benchmark works")
    return head, rest if sep else ""


def build_experiments_index(runs: list[RunCard], out_dir: Path, comparison_md: str = "", guide_md: str = "") -> Path:
    """site/index.html: the guide, the cross-run comparison, and one expandable card per run (models,
    GPU, headline results), each linking to its run page."""
    out_dir = Path(out_dir)
    assets = out_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "style.css").write_text(CSS.lstrip(), encoding="utf-8")
    (assets / "app.js").write_text(JS.lstrip(), encoding="utf-8")
    intro, guide = _guide_sections(guide_md)
    body = (
        '<section class="hero"><div class="eyebrow">Document reading benchmark</div>'
        "<h1>Which setup reads documents best?</h1>"
        + (f'<div class="intro">{markdown_lite.render(intro)}</div>' if intro else "")
        + "</section>"
        + '<nav class="toc small">Jump to: <a href="#guide">How the benchmark works</a> &middot; '
        '<a href="#compare">Compare runs</a> &middot; <a href="#runs">Experiments</a></nav>'
        + (
            f'<section id="guide" class="guide"><h2>How the benchmark works</h2>{markdown_lite.render(guide)}</section>'
            if guide
            else ""
        )
        + (
            '<section id="compare" class="compare"><h2>Compare runs</h2>'
            f"{markdown_lite.render(comparison_md.replace('## Compare runs', '', 1))}</section>"
            if comparison_md
            else ""
        )
        + f'<h2 id="runs">Experiments</h2><p class="muted">{len(runs)} run{"s" if len(runs) != 1 else ""}, '
        "newest first. Click a row to expand it, or open the run for its documents.</p>"
        + ("".join(_run_card(r) for r in runs) or '<p class="empty">No runs yet.</p>')
    )
    idx = out_dir / "index.html"
    idx.write_text(_page("ocr-bench experiments", body, "", "index.html", []), encoding="utf-8")
    return idx
