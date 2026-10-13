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


def _pct(x) -> str:
    return "–" if x is None else f"{x:.1%}"


def _usd(x) -> str:
    return "–" if x is None else f"${x:.2f}"


# --------------------------------------------------------------------------- compute
def pipeline_facts(ea: list[dict]) -> dict:
    """Per variant: most accurate, cheapest and fastest pipeline, which ones have no price, and every pipeline."""
    out = {}
    for v in ("clean", "degraded"):
        rows = [a for a in ea if a["variant"] == v]
        acc = _max(rows, "field_accuracy")
        if acc is None:
            continue
        cheap, fast = _min(rows, "cost_per_1k_docs_usd"), _min(rows, "end_to_end_latency_s")
        out[v] = {
            "unpriced": sorted(a["pipeline"] for a in rows if a.get("cost_per_1k_docs_usd") is None),
            "most_accurate": {"pipeline": acc["pipeline"], "field_accuracy": acc["field_accuracy"]},
            "cheapest": cheap
            and {"pipeline": cheap["pipeline"], "cost_per_1k_docs_usd": cheap["cost_per_1k_docs_usd"]},
            "fastest": fast and {"pipeline": fast["pipeline"], "end_to_end_latency_s": fast["end_to_end_latency_s"]},
            "all": {
                a["pipeline"]: {k: a[k] for k in ("field_accuracy", "end_to_end_latency_s", "cost_per_1k_docs_usd")}
                for a in rows
            },
        }
    return out


def reading_facts(ta: list[dict]) -> dict:
    out: dict[str, dict] = {}
    for a in ta:
        out.setdefault(a["backend"], {})[a["variant"]] = {
            "cer_median": a.get("cer_median"),
            "number_recall": a["num_recall"],
            "runaway_pages": a.get("runaway", 0),
            "invented_numbers_excl_runaway": a.get("num_hallucinated_excl_runaway", a["num_hallucinated"]),
        }
    return out


def throughput_facts(sp: list[dict]) -> dict:
    out = {}
    for b in dict.fromkeys(s["backend"] for s in sp):  # plan order, deterministic
        rows = [s for s in sp if s["backend"] == b]
        c1 = [s for s in rows if s["concurrency"] == 1]
        best = _max(rows, "pages_per_min")
        if best is None:
            continue
        out[b] = {
            "p50_s_at_concurrency_1": c1[0]["p50_s"] if c1 else None,
            "max_pages_per_min": best["pages_per_min"],
            "at_concurrency": best["concurrency"],
        }
    return out


def selfhosted_facts(meta: dict, sp: list[dict], throughput: dict, basis: dict) -> dict:
    """Per self-hosted backend: GPU price, capacity, cost basis, and the volume above which it is cheaper
    than each token-priced transcriber (its GPU is billed per hour, busy or idle)."""
    be = meta["backends"]
    out = {}
    for name in sorted({s["backend"] for s in sp if (be.get(s["backend"]) or {}).get("kind") == "selfhosted"}):
        price = be[name].get("price_per_hour_usd")
        if not price:
            continue
        break_even = {
            s["backend"]: {
                "llm_cost_per_1k_pages_usd": s["cost_per_1k_pages_usd"],
                "cheaper_above_pages_per_hour": round(price / s["cost_per_1k_pages_usd"] * 1000),
            }
            for s in sp
            if (be.get(s["backend"]) or {}).get("kind") != "selfhosted"
            and s["concurrency"] == 1
            and s.get("cost_per_1k_pages_usd")
        }
        cap = throughput.get(name, {}).get("max_pages_per_min")
        out[name] = {
            "gpu_price_per_hour_usd": price,
            "instance_type": be[name].get("instance_type"),
            "capacity_pages_per_hour": round(cap * 60) if cap else None,
            "cost_basis": basis.get(name),
            "break_even": break_even,
        }
    return out


def most_missed(docs: list[dict], n: int = 4) -> dict[str, list[tuple[str, int]]]:
    misses: dict[str, dict[str, int]] = {}
    for d in docs:
        for fld, x in d["fields"].items():
            if not x["correct"]:
                k = f"{d['doc_id'].rsplit('-', 1)[0]}.{fld}"
                misses.setdefault(d["pipeline"], {}).setdefault(k, 0)
                misses[d["pipeline"]][k] += 1
    return {p: sorted(m.items(), key=lambda x: -x[1])[:n] for p, m in sorted(misses.items())}


def compute(meta: dict, scores: dict) -> dict:
    sp = scores["speed"]
    throughput = throughput_facts(sp)
    basis = (scores.get("method") or {}).get("cost_basis") or {}
    return {
        "pipelines": pipeline_facts(scores["extraction"]["aggregate"]),
        "reading": reading_facts(scores["transcription"]["aggregate"]),
        "throughput": throughput,
        "selfhosted": selfhosted_facts(meta, sp, throughput, basis),
        "most_missed": most_missed(scores["extraction"]["docs"]),
    }


# --------------------------------------------------------------------------- markdown
def _name(pipe: str, labels: dict[str, str]) -> str:
    """A pipeline as Markdown: its readable label (view.pipeline_label) with the raw key, or just the key."""
    lbl = labels.get(pipe)
    return f"{lbl} (`{pipe}`)" if lbl and lbl != pipe else f"`{pipe}`"


