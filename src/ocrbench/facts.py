"""Decision facts computed from scores, not by the LLM.

The summariser is given these and told to base every comparison on them; the report shows
them as a table, so the key comparisons are correct even if the LLM prose is not.
"""

from __future__ import annotations

from .score import RANKING_MIN_COVERAGE as MIN_COVERAGE  # default for scores that don't record theirs


def _min(rows, key):
    rows = [r for r in rows if r.get(key) is not None]
    return min(rows, key=lambda r: r[key]) if rows else None


def _max(rows, key):
    rows = [r for r in rows if r.get(key) is not None]
    return max(rows, key=lambda r: r[key]) if rows else None


def _pct(x) -> str:
    return "–" if x is None else f"{x:.1%}"


def _coverage(a: dict) -> float:
    c = a.get("coverage")
    return 1.0 if c is None else float(c)  # scores from before coverage was recorded


def _usd(x) -> str:
    return "–" if x is None else f"${x:.2f}"


# --------------------------------------------------------------------------- compute
def pipeline_facts(ea: list[dict], min_coverage: float = MIN_COVERAGE) -> dict:
    """Per variant: most accurate, cheapest and fastest pipeline, which ones have no cost, and every pipeline.

    Cheapest and fastest only compare pipelines with coverage >= `min_coverage`: a pipeline that mostly failed
    is cheap and quick because it did little. If none qualifies, all are compared and `coverage_fallback` says so.
    Most accurate needs no filter: failed documents already score 0."""
    out = {}
    for v in ("clean", "degraded"):
        rows = [a for a in ea if a["variant"] == v]
        acc = _max(rows, "field_accuracy")
        if acc is None:
            continue
        low = sorted(a["pipeline"] for a in rows if _coverage(a) < min_coverage)
        ranked = [a for a in rows if a["pipeline"] not in low]
        fallback = not ranked
        ranked = ranked or rows
        cheap, fast = _min(ranked, "cost_per_1k_docs_usd"), _min(ranked, "end_to_end_latency_s")
        out[v] = {
            "ranking_min_coverage": min_coverage,
            "low_coverage": low,
            "coverage_fallback": fallback,
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


def throughput_facts(sp: list[dict], basis: dict | None = None) -> dict:
    """Per backend: p50 at concurrency 1 and its top speed. For a backend whose cost basis names a run
    (self-hosted: the sustained run), that run, so capacity and cost per page describe the same run."""
    basis = basis or {}
    out = {}
    for b in dict.fromkeys(s["backend"] for s in sp):  # plan order, deterministic
        rows = [s for s in sp if s["backend"] == b]
        c1 = [s for s in rows if s["concurrency"] == 1]
        conc = (basis.get(b) or {}).get("concurrency")
        best = next((s for s in rows if conc is not None and s["concurrency"] == conc), None)
        best = best or _max(rows, "pages_per_min")
        if best is None:
            continue
        out[b] = {
            "p50_s_at_concurrency_1": c1[0]["p50_s"] if c1 else None,
            "max_pages_per_min": best["pages_per_min"],
            "at_concurrency": best["concurrency"],
        }
        if conc is not None:
            out[b]["meets_error_threshold"] = (basis.get(b) or {}).get("meets_error_threshold", True)
    return out


def selfhosted_facts(meta: dict, sp: list[dict], throughput: dict, basis: dict) -> dict:
    """Per self-hosted backend: GPU price, capacity, cost basis, and the volume above which it is cheaper
    than each token-priced transcriber (its GPU is billed per hour, busy or idle). The LLM side uses the
    LLM's cost basis, the same figure pipeline totals use. A volume above what one GPU handled can't be
    reached on one GPU: the self-hosted model is then not cheaper at any volume."""
    be = meta["backends"]
    out = {}
    llms = dict.fromkeys(
        s["backend"] for s in sp if (be.get(s["backend"]) or {}).get("kind") not in (None, "selfhosted")
    )
    for name in sorted({s["backend"] for s in sp if (be.get(s["backend"]) or {}).get("kind") == "selfhosted"}):
        price = be[name].get("price_per_hour_usd")
        if not price:
            continue
        cap = throughput.get(name, {}).get("max_pages_per_min")
        capacity = round(cap * 60) if cap else None
        break_even = {}
        for llm in llms:
            c = (basis.get(llm) or {}).get("cost_per_1k_pages_usd")
            if not c:
                continue
            above = round(price / c * 1000)
            break_even[llm] = {
                "llm_cost_per_1k_pages_usd": c,
                "cheaper_above_pages_per_hour": above,
                "reachable_on_one_gpu": capacity is None or above <= capacity,
            }
        out[name] = {
            "gpu_price_per_hour_usd": price,
            "instance_type": be[name].get("instance_type"),
            "capacity_pages_per_hour": capacity,
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
    method = scores.get("method") or {}
    basis = method.get("cost_basis") or {}
    throughput = throughput_facts(sp, basis)
    return {
        "pipelines": pipeline_facts(
            scores["extraction"]["aggregate"], method.get("ranking_min_coverage", MIN_COVERAGE)
        ),
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
            "**Cost note:** no cost could be computed for "
            + ", ".join(f"`{x}`" for x in unpriced)
            + " (no price configured, or no successful request reported its token usage), so they are left out "
            "of the cheapest-pipeline comparison. Set `price_per_mtok` for those backends to include them.",
        ]
    low = sorted({x for v in p for x in p[v].get("low_coverage", [])})
    if low:
        thr = next(iter(p.values())).get("ranking_min_coverage", MIN_COVERAGE)
        fell_back = [v for v in ("clean", "degraded") if (p.get(v) or {}).get("coverage_fallback")]
        out += [
            "",
            f"**Coverage note:** {', '.join(f'`{x}`' for x in low)} answered fewer than {thr:.0%} of the documents"
            + (
                f"; no pipeline reached that on {' and '.join(fell_back)} pages, so cheapest and fastest there "
                "compare all pipelines."
                if fell_back
                else ", so they are left out of the cheapest and fastest comparisons (failed documents are cheap "
                "and quick)."
            ),
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
        flag = "" if t.get("meets_error_threshold", True) else ", no run within 1% errors"
        out.append(f"| `{b}` | {p50} | {t['max_pages_per_min']:.0f} ({t['at_concurrency']}{flag}) |")
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
        if y.get("reachable_on_one_gpu", True):
            out.append(
                f"- `{name}` is cheaper than `{b}` transcription ({_usd(y['llm_cost_per_1k_pages_usd'])}/1k pages) "
                f"above **~{y['cheaper_above_pages_per_hour']} pages/hour** sustained"
            )
        else:
            out.append(
                f"- `{name}` is not cheaper than `{b}` transcription ({_usd(y['llm_cost_per_1k_pages_usd'])}/1k "
                f"pages) at any volume: it would take ~{y['cheaper_above_pages_per_hour']} pages/hour, more than "
                "one GPU handled in this run"
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
