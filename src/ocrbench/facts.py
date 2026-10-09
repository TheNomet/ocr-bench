"""Decision facts computed from scores, not by the LLM.

The summariser is given these and told to base every comparison on them; the report shows
them as a table, so the key comparisons are correct even if the LLM prose is not.
"""

from __future__ import annotations


def _min(rows, key):
    rows = [r for r in rows if r.get(key) is not None]
    return min(rows, key=lambda r: r[key]) if rows else None


def _max(rows, key):
    rows = [r for r in rows if r.get(key) is not None]
    return max(rows, key=lambda r: r[key]) if rows else None


def compute(meta: dict, scores: dict) -> dict:
    ea, ta, sp = scores["extraction"]["aggregate"], scores["transcription"]["aggregate"], scores["speed"]
    pipelines = {}
    for v in ("clean", "degraded"):
        rows = [a for a in ea if a["variant"] == v]
        if not rows:
            continue
        acc, cheap, fast = (
            _max(rows, "field_accuracy"),
            _min(rows, "cost_per_1k_docs_usd"),
            _min(rows, "end_to_end_latency_s"),
        )
        pipelines[v] = {
            "most_accurate": {"pipeline": acc["pipeline"], "field_accuracy": acc["field_accuracy"]},
            "cheapest": cheap
            and {"pipeline": cheap["pipeline"], "cost_per_1k_docs_usd": cheap["cost_per_1k_docs_usd"]},
            "fastest": fast and {"pipeline": fast["pipeline"], "end_to_end_latency_s": fast["end_to_end_latency_s"]},
            "all": {
                a["pipeline"]: {
                    "field_accuracy": a["field_accuracy"],
                    "end_to_end_latency_s": a["end_to_end_latency_s"],
                    "cost_per_1k_docs_usd": a["cost_per_1k_docs_usd"],
                }
                for a in rows
            },
        }

    reading = {}
    for a in ta:
        reading.setdefault(a["backend"], {})[a["variant"]] = {
            "cer_median": a.get("cer_median"),
            "number_recall": a["num_recall"],
            "runaway_pages": a.get("runaway", 0),
            "invented_numbers_excl_runaway": a.get("num_hallucinated_excl_runaway", a["num_hallucinated"]),
        }

    throughput = {}
    for b in {s["backend"] for s in sp}:
        rows = [s for s in sp if s["backend"] == b]
        c1 = [s for s in rows if s["concurrency"] == 1]
        best = _max(rows, "pages_per_min")
        throughput[b] = {
            "p50_s_at_concurrency_1": c1[0]["p50_s"] if c1 else None,
            "max_pages_per_min": best["pages_per_min"],
            "at_concurrency": best["concurrency"],
        }

    # OCR transcription is cheaper than an LLM transcriber above this many pages/hour (GPU billed even when idle)
    price = (meta.get("ocr") or {}).get("price_per_hour_usd")
    break_even = {}
    if price:
        for s in sp:
            if s["backend"] != "ocr" and s["concurrency"] == 1 and s.get("cost_per_1k_pages_usd"):
                pph = price / s["cost_per_1k_pages_usd"] * 1000
                break_even[s["backend"]] = {
                    "llm_cost_per_1k_pages_usd": s["cost_per_1k_pages_usd"],
                    "ocr_cheaper_above_pages_per_hour": round(pph),
                }
        ocr_max = throughput.get("ocr", {}).get("max_pages_per_min")
        if ocr_max:
            break_even["_ocr_capacity_pages_per_hour"] = round(ocr_max * 60)
    return {
        "pipelines": pipelines,
        "reading": reading,
        "throughput": throughput,
        "break_even": break_even,
        "gpu_price_per_hour_usd": price,
    }


def to_md(f: dict) -> str:
    def pct(x):
        return "–" if x is None else f"{x:.1%}"

    def usd(x):
        return "–" if x is None else f"${x:.2f}"

    out = [
        "## Key facts (computed, not LLM-written)",
        "",
        "| question | clean pages | degraded pages |",
        "|---|---|---|",
    ]
    p = f["pipelines"]
    for label, key, fmt in (
        ("most accurate pipeline", "most_accurate", lambda d: f"`{d['pipeline']}` {pct(d['field_accuracy'])}"),
        ("cheapest pipeline", "cheapest", lambda d: f"`{d['pipeline']}` {usd(d['cost_per_1k_docs_usd'])}/1k docs"),
        ("fastest pipeline", "fastest", lambda d: f"`{d['pipeline']}` {d['end_to_end_latency_s']:.1f} s/doc"),
    ):
        cells = [fmt(p[v][key]) if v in p and p[v].get(key) else "–" for v in ("clean", "degraded")]
        out.append(f"| {label} | {cells[0]} | {cells[1]} |")
    out += [
        "",
        "| reader | variant | CER median | number recall | invented numbers (excl. runaway) | runaway pages |",
        "|---|---|---|---|---|---|",
    ]
    for b, vs in f["reading"].items():
        for v, r in vs.items():
            out.append(
                f"| `{b}` | {v} | {r['cer_median']:.3f} | {pct(r['number_recall'])} | "
                f"{r['invented_numbers_excl_runaway']} | {r['runaway_pages']} |"
            )
    out += ["", "| reader | p50 s/page (one at a time) | max pages/min (at concurrency) |", "|---|---|---|"]
    for b, t in f["throughput"].items():
        p50 = "–" if t["p50_s_at_concurrency_1"] is None else f"{t['p50_s_at_concurrency_1']:.1f}"
        out.append(f"| `{b}` | {p50} | {t['max_pages_per_min']:.0f} ({t['at_concurrency']}) |")
    be = {k: v for k, v in f["break_even"].items() if not k.startswith("_")}
    if be:
        cap = f["break_even"].get("_ocr_capacity_pages_per_hour")
        out += [
            "",
            f"OCR transcription costs {usd(f['gpu_price_per_hour_usd'])}/hour whether busy or idle"
            + (f"; one GPU handled at most ~{cap} pages/hour in this run." if cap else "."),
            "",
        ]
        for b, x in be.items():
            out.append(
                f"- cheaper than `{b}` transcription ({usd(x['llm_cost_per_1k_pages_usd'])}/1k pages) above "
                f"**~{x['ocr_cheaper_above_pages_per_hour']} pages/hour** sustained"
            )
    return "\n".join(out)