def _md_winners(p: dict, labels: dict[str, str]) -> list[str]:
    out = ["| question | clean pages | degraded pages |", "|---|---|---|"]

    def n(d: dict) -> str:
        return _name(d["pipeline"], labels)

    for label, key, fmt in (
        ("most accurate pipeline", "most_accurate", lambda d: f"{n(d)}: {_pct(d['field_accuracy'])}"),
        ("cheapest pipeline", "cheapest", lambda d: f"{n(d)}: {_usd(d['cost_per_1k_docs_usd'])}/1k docs"),
        ("fastest pipeline", "fastest", lambda d: f"{n(d)}: {d['end_to_end_latency_s']:.1f} s/doc"),
    ):
        cells = [fmt(p[v][key]) if v in p and p[v].get(key) else "–" for v in ("clean", "degraded")]
        if key == "cheapest" and any(p[v].get("unpriced") for v in p):
            label = "cheapest pipeline (among priced, see note)"
        out.append(f"| {label} | {cells[0]} | {cells[1]} |")
    unpriced = sorted({x for v in p for x in p[v].get("unpriced", [])})
    if unpriced:
        out += [
            "",
            "**Cost note:** no price is configured for "
            + ", ".join(f"`{x}`" for x in unpriced)
            + ", so they are left out of the cheapest-pipeline comparison. Set `price_per_mtok` for those "
            "backends to include them.",
        ]
    return out


def _md_every_pipeline(p: dict, labels: dict[str, str]) -> list[str]:
    names = sorted({n for v in p.values() for n in v.get("all", {})})
    if not names:
        return []
    out = [
        "",
        "Every pipeline (field accuracy, time per document, cost per 1,000 documents):",
        "",
        "| pipeline | clean accuracy | degraded accuracy | s/doc (clean) | $/1k docs (clean) |",
        "|---|---|---|---|---|",
    ]
    for n in names:
        c = (p.get("clean") or {}).get("all", {}).get(n, {})
        dg = (p.get("degraded") or {}).get("all", {}).get(n, {})
        sec = c.get("end_to_end_latency_s")
        out.append(
            f"| {_name(n, labels)} | {_pct(c.get('field_accuracy'))} | {_pct(dg.get('field_accuracy'))} | "
            f"{'–' if sec is None else f'{sec:.1f}'} | {_usd(c.get('cost_per_1k_docs_usd'))} |"
        )
    return out


def _md_readers(reading: dict, throughput: dict) -> list[str]:
    out = [
        "",
        "| reader | variant | CER median | number recall | invented numbers (excl. runaway) | runaway pages |",
        "|---|---|---|---|---|---|",
    ]
    for b, vs in reading.items():
        for v, r in vs.items():
            out.append(
                f"| `{b}` | {v} | {r['cer_median']:.3f} | {_pct(r['number_recall'])} | "
                f"{r['invented_numbers_excl_runaway']} | {r['runaway_pages']} |"
            )
    out += ["", "| reader | p50 s/page (one at a time) | max pages/min (at concurrency) |", "|---|---|---|"]
    for b, t in throughput.items():
        p50 = "–" if t["p50_s_at_concurrency_1"] is None else f"{t['p50_s_at_concurrency_1']:.1f}"
        out.append(f"| `{b}` | {p50} | {t['max_pages_per_min']:.0f} ({t['at_concurrency']}) |")
    return out


def _md_selfhosted(name: str, x: dict) -> list[str]:
    cap = x.get("capacity_pages_per_hour")
    out = [
        "",
        f"`{name}` runs on its own GPU ({x.get('instance_type') or '?'}), which costs "
        f"{_usd(x['gpu_price_per_hour_usd'])}/hour whether busy or idle"
        + (f"; it handled at most ~{cap} pages/hour in this run." if cap else "."),
        "",
    ]
    cb = x.get("cost_basis") or {}
    if cb.get("cost_per_1k_pages_usd") is not None:
        c1 = cb.get("cost_at_concurrency_1")
        out += [
            f"Pipeline costs that include `{name}` use {_usd(cb['cost_per_1k_pages_usd'])}/1k pages: the cost at "
            f"its {cb['basis']}"
            + (f". One request at a time it would be {_usd(c1)}/1k pages." if c1 is not None else "."),
            "",
        ]
    for b, y in x["break_even"].items():
        out.append(
            f"- `{name}` is cheaper than `{b}` transcription ({_usd(y['llm_cost_per_1k_pages_usd'])}/1k pages) "
            f"above **~{y['cheaper_above_pages_per_hour']} pages/hour** sustained"
        )
    return out


def to_md(f: dict, labels: dict[str, str] | None = None) -> str:
    """The key facts as Markdown. `labels`: readable pipeline names (view.pipeline_labels), shown with the raw key."""
    labels = labels or {}
    out = ["## Key facts (computed, not LLM-written)", ""]
    out += _md_winners(f["pipelines"], labels)
    out += _md_every_pipeline(f["pipelines"], labels)
    out += _md_readers(f["reading"], f["throughput"])
    for name, x in (f.get("selfhosted") or {}).items():
        out += _md_selfhosted(name, x)
    mm = f.get("most_missed") or {}
    if mm:
        out += ["", "Fields missed most often (count over clean + degraded documents):", ""]
        out += [f"- {_name(p, labels)}: " + ", ".join(f"`{k}` ({n})" for k, n in items) for p, items in mm.items()]
    return "\n".join(out)
