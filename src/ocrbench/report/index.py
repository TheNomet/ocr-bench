"""site/index.html: the experiments index (latest result, the cross-run comparison, one card per run, the shared guide)."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from .. import view
from ..explain import AGENT_INPUT, EXTRACTOR_INPUT
from ..metrics import fmt
from . import markdown_lite
from .shell import e, page_shell, write_static
from .site import VARIANTS


@dataclass
class RunCard:
    """One run on the experiments index. `card_meta` is run.json as the run page describes it (hidden
    backends removed); `headline` comes from reporting.headline; `bottom_line` from summary.md, with the
    numbers in it that are not in the run's current key facts in `unverified_numbers`."""

    meta: dict
    card_meta: dict
    scores: dict
    headline: dict
    bottom_line: str = ""
    unverified_numbers: list[str] = field(default_factory=list)


def newest_first(runs: list[RunCard]) -> list[RunCard]:
    """Runs ordered newest first by run.json `created`; the run id breaks ties and stands in when it is missing."""
    return sorted(runs, key=lambda r: (str(r.meta.get("created") or ""), str(r.meta.get("id") or "")), reverse=True)


def _bottom_line_html(r: RunCard) -> str:
    warn = (
        f'<p class="warn">Unverified numbers (not in the key facts): {e(", ".join(r.unverified_numbers))}</p>'
        if r.unverified_numbers
        else ""
    )
    return warn + markdown_lite.render(label_pipelines(r.bottom_line, view.pipeline_labels(r.card_meta)))


def _card_models(m: dict) -> str:
    notes = {
        view.ROLE_AGENT: f"Reads {AGENT_INPUT}; returns the fields as JSON.",
        view.ROLE_EXTRACTOR: f"Reads {EXTRACTOR_INPUT}; returns the fields as JSON.",
        view.ROLE_WRITER: "Writes the bottom line and recommendation from the computed key facts",
    }
    rows = []
    for mr in view.model_rows(m):
        note = " ".join(dict.fromkeys(notes[x] for x in mr.roles if x in notes))
        if view.ROLE_WRITER in mr.roles:
            note += f"; reasoning effort {mr.reasoning_effort}." if mr.reasoning_effort else "."
        chip = f'<span class="chip warmchip">temperature {mr.temperature:g}</span>' if mr.temperature else ""
        rows.append(
            f'<tr><td class="role">{e(", ".join(mr.roles))}</td><td><code>{e(mr.key)}</code></td><td>{chip}'
            f'<div class="mname">{e(mr.label)}</div>'
            + (
                f'<div class="mid" title="{e(mr.detail_title)}">{e(mr.detail)}</div>'
                if mr.detail_title
                else f'<div class="mid">{e(mr.detail)}</div>'
            )
            + (f'<div class="mnote">{markdown_lite.inline(note)}</div>' if note else "")
            + "</td></tr>"
        )
    return f'<div class="table-wrap plain"><table class="models"><caption>Models</caption>{"".join(rows)}</table></div>'


def _card_gpu(m: dict) -> str:
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
        '<div class="table-wrap plain"><table class="mini"><caption>Results</caption><thead><tr>'
        "<th>input → extractor</th><th>clean</th><th>degraded</th>"
        "<th>time / doc</th><th>$ / 1k docs</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
        '<p class="muted small">Field accuracy; best per variant in green. Time and cost: clean pages.</p>'
    )


_CODE_SPAN = re.compile(r"`([^`]+)`")


def label_pipelines(md: str, labels: dict[str, str]) -> str:
    """LLM Markdown with every `pipeline` code span shown as its readable label plus the raw key."""

    def sub(m: re.Match) -> str:
        if m[1] not in labels:
            return m[0]
        lbl = labels[m[1]]
        if re.search(r"(^|[.!?:]\s+)$", md[: m.start()]):  # starts a sentence
            lbl = lbl[:1].upper() + lbl[1:]
        return f"{lbl} (`{m[1]}`)"

    return _CODE_SPAN.sub(sub, md)


def _pipe_html(m: dict, pipe: str) -> str:
    return f'{e(view.pipeline_label(m, pipe))} <code class="key">{e(pipe)}</code>'


def _latest(r: RunCard) -> str:
    """The "Latest result" box: the newest run's most accurate pipeline per variant and its bottom line."""
    m = r.card_meta
    best = r.headline.get("best") or {}
    rows = "".join(
        f'<div class="latest-row"><span class="latest-v">{e(v)} pages</span>'
        f'<span class="latest-acc">{e(fmt(best[v]["field_accuracy"], "pct"))}</span>'
        f'<span class="latest-pipe">{_pipe_html(m, best[v]["pipeline"])}</span></div>'
        for v in VARIANTS
        if best.get(v)
    )
    if not rows and not r.bottom_line:
        return ""
    bl = f'<div class="latest-bl">{_bottom_line_html(r)}</div>' if r.bottom_line else ""
    return (
        '<section class="latest" aria-labelledby="latest-h">'
        f'<h2 id="latest-h">Latest result</h2><p class="muted small">Most accurate pipeline (field accuracy) in '
        f'<a href="{quote(m["id"])}/index.html">{e(m["id"])}</a>, the newest run.</p>'
        f"{rows}{bl}"
        f'<p class="small"><a href="{quote(m["id"])}/index.html">Open the run &rarr;</a></p></section>'
    )


