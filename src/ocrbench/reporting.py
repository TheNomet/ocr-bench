"""report.md + site for one run, and the experiments index across runs.

The setup, legend and LLM recommendation are produced as Markdown here, so report.md and the
site show exactly the same information (the site renders it with markdown_lite).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import explain, metrics, prompts, view
from .report.site import RunCard


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
    agent = meta["extraction"]["agent"]
    a = view.spec(meta, agent)
    others = [
        f"`{k}`" for k in dict.fromkeys(view.extractor(meta, p) for p in meta["extraction"]["pipelines"]) if k != agent
    ]
    note = explain.agent_note(
        a.get("model"),
        a.get("temperature", 0.0),
        others,
        label=view.label(meta, agent),
        max_tokens=int(prompts.of(meta, "extract")["max_tokens"]),
    )
    out = ["### The extraction agent", "", note]
    if view.sampling(meta):
        out += [
            "",
            "Models with temperature above 0 sample their output, so their results vary slightly between "
            "runs; differences of a point or two are within noise.",
        ]
    hidden = focus_backends(meta)
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
        misses: dict[str, dict[str, int]] = {}
        for d in scores["extraction"]["docs"]:
            for f, x in d["fields"].items():
                if not x["correct"]:
                    key = f"{d['doc_id'].rsplit('-', 1)[0]}.{f}"
                    misses.setdefault(d["pipeline"], {}).setdefault(key, 0)
                    misses[d["pipeline"]][key] += 1
        if misses:
            out += ["", "Most-missed fields (count over clean + degraded):", ""]
            for p, m in sorted(misses.items()):
                out.append(
                    f"- **{p}**: " + ", ".join(f"`{k}` ({v})" for k, v in sorted(m.items(), key=lambda x: -x[1])[:5])
                )
    return "\n".join(out)


def read_summary(run_dir: Path) -> tuple[str, dict]:
    """(markdown body, info) of the LLM recommendation; info from summary.json.

    Older runs kept the info in an HTML comment on the first line of summary.md; that is still read.
    """
    p = run_dir / "summary.md"
    if not p.exists():
        return "", {}
    text = p.read_text(encoding="utf-8")
    info_p = run_dir / "summary.json"
    if info_p.exists():
        return text.strip(), json.loads(info_p.read_text(encoding="utf-8"))
    m = re.match(r"<!--\s*LLM comment by (\S+) \((.*?)\).*?unverified numbers: (.*?)\s*-->\n?", text)
    if not m:
        return text.strip(), {}
    bad = [] if m.group(3).strip() == "none" else [x.strip() for x in m.group(3).split(",")]
    return text[m.end() :].strip(), {"backend": m.group(1), "model": m.group(2), "unverified_numbers": bad}


def author_line(info: dict) -> str:
    if not info:
        return "LLM-written"
    return f"LLM comment by {info.get('backend')} ({info.get('model')}) on the computed key facts"


def headline(meta: dict, scores: dict) -> dict:
    """Short facts for the experiments index."""
    scores = visible(meta, scores)
    meta = focused_meta(meta)
    ea = scores["extraction"]["aggregate"]
    best = {}
    for v in ("clean", "degraded"):
        rows = [a for a in ea if a["variant"] == v]
        if rows:
            b = max(rows, key=lambda a: a["field_accuracy"])
            best[v] = {"pipeline": b["pipeline"], "field_accuracy": b["field_accuracy"]}
    pipes: dict[str, dict] = {}
    for a in ea:
        pipes.setdefault(a["pipeline"], {})[a["variant"]] = {
            "acc": a["field_accuracy"],
            "cost": a["cost_per_1k_docs_usd"],
            "s": a["end_to_end_latency_s"],
        }
    return {"best": best, "pipelines": pipes}


def report_md(meta: dict, scores: dict, run_dir: Path) -> str:
    scores = visible(meta, scores)
    meta = focused_meta(meta)
    body, info = read_summary(run_dir)
    bad = info.get("unverified_numbers") or []
    recommendation = (
        ["## Recommendation", "", f"*{author_line(info)}. It interprets the key facts above.*", ""]
        + ([f"> **Unverified numbers** (not found in the key facts): {', '.join(bad)}", ""] if bad else [])
        + [body]
        if body
        else ["## Recommendation", "", "*No LLM recommendation yet. Run the `summarize` stage.*"]
    )
    from .facts import compute, to_md

    parts = [
        f"# ocr-bench run `{meta['id']}`",
        "",
        to_md(compute(meta, scores)),
        "\n".join(recommendation),
        setup_md(meta),
        legend_md(meta),
        results_md(scores),
    ]
    return "\n\n".join(parts) + "\n"


def write(meta: dict, scores: dict, run_dir: Path, site_root: Path) -> Path:
    from .fileio import write_if_changed
    from .report import RunPage, build_site

    write_if_changed(run_dir / "scores.json", json.dumps(scores, ensure_ascii=False, indent=1))
    write_if_changed(run_dir / "report.md", report_md(meta, scores, run_dir))
    from .facts import compute, to_md

    body, info = read_summary(run_dir)
    vis = visible(meta, scores)
    meta = focused_meta(meta)
    page = RunPage(
        facts_md=to_md(compute(meta, vis)),
        recommendation=body,
        recommendation_author=author_line(info),
        unverified_numbers=info.get("unverified_numbers") or [],
        setup_md=setup_md(meta),
        plan_description=meta.get("plan_description", ""),
        hide_backends=sorted(focus_backends(meta)),
        pipeline_labels={p: view.pipeline_label(meta, p) for p in meta["extraction"]["pipelines"]},
    )
    return build_site(run_dir / "docs", run_dir, vis, site_root / meta["id"], page)


def bottom_line(run_dir: Path) -> str:
    """First section of the recommendation ("## Bottom line"), for the experiments index."""
    body, _ = read_summary(run_dir)
    _, sep, rest = body.partition("## Bottom line")
    return rest.split("\n## ", 1)[0].strip() if sep else ""


def run_card(meta: dict, scores: dict, run_dir: Path) -> RunCard:
    return RunCard(
        meta=meta,
        card_meta=focused_meta(meta),
        scores=scores,
        headline=headline(meta, scores),
        bottom_line=bottom_line(run_dir),
    )


def comparison(items: list[RunCard]) -> dict:
    """Cross-run tables over runs, newest first.

    Rows are keyed by what was actually measured (pipeline + extractor model id, or backend + model id),
    so a renamed config key never silently compares different models.
    """
    runs_ = [i.meta["id"] for i in items]
    extraction: dict[str, dict] = {}
    reading: dict[str, dict] = {}
    speed: dict[str, dict] = {}
    for it in items:
        m, rid = it.meta, it.meta["id"]
        s = visible(m, it.scores, focus=False)
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
            cells.append("–" if not x else f"{x.get('clean', 0):.1%} / {x.get('degraded', 0):.1%}")
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
def focus_backends(meta: dict) -> set[str]:
    """Backends a run reports on but doesn't focus on (run.json `report.hide_backends`): e.g. models
    re-run only as a reproducibility check. Their data stays on disk and in the cross-run comparison."""
    return set((meta.get("report") or {}).get("hide_backends") or [])


def focused_meta(meta: dict) -> dict:
    """run.json as a run page should describe it: hidden backends removed from transcribers and pipelines,
    and the default agent replaced by a shown extractor if it was hidden."""
    hidden = focus_backends(meta)
    ex = dict(meta.get("extraction") or {})
    agents = ex.get("agents") or {}
    keep = [
        p
        for p in ex.get("pipelines", [])
        if p.split("->")[0] not in hidden
        and agents.get(p, ex.get("agent")) not in hidden
        and not view.same_model(meta, p)  # hidden everywhere: one model reading and extracting
    ]
    ex["pipelines"] = keep
    if ex.get("agent") in hidden:
        shown = [agents.get(p) for p in keep if agents.get(p) and agents.get(p) not in hidden]
        ex["agent"] = shown[0] if shown else ex.get("agent")
    ex["agents"] = {p: agents.get(p, ex["agent"]) for p in keep}
    return {
        **meta,
        "extraction": ex,
        "transcribers": [t for t in meta.get("transcribers") or [] if t not in hidden],
        "plan_items": [i for i in meta.get("plan_items") or [] if i.get("backend") not in hidden],
    }


def visible(meta: dict, scores: dict, focus: bool = True) -> dict:
    """Scores as shown on a run page.

    Always hides `X->X` pipelines (same model reading and extracting). With focus=True, also hides
    everything involving a backend in `report.hide_backends` (as transcriber or extractor).
    """
    hidden_be = focus_backends(meta) if focus else set()

    def pipe_hidden(p: str) -> bool:
        src = p.split("->")[0]
        return view.same_model(meta, p) or src in hidden_be or view.extractor(meta, p) in hidden_be

    ex = scores.get("extraction") or {}
    tr = scores.get("transcription") or {}
    return {
        **scores,
        "speed": [r for r in scores.get("speed") or [] if r["backend"] not in hidden_be],
        "transcription": {
            "aggregate": [a for a in tr.get("aggregate") or [] if a["backend"] not in hidden_be],
            "pages": [p for p in tr.get("pages") or [] if p["backend"] not in hidden_be],
        },
        "extraction": {
            "aggregate": [a for a in ex.get("aggregate") or [] if not pipe_hidden(a["pipeline"])],
            "docs": [d for d in ex.get("docs") or [] if not pipe_hidden(d["pipeline"])],
        },
    }


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
