"""report.md + site for one run, and the experiments index across runs.

The setup, legend and LLM recommendation are produced as Markdown here, so report.md and the
site show exactly the same information (the site renders it with markdown_lite).
"""

from __future__ import annotations

import json
from pathlib import Path

from . import explain, metrics, prompts, summarize, view
from .facts import compute, most_missed, pipeline_facts, to_md
from .fileio import write_if_changed
from .report import RunCard, RunPage, build_site, newest_first, recommendation_md


# --------------------------------------------------------------------------- setup + legend
def _md_models(meta: dict) -> list[str]:
    out = [
        "### Models",
        "",
        "| role | name in tables | model | model id | served by | temperature | price |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in view.model_rows(meta):
        temp = "default" if r.temperature is None else f"{r.temperature:g}"
        out.append(f"| {', '.join(r.roles)} | `{r.key}` | {r.label} | `{r.model_id}` | {r.via} | {temp} | {r.price} |")
    return out


def _md_agent(meta: dict) -> list[str]:
    title = "### The extraction agent" if view.default_agent_shown(meta) else "### The extractors"
    out = [title, "", view.agent_note(meta)]
    if view.sampling(meta):
        out += [
            "",
            "Models with temperature above 0 sample their output, so their results vary slightly between "
            "runs; differences of a point or two are within noise.",
        ]
    hidden = view.hidden_backends(meta)
    if hidden:
        out += [
            "",
            "Also re-run as a reproducibility check, shown only in *Compare runs* on the main page: "
            + ", ".join(f"`{h}`" for h in sorted(hidden))
            + ".",
        ]
    return out


def _md_gpu(meta: dict) -> list[str]:
    sh = view.selfhosted_in_run(meta)
    out = ["### GPU", ""]
    if not sh:
        out.append("No self-hosted model in this run, so no GPU was used.")
    for i, name in enumerate(sh):
        out += ([""] if i else []) + ([f"`{name}`:", ""] if len(sh) > 1 else []) + ["| | |", "|---|---|"]
        out += [f"| {k} | {v} |" for k, v in view.gpu_rows(meta, name)]
    return out


def _md_run(meta: dict) -> list[str]:
    docs, runner, items = meta["documents"], meta["runner"], meta.get("plan_items") or []
    out = [
        "### Run",
        "",
        "| | |",
        "|---|---|",
        f"| plan | `{meta['plan']}`: {meta.get('plan_description') or ''} ({len(items)} transcription runs) |",
        f"| documents | {docs['count']} documents, {docs['pages']} pages, {docs['page_images']} page images "
        f"(clean + degraded), seed {docs.get('seed')} |",
        f"| runner | {runner.get('mode')} ({runner.get('cpu')} CPU units / {runner.get('memory')} MB) |",
        f"| code | git `{meta.get('git_commit') or '?'}` |",
        f"| started | {meta.get('created')} |",
    ]
    if items:
        out += ["", "Transcription runs in this plan:", ""]
        for it in items:
            extra = ", ".join(f"{k} {v}" for k, v in it.items() if k not in ("backend", "concurrency"))
            out.append(
                f"- `{it['backend']}` at concurrency {it.get('concurrency', 1)}" + (f" ({extra})" if extra else "")
            )
    return out


def setup_md(meta: dict) -> str:
    out = ["## Setup", ""] + _md_models(meta) + [""] + _md_agent(meta) + [""] + _md_gpu(meta) + [""] + _md_run(meta)
    return "\n".join(out)


def legend_md(meta: dict) -> str:
    out = ["## How to read this report", "", "### Stages", ""]
    out += [f"- **{a}**: {b}" for a, b in explain.STAGES]
    out += [
        "",
        "### Pipelines (the arrows)",
        "",
        explain.ARROW,
        "",
        "| pipeline | what the extractor reads |",
        "|---|---|",
    ]
    out += [f"| `{p}` | {view.pipeline_text(meta, p)} |" for p in meta["extraction"]["pipelines"]]
    out += ["", "### Page variants", ""] + [f"- **{k}**: {v}" for k, v in explain.VARIANTS.items()]
    out += [
        "",
        "### Document types",
        "",
        "| type | what it is | docs | pages | hard part | fields extracted |",
        "|---|---|---|---|---|---|",
    ]
    for t, d in meta["documents"]["types"].items():
        out.append(
            f"| `{t}` | {d['description']} | {d['docs']} | {d['pages']} | {explain.CHALLENGES.get(t, '')} | "
            f"{', '.join(d['fields'])} |"
        )
    out += [
        "",
        "All documents are synthetic Norwegian (bokmål) everyday paperwork with fictional names and numbers.",
        "",
        "### Metrics",
        "",
    ] + [f"- **{k}**: {v}" for k, v in metrics.GLOSSARY]
    return "\n".join(out)


# --------------------------------------------------------------------------- result tables (markdown)
def results_md(scores: dict) -> str:
    out = ["## Results"]
    if scores["speed"]:
        out += ["", "### Speed and cost (stage 1)", ""] + metrics.markdown_table(scores["speed"], metrics.SPEED_MD)
    ta = scores["transcription"]["aggregate"]
    if ta:
        out += ["", "### Transcription quality (stage 1)", ""] + metrics.markdown_table(ta, metrics.TRANSCRIPTION_MD)
    ea = scores["extraction"]["aggregate"]
    if ea:
        out += ["", "### Field extraction (stage 2)", ""] + metrics.markdown_table(ea, metrics.EXTRACTION_MD)
        misses = most_missed(scores["extraction"]["docs"], n=5)
        if misses:
            out += ["", "Most-missed fields (count over clean + degraded):", ""]
            out += [f"- **{p}**: " + ", ".join(f"`{k}` ({v})" for k, v in m) for p, m in misses.items()]
    return "\n".join(out)


def read_summary(run_dir: Path) -> tuple[str, dict]:
    """(markdown body, info) of the LLM recommendation; info (who wrote it, unverified numbers) from summary.json."""
    p = run_dir / "summary.md"
    if not p.exists():
        return "", {}
    info_p = run_dir / "summary.json"
    info = json.loads(info_p.read_text(encoding="utf-8")) if info_p.exists() else {}
    return p.read_text(encoding="utf-8").strip(), info


def unverified(meta: dict, scores: dict, text: str) -> list[str]:
    """Numbers in `text` (LLM-written) not found in the run's current key facts and setup. Re-checked at report
    time rather than read from summary.json, so numbers made stale by a re-score are flagged."""
    return summarize.unverified_numbers(text, summarize.build_prompt(meta, scores)[1]) if text else []


def author_line(info: dict) -> str:
    if not info:
        return "LLM-written"
    return f"LLM comment by {info.get('backend')} ({info.get('model')}) on the computed key facts"


def headline(scores: dict) -> dict:
    """Short facts for the experiments index, from the scores a run page shows (view.visible)."""
    ea = scores["extraction"]["aggregate"]
    best = {v: f["most_accurate"] for v, f in pipeline_facts(ea).items()}
    pipes: dict[str, dict] = {}
    for a in ea:
        pipes.setdefault(a["pipeline"], {})[a["variant"]] = {
            "acc": a["field_accuracy"],
            "cost": a["cost_per_1k_docs_usd"],
            "s": a["end_to_end_latency_s"],
        }
    return {"best": best, "pipelines": pipes}


def _report_md(meta: dict, scores: dict, facts_md: str, body: str, info: dict, bad: list[str]) -> str:
    """report.md from the focused run.json and visible scores (view.focus); `bad`: the unverified numbers."""
    recommendation = (
        ["## Recommendation", "", f"*{author_line(info)}. It interprets the key facts above.*", ""]
        + ([f"> **Unverified numbers** (not found in the key facts): {', '.join(bad)}", ""] if bad else [])
        + [recommendation_md(body)]
        if body
        else ["## Recommendation", "", "*No LLM recommendation yet. Run the `summarize` stage.*"]
    )
    parts = [
        f"# ocr-bench run `{meta['id']}`",
        "",
        facts_md,
        "\n".join(recommendation),
        setup_md(meta),
        legend_md(meta),
        results_md(scores),
    ]
    return "\n\n".join(parts) + "\n"


def report_md(meta: dict, scores: dict, run_dir: Path) -> str:
    fm, vis = view.focus(meta, scores)
    body, info = read_summary(run_dir)
    bad = unverified(meta, scores, body)
    return _report_md(fm, vis, to_md(compute(fm, vis), view.pipeline_labels(fm)), body, info, bad)


def write(meta: dict, scores: dict, run_dir: Path, site_root: Path) -> Path:
    fm, vis = view.focus(meta, scores)
    body, info = read_summary(run_dir)
    labels = view.pipeline_labels(fm)
    facts_md = to_md(compute(fm, vis), labels)
    bad = unverified(meta, scores, body)
    write_if_changed(run_dir / "scores.json", json.dumps(scores, ensure_ascii=False, indent=1))
    write_if_changed(run_dir / "report.md", _report_md(fm, vis, facts_md, body, info, bad))
    page = RunPage(
        facts_md=facts_md,
        recommendation=body,
        recommendation_author=author_line(info),
        unverified_numbers=bad,
        setup_md=setup_md(fm),
        plan_description=fm.get("plan_description", ""),
        hide_backends=sorted(view.hidden_backends(fm)),
        pipeline_labels=labels,
    )
    return build_site(run_dir / "docs", run_dir, vis, site_root / fm["id"], page)


def bottom_line(run_dir: Path) -> str:
    """First section of the recommendation ("## Bottom line"), for the experiments index."""
    body, _ = read_summary(run_dir)
    _, sep, rest = body.partition("## Bottom line")
    return rest.split("\n## ", 1)[0].strip() if sep else ""


def run_card(meta: dict, scores: dict, run_dir: Path) -> RunCard:
    fm, vis = view.focus(meta, scores)
    bl = bottom_line(run_dir)
    return RunCard(
        meta=meta,
        card_meta=fm,
        scores=scores,
        headline=headline(vis),
        bottom_line=bl,
        unverified_numbers=unverified(meta, scores, bl),
    )


def comparison(items: list[RunCard]) -> dict:
    """Cross-run tables over runs, newest first.

    Rows are keyed by what was actually measured (pipeline + extractor model id, or backend + model id),
    so a renamed config key never silently compares different models.
    """
    items = newest_first(items)
    runs_ = [i.meta["id"] for i in items]
    extraction: dict[str, dict] = {}
    reading: dict[str, dict] = {}
    speed: dict[str, dict] = {}
    for it in items:
        m, rid = it.meta, it.meta["id"]
        s = view.visible(m, it.scores, focus=False)
        for a in s["extraction"]["aggregate"]:
            label = view.pipeline_label(m, a["pipeline"])
            extraction.setdefault(label, {}).setdefault(rid, {})[a["variant"]] = a["field_accuracy"]
        for a in s["transcription"]["aggregate"]:
            model = view.label(m, a["backend"])
            reading.setdefault(model, {}).setdefault(rid, {})[a["variant"]] = (a.get("cer_median"), a["num_recall"])
        for sp in s["speed"]:
            if view.is_selfhosted(m, sp["backend"]):
                b = view.spec(m, sp["backend"])
                row = speed.setdefault(
                    f"{rid}|{sp['backend']}",
                    {
                        "run": rid,
                        "model": view.short_label(m, sp["backend"]),
                        "host": b.get("instance_type") or "?",
                        "by_concurrency": {},
                    },
                )
                row["by_concurrency"].setdefault(sp["concurrency"], sp["pages_per_min"])
    return {"runs": runs_, "extraction": extraction, "reading": reading, "selfhosted_speed": speed}


def _pct(x: float | None) -> str:
    return "–" if x is None else f"{x:.1%}"


def comparison_md(c: dict) -> str:
    runs_ = c["runs"]
    if len(runs_) < 2:
        return ""
    out = [
        "## Compare runs",
        "",
        "The same metric across runs. Rows are matched on the model actually used, and pipelines where one model both reads and extracts are hidden.",
        "",
        "### Field accuracy (clean / degraded)",
        "",
        "| input → extractor | " + " | ".join(r for r in runs_) + " |",
        "|---" * (len(runs_) + 1) + "|",
    ]
    for label, per in sorted(c["extraction"].items()):
        cells = []
        for r in runs_:
            x = per.get(r)
            cells.append("–" if not x else " / ".join(_pct(x.get(v)) for v in ("clean", "degraded")))
        out.append(f"| {label} | " + " | ".join(cells) + " |")
    out += [
        "",
        "### Transcription, degraded pages (CER median / number recall)",
        "",
        "| reader | " + " | ".join(r for r in runs_) + " |",
        "|---" * (len(runs_) + 1) + "|",
    ]
    for model, per in sorted(c["reading"].items()):
        cells = []
        for r in runs_:
            x = (per.get(r) or {}).get("degraded")
            cells.append("–" if not x else f"{x[0]:.3f} / {x[1]:.1%}")
        out.append(f"| {model} | " + " | ".join(cells) + " |")
    rows = c.get("selfhosted_speed") or {}
    if rows:
        concs = sorted({k for r in rows.values() for k in r["by_concurrency"]})
        out += [
            "",
            "### Self-hosted throughput (pages/min by concurrency)",
            "",
            "| run | model | host | " + " | ".join(f"c{k}" for k in concs) + " |",
            "|---" * (3 + len(concs)) + "|",
        ]
        for r in runs_:
            for row in (x for x in rows.values() if x["run"] == r):
                bc = row["by_concurrency"]
                out.append(
                    f"| `{r}` | {row['model']} | {row['host']} | "
                    + " | ".join(f"{bc[k]:.1f}" if k in bc else "–" for k in concs)
                    + " |"
                )
    return "\n".join(out)


# --------------------------------------------------------------------------- shared guide (main page)
def guide_md(latest: dict | None) -> str:
    out = [explain.INTRO, "", "## How the benchmark works", ""]
    out += [f"- **{a}**: {b}" for a, b in explain.STAGES]
    out += [
        "",
        "### Pipelines",
        "",
        "A pipeline is written **input → extractor**. The input is what the extracting model gets to read:",
        "",
        "- **page images**: the extractor looks at the images itself (one step).",
        "- **<self-hosted model> text**: a model on our own GPU first transcribes every page; the extractor gets "
        "only that text.",
        "- **<LLM> text**: an LLM first transcribes every page; a **different** LLM extracts from the text.",
        "",
        "Pipelines where the same model both transcribes and extracts are not shown: they only measure a model "
        "against itself.",
        "",
        "### The extractor",
        "",
        f"Whatever model extracts, it gets {explain.AGENT_INPUT}, and returns "
        f"{explain.agent_output(int(prompts.of(latest or {}, 'extract')['max_tokens']))}. "
        "It never sees the ground truth. Run pages list the exact model and settings.",
        "",
        "### Pages",
        "",
    ]
    out += [f"- **{k}**: {v}" for k, v in explain.VARIANTS.items()]
    if latest:
        out += [
            "",
            "### Document types",
            "",
            "Synthetic Norwegian (bokmål) everyday paperwork with fictional "
            "names and numbers, generated with exact ground truth.",
            "",
            "| type | what it is | hard part | fields extracted |",
            "|---|---|---|---|",
        ]
        for t, d in latest["documents"]["types"].items():
            out.append(f"| `{t}` | {d['description']} | {explain.CHALLENGES.get(t, '')} | {', '.join(d['fields'])} |")
    out += ["", "### Metrics", ""] + [f"- **{k}**: {v}" for k, v in metrics.GLOSSARY]
    return "\n".join(out)
