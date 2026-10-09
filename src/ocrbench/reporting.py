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
        "| role | name in tables | model | model id | served by | price |",
        "|---|---|---|---|---|---|",
    ]
    gpu_price = f"{_usd(o.get('price_per_hour_usd'))}/hour (on-demand)" if o.get("price_per_hour_usd") else "–"
    out.append(
        f"| transcriber (self-hosted) | `ocr` | {explain.ocr_label(o)} | `{o.get('served_model_name')}` | "
        f"vLLM on {o.get('serving', 'SageMaker')} | {gpu_price} |"
    )
    roles: dict[str, list[str]] = {}
    for t in meta.get("transcribers") or []:
        if t != "ocr":
            roles.setdefault(t, []).append("transcriber")
    roles.setdefault(meta["extraction"]["agent"], []).append("extraction agent")
    roles.setdefault(meta.get("summary", {}).get("agent") or meta["extraction"]["agent"], []).append("summary writer")
    for name, rs in roles.items():
        b = meta["llm_backends"].get(name, {})
        p = b.get("price_per_mtok") or {}
        price = f"${p.get('input')} in / ${p.get('output')} out per 1M tokens" if p else "–"
        out.append(
            f"| {', '.join(rs)} | `{name}` | {explain.model_label(b.get('model'))} | `{b.get('model', '?')}` | "
            f"{b.get('via', '?')} | {price} |"
        )
    agent_model = meta["llm_backends"].get(meta["extraction"]["agent"], {}).get("model")
    out += ["", "### The extraction agent", "", explain.agent_note(agent_model)]
    out += [
        "",
        "### GPU",
        "",
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
        "",
        "### Run",
        "",
        "| | |",
        "|---|---|",
        f"| plan | `{meta['plan']}`: {len(meta.get('plan_items') or [])} transcription runs |",
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
        out.append(f"| `{p}` | {explain.pipeline_text(p, meta['ocr'].get('hf_repo', 'ocr'))} |")
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


def unverified(author: str) -> list[str]:
    m = re.search(r"unverified numbers: (.*)$", author or "")
    return [] if not m or m.group(1).strip() == "none" else [x.strip() for x in m.group(1).split(",")]


def read_summary(run_dir: Path) -> tuple[str, str]:
    """(body, author line) of summary.md, or ("", "")."""
    p = run_dir / "summary.md"
    if not p.exists():
        return "", ""
    text = p.read_text(encoding="utf-8")
    m = re.match(r"<!--\s*(.*?)\s*-->\n?", text)
    return (text[m.end() :] if m else text).strip(), (m.group(1) if m else "")


def headline(meta: dict, scores: dict) -> dict:
    """Short facts for the experiments index."""
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
    body, author = read_summary(run_dir)
    findings = (
        ["## Findings", "", f"*{author.split(';')[0]}. It interprets the key facts above.*", ""]
        + (
            [f"> **Unverified numbers** (not found in the key facts): {', '.join(unverified(author))}", ""]
            if unverified(author)
            else []
        )
        + [body]
        if body
        else ["## Findings", "", "*No LLM summary yet. Run the `summarize` stage.*"]
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
    from .report import build_site

    (run_dir / "scores.json").write_text(json.dumps(scores, ensure_ascii=False, indent=1), encoding="utf-8")
    (run_dir / "report.md").write_text(report_md(meta, scores, run_dir), encoding="utf-8")
    from .facts import compute, to_md

    body, author = read_summary(run_dir)
    sections = {
        "facts_md": to_md(compute(meta, scores)),
        "findings": body,
        "findings_author": author.split(";")[0],
        "findings_unverified": unverified(author),
        "setup_md": setup_md(meta),
        "legend_md": legend_md(meta),
    }
    return build_site(run_dir / "docs", run_dir, scores, site_root / meta["id"], sections=sections)