def _repeat_chip(scores: dict) -> str:
    """ "run-to-run check" chip for a run that repeated something (scores["variation"])."""
    var = scores.get("variation") or {}
    n = max((a.get("repeats") or 0 for k in ("transcription", "extraction") for a in var.get(k) or []), default=0)
    return f'<span class="chip">run-to-run check &middot; {e(n)} repeats</span>' if n > 1 else ""


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
        f'<span class="chip">{e(created)}</span>{_repeat_chip(r.scores)}</div></div>'
        f'<a class="open" href="{quote(m["id"])}/index.html">Open run &rarr;</a></summary>'
        + (
            '<div class="bottom-line"><div class="label">Bottom line <span class="muted">(LLM comment)</span></div>'
            f"{_bottom_line_html(r)}</div>"
            if r.bottom_line
            else ""
        )
        + '<div class="run-grid">'
        f'<div class="block wide">{_card_models(m)}</div>'
        f'<div class="block"><h3>GPU (self-hosted models)</h3>{_card_gpu(m)}</div>'
        f'<div class="block">{_card_results(m, r.headline)}</div>'
        "</div></details>"
    )


def _guide_sections(guide_md: str) -> tuple[str, str]:
    """(intro, "How the benchmark works" body) of the shared guide."""
    head, sep, rest = guide_md.partition("## How the benchmark works")
    return head, rest if sep else ""


def _is_run_page(d: Path) -> bool:
    """A folder build_site wrote (index.html + docs/ + assets/), not the site's own assets/ or anything else."""
    return (
        d.is_dir()
        and d.name != "assets"
        and (d / "index.html").is_file()
        and (d / "docs").is_dir()
        and (d / "assets").is_dir()
    )


def prune_run_pages(runs: list[RunCard], out_dir: Path) -> list[str]:
    """Delete the run pages under `out_dir` whose run is not in `runs` (its folder was removed from runs/),
    so a publish doesn't keep serving them. Returns the names removed."""
    keep = {str(r.meta.get("id")) for r in runs}
    gone = sorted(d.name for d in Path(out_dir).iterdir() if d.name not in keep and _is_run_page(d))
    for name in gone:
        shutil.rmtree(Path(out_dir) / name)
    return gone


def build_experiments_index(
    runs: list[RunCard], out_dir: Path, comparison_md: str = "", guide_md: str = "", prune: bool = True
) -> Path:
    """site/index.html: the newest run's headline result, the cross-run comparison, one expandable card per
    run (models, GPU, headline results) linking to its run page, and the guide below them.

    Runs are shown newest first by `created`, whatever order they come in. With `prune`, run pages of runs
    that no longer exist are deleted (see prune_run_pages)."""
    out_dir = Path(out_dir)
    runs = newest_first(runs)
    write_static(out_dir)
    if prune:
        for name in prune_run_pages(runs, out_dir):
            print(f"removed stale run page: {out_dir / name}", flush=True)
    intro, guide = _guide_sections(guide_md)
    body = (
        '<section class="hero"><div class="eyebrow">Document reading benchmark</div>'
        "<h1>Which setup reads documents best?</h1>"
        + (_latest(runs[0]) if runs else "")
        + (f'<div class="intro">{markdown_lite.render(intro)}</div>' if intro else "")
        + "</section>"
        + '<nav class="toc small">Jump to: '
        + ('<a href="#compare">Compare runs</a> &middot; ' if comparison_md else "")
        + '<a href="#runs">Experiments</a>'
        + (' &middot; <a href="#guide">How the benchmark works</a>' if guide else "")
        + "</nav>"
        + (
            '<section id="compare" class="compare"><h2>Compare runs</h2>'
            f"{markdown_lite.render(comparison_md.replace('## Compare runs', '', 1), shift=0)}</section>"
            if comparison_md
            else ""
        )
        + f'<h2 id="runs">Experiments</h2><p class="muted">{len(runs)} run{"s" if len(runs) != 1 else ""}, '
        "newest first. Click a row to expand it, or open the run for its documents.</p>"
        + ("".join(_run_card(r) for r in runs) or '<p class="empty">No runs yet.</p>')
        + (
            '<section id="guide" class="guide"><h2>How the benchmark works</h2>'
            f"{markdown_lite.render(guide, shift=0)}</section>"
            if guide
            else ""
        )
    )
    idx = out_dir / "index.html"
    idx.write_text(page_shell("ocr-bench experiments", body, "", "index.html", []), encoding="utf-8")
    return idx
