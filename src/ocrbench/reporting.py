"""Write report.md (deterministic summary) and the static site from scores."""

from __future__ import annotations

import json
from pathlib import Path


def _f(v, fmt="{:.2f}", none="–"):
    return none if v is None else fmt.format(v)


def summary_md(scores: dict) -> str:
    out = []
    speed = scores["speed"]
    if speed:
        out += [
            "## Speed",
            "",
            "| backend | conc | pages | ok | pages/min | p50 s | p95 s | $ / 1k pages |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for s in speed:
            out.append(
                f"| {s['backend']} | {s['concurrency']} | {s['pages']} | {s['ok']} | {s['pages_per_min']:.1f} | "
                f"{_f(s['p50_s'])} | {_f(s['p95_s'])} | {_f(s['cost_per_1k_pages_usd'])} |"
            )
        out.append("")
    ta = scores["transcription"]["aggregate"]
    if ta:
        out += [
            "## Transcription quality",
            "",
            "Runaway = output more than 3x the page's length (repetition loop). One runaway page dominates "
            "the mean CER, so the median is shown too.",
            "",
            "| backend | variant | pages | CER mean | CER median | runaway | word recall | number recall | missing nums | invented nums |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for a in ta:
            out.append(
                f"| {a['backend']} | {a['variant']} | {a['pages']} | {a['cer']:.3f} | {a.get('cer_median', 0):.3f} | "
                f"{a.get('runaway', 0)} | {a['word_recall']:.1%} | "
                f"{a['num_recall']:.1%} | {a['num_missing']} | {a['num_hallucinated']} |"
            )
        out.append("")
    ea = scores["extraction"]["aggregate"]
    if ea:
        out += [
            "## Field extraction",
            "",
            "| pipeline | variant | docs | field accuracy | agent s | end-to-end s | $ / 1k docs |",
            "|---|---|---|---|---|---|---|",
        ]
        for a in ea:
            out.append(
                f"| {a['pipeline']} | {a['variant']} | {a['docs']} | {a['field_accuracy']:.1%} | "
                f"{a['mean_latency_s']:.2f} | {a['end_to_end_latency_s']:.2f} | {_f(a['cost_per_1k_docs_usd'])} |"
            )
        # most-missed fields per pipeline
        misses: dict[str, dict[str, int]] = {}
        for d in scores["extraction"]["docs"]:
            for f, x in d["fields"].items():
                if not x["correct"]:
                    key = f"{d['doc_id'].rsplit('-', 1)[0]}.{f}"
                    misses.setdefault(d["pipeline"], {}).setdefault(key, 0)
                    misses[d["pipeline"]][key] += 1
        if misses:
            out += ["", "Most-missed fields:", ""]
        for p, m in sorted(misses.items()):
            top = ", ".join(f"`{k}` ({v})" for k, v in sorted(m.items(), key=lambda x: -x[1])[:5])
            out.append(f"- **{p}**: {top}")
        out.append("")
    return "\n".join(out)


def write(scores: dict, docs: Path, run_dir: Path, site_dir: Path, findings_md: str = "") -> Path:
    from .report import build_site

    scores["summary_md"] = (findings_md.strip() + "\n\n" if findings_md.strip() else "") + summary_md(scores)
    (run_dir / "scores.json").write_text(json.dumps(scores, ensure_ascii=False, indent=1), encoding="utf-8")
    (run_dir / "report.md").write_text(f"# ocr-bench — {scores['tag']}\n\n{scores['summary_md']}", encoding="utf-8")
    return build_site(docs, run_dir, scores, site_dir)
