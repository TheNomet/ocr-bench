"""report.md + site for one run, and the experiments index across runs.

The setup, legend and LLM findings are produced as Markdown here, so report.md and the
site show exactly the same information (the site renders it with markdown_lite).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import explain


def _f(v, fmt="{:.2f}", none="–"):
    return none if v is None else fmt.format(v)


def _usd(v):
    return _f(v, "${:.2f}")


# --------------------------------------------------------------------------- setup + legend
def setup_md(meta: dict) -> str:
    o, docs = meta["ocr"], meta["documents"]
    out = [
        "## Setup",
        "",
        "### Models",
        "",
        "| role | name in tables | model | model id | served by | temperature | price |",
        "|---|---|---|---|---|---|---|",
    ]
    gpu_price = f"{_usd(o.get('price_per_hour_usd'))}/hour (on-demand)" if o.get("price_per_hour_usd") else "–"
    uses_ocr = "ocr" in (meta.get("transcribers") or [])
    if uses_ocr:
        out.append(
            f"| transcriber (self-hosted) | `ocr` | {explain.ocr_label(o)} | `{o.get('served_model_name')}` | "
            f"vLLM on {o.get('serving', 'SageMaker')} | {o.get('request_temperature', 0):g} | {gpu_price} |"
        )
    roles: dict[str, list[str]] = {}
    for t in meta.get("transcribers") or []:
        if t != "ocr":
            roles.setdefault(t, []).append("transcriber")
    agents = meta["extraction"].get("agents") or {}
    roles.setdefault(meta["extraction"]["agent"], []).append("extraction agent (default)")
    for k in dict.fromkeys(v for v in agents.values() if v != meta["extraction"]["agent"]):
        roles.setdefault(k, []).append("extractor")
    roles.setdefault(meta.get("summary", {}).get("agent") or meta["extraction"]["agent"], []).append("summary writer")
    for name, rs in roles.items():
        b = meta["llm_backends"].get(name, {})
        p = b.get("price_per_mtok") or {}
        price = f"${p.get('input')} in / ${p.get('output')} out per 1M tokens" if p else "–"
        temp = b.get("temperature", 0.0)
        out.append(
            f"| {', '.join(rs)} | `{name}` | {explain.model_label(b.get('model'))} | `{b.get('model', '?')}` | "
            f"{b.get('via', '?')} | {'default' if temp is None else f'{temp:g}'} | {price} |"
        )
    a = meta["llm_backends"].get(meta["extraction"]["agent"], {})
    others = [f"`{k}`" for k in dict.fromkeys(v for v in agents.values() if v != meta["extraction"]["agent"])]
    out += ["", "### The extraction agent", "", explain.agent_note(a.get("model"), a.get("temperature", 0.0), others)]
    if any((b.get("temperature") or 0) > 0 for b in meta["llm_backends"].values()):
        out += [
            "",
            "Models with temperature above 0 sample their output, so their results vary slightly between "
            "runs; differences of a point or two are within noise.",
        ]
    gpu_rows = [
        "| | |",
        "|---|---|",
        f"| instance | `{o.get('instance_type') or 'unknown'}` (picked from pools: {', '.join(o.get('instance_pools') or []) or '–'}) |",
        f"| GPU | {o.get('gpu', '?')} |",
        f"| host | {o.get('host', '?')} |",
        f"| price | {gpu_price}, billed while the endpoint exists, busy or idle |",
        f"| region | {meta.get('region')} |",
        f"| host image | `{o.get('inference_ami_version') or '–'}` |",
        f"| container | `{o.get('source_image')}` (digest `{(o.get('image_digest') or '–')[:19]}`) |",
        f"| serving flags | `{' '.join(o.get('serve_args') or [])}` |",
    ]
    hidden = focus_backends(meta)
    if hidden:
        out += [
            "",
            "Also re-run as a reproducibility check, shown only in *Compare runs* on the main page: "
            + ", ".join(f"`{h}`" for h in sorted(hidden))
            + ".",
        ]
    out += ["", "### GPU", ""] + (gpu_rows if uses_ocr else ["No self-hosted model in this run, so no GPU was used."])
    out += [
        "",
        "### Run",
        "",
        "| | |",
        "|---|---|",
        f"| plan | `{meta['plan']}`: {meta.get('plan_description') or ''} ({len(meta.get('plan_items') or [])} transcription runs) |",
        f"| documents | {docs['count']} documents, {docs['pages']} pages, {docs['page_images']} page images (clean + degraded), seed {docs.get('seed')} |",
        f"| runner | {meta['runner'].get('mode')} ({meta['runner'].get('cpu')} CPU units / {meta['runner'].get('memory')} MB) |",
        f"| code | git `{meta.get('git_commit') or '?'}` |",
        f"| started | {meta.get('created')} |",
    ]
    items = meta.get("plan_items") or []
    if items:
        out += ["", "Transcription runs in this plan:", ""]
        for it in items:
            extra = ", ".join(f"{k} {v}" for k, v in it.items() if k not in ("backend", "concurrency"))
            out.append(
                f"- `{it['backend']}` at concurrency {it.get('concurrency', 1)}" + (f" ({extra})" if extra else "")
            )
    return "\n".join(out)


def legend_md(meta: dict) -> str:
    out = ["## How to read this report", "", "### Stages", ""]
    out += [f"- **{a}**: {b}" for a, b in explain.STAGES]
    out += ["", "### Pipelines (the arrows)", "", explain.ARROW, "", "| pipeline | what the agent reads |", "|---|---|"]
    for p in meta["extraction"]["pipelines"]:
        ext = (meta["extraction"].get("agents") or {}).get(p) or meta["extraction"]["agent"]
        label = explain.model_label(meta["llm_backends"].get(ext, {}).get("model"))
        out.append(f"| `{p}` | {explain.pipeline_text(p, meta['ocr'].get('hf_repo', 'ocr'), f'`{ext}` ({label})')} |")
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
    ] + [f"- **{k}**: {v}" for k, v in explain.METRICS]
    return "\n".join(out)


# --------------------------------------------------------------------------- result tables (markdown)
def results_md(scores: dict) -> str:
    out = ["## Results"]
    speed = scores["speed"]
    if speed:
        out += [
            "",
            "### Speed and cost (stage 1)",
            "",
            "| backend | conc | pages | ok | pages/min | p50 s | p95 s | $ / 1k pages |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for s in speed:
            out.append(
                f"| {s['backend']} | {s['concurrency']} | {s['pages']} | {s['ok']} | {s['pages_per_min']:.1f} | "
                f"{_f(s['p50_s'])} | {_f(s['p95_s'])} | {_f(s['cost_per_1k_pages_usd'])} |"
            )
    ta = scores["transcription"]["aggregate"]
    if ta:
        out += [
            "",
            "### Transcription quality (stage 1)",
            "",
            "| backend | variant | pages | CER mean | CER median | runaway | word recall | number recall | missing nums | invented nums |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for a in ta:
            out.append(
                f"| {a['backend']} | {a['variant']} | {a['pages']} | {a['cer']:.3f} | {a.get('cer_median', 0):.3f} | "
                f"{a.get('runaway', 0)} | {a['word_recall']:.1%} | {a['num_recall']:.1%} | {a['num_missing']} | "
                f"{a['num_hallucinated']} |"
            )
    ea = scores["extraction"]["aggregate"]
    if ea:
        out += [
            "",
            "### Field extraction (stage 2)",
            "",
            "| pipeline | variant | docs | field accuracy | agent s | end-to-end s | $ / 1k docs |",
            "|---|---|---|---|---|---|---|",
        ]
        for a in ea:
            out.append(
                f"| {a['pipeline']} | {a['variant']} | {a['docs']} | {a['field_accuracy']:.1%} | "
                f"{a['mean_latency_s']:.2f} | {a['end_to_end_latency_s']:.2f} | {_f(a['cost_per_1k_docs_usd'])} |"
            )
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
    ocr_rows = [s for s in scores["speed"] if s["backend"] == "ocr"]
    c1 = [s for s in ocr_rows if s["concurrency"] == 1]
    pipes: dict[str, dict] = {}
    for a in ea:
        pipes.setdefault(a["pipeline"], {})[a["variant"]] = {
            "acc": a["field_accuracy"],
            "cost": a["cost_per_1k_docs_usd"],
            "s": a["end_to_end_latency_s"],
        }
    return {
        "best": best,
        "pipelines": pipes,
        "ocr_p50_s": c1[0]["p50_s"] if c1 else None,
        "ocr_max_pages_per_min": max((s["pages_per_min"] for s in ocr_rows), default=None),
    }


def report_md(meta: dict, scores: dict, run_dir: Path) -> str:
    scores = visible(meta, scores)
    meta = focused_meta(meta)
    body, info = read_summary(run_dir)
    bad = info.get("unverified_numbers") or []
    findings = (
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
        "\n".join(findings),
        setup_md(meta),
        legend_md(meta),
        results_md(scores),
    ]
    return "\n\n".join(parts) + "\n"


def write(meta: dict, scores: dict, run_dir: Path, site_root: Path) -> Path:
    from .fileio import write_if_changed
    from .report import build_site

    write_if_changed(run_dir / "scores.json", json.dumps(scores, ensure_ascii=False, indent=1))
    write_if_changed(run_dir / "report.md", report_md(meta, scores, run_dir))
    from .facts import compute, to_md

    body, info = read_summary(run_dir)
    vis = visible(meta, scores)
    meta = focused_meta(meta)
    sections = {
        "facts_md": to_md(compute(meta, vis)),
        "findings": body,
        "findings_author": author_line(info),
        "findings_unverified": info.get("unverified_numbers") or [],
        "setup_md": setup_md(meta),
        "plan_description": meta.get("plan_description", ""),
        "hide_backends": sorted(focus_backends(meta)),
        "pipeline_labels": {p: explain.pipeline_label(meta, p) for p in meta["extraction"]["pipelines"]},
    }
    return build_site(run_dir / "docs", run_dir, vis, site_root / meta["id"], sections=sections)


def comparison(items: list[dict]) -> dict:
    """Cross-run tables. items: [{"meta":..., "scores":...}] newest first.

    Rows are keyed by what was actually measured (pipeline + extractor model id, or backend + model id),
    so a renamed config key never silently compares different models.
    """
    from . import explain

    runs_ = [i["meta"]["id"] for i in items]
    extraction: dict[str, dict] = {}
    reading: dict[str, dict] = {}
    speed: dict[str, dict] = {}
    for it in items:
        m, rid = it["meta"], it["meta"]["id"]
        s = visible(m, it["scores"], focus=False)
        be = m.get("llm_backends") or {}
        for a in s["extraction"]["aggregate"]:
            label = explain.pipeline_label(m, a["pipeline"])
            extraction.setdefault(label, {}).setdefault(rid, {})[a["variant"]] = a["field_accuracy"]
        for a in s["transcription"]["aggregate"]:
            model = (
                explain.ocr_label(m["ocr"])
                if a["backend"] == "ocr"
                else explain.model_label(be.get(a["backend"], {}).get("model"))
            )
            reading.setdefault(model, {}).setdefault(rid, {})[a["variant"]] = (a.get("cer_median"), a["num_recall"])
        for sp in s["speed"]:
            if sp["backend"] == "ocr":
                key = f"ocr on {m['ocr'].get('instance_type')}"
                speed.setdefault(rid, {}).setdefault(sp["concurrency"], sp["pages_per_min"])
                speed[rid]["_host"] = key
    return {"runs": runs_, "extraction": extraction, "reading": reading, "ocr_speed": speed}


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
    if c["ocr_speed"]:
        out += [
            "",
            "### OCR throughput (pages/min by concurrency)",
            "",
            "| run | host | c1 | c4 | c16 | c32 |",
            "|---|---|---|---|---|---|",
        ]
        for r in runs_:
            sp = c["ocr_speed"].get(r)
            if sp:
                out.append(
                    f"| `{r}` | {sp['_host']} | "
                    + " | ".join(f"{sp[k]:.1f}" if k in sp else "–" for k in (1, 4, 16, 32))
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
    if not hidden:
        return meta
    ex = dict(meta.get("extraction") or {})
    agents = ex.get("agents") or {}
    keep = [
        p
        for p in ex.get("pipelines", [])
        if p.split("->")[0] not in hidden and agents.get(p, ex.get("agent")) not in hidden
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
        return explain.same_model(meta, p) or src in hidden_be or explain._extractor(meta, p) in hidden_be

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
        "- **<OCR model> text**: our self-hosted OCR model first transcribes every page; the extractor gets only "
        "that text.",
        "- **<LLM> text**: an LLM first transcribes every page; a **different** LLM extracts from the text.",
        "",
        "Pipelines where the same model both transcribes and extracts are not shown: they only measure a model "
        "against itself.",
        "",
        "### The extractor",
        "",
        f"Whatever model extracts, it gets {explain.AGENT_INPUT}, and returns {explain.AGENT_OUTPUT}. "
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
    out += ["", "### Metrics", ""] + [f"- **{k}**: {v}" for k, v in explain.METRICS]
    return "\n".join(out)
