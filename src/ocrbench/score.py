"""Stage 3: score a run folder against its ground truth. A pure function of runs/<id>/.

Everything comes from the run itself: documents (runs/<id>/docs), records (*.jsonl), and the setup
in run.json (prices, GPU price, post-processing). The live config is never consulted, so a run scores
the same on any machine, today or later.

Choices that affect comparisons, all reported in scores["method"]:
- Failed requests count. A page with no successful transcript scores CER=1 and recall 0; an extraction
  that failed (or had no transcript to read) scores 0 on every field. `coverage` shows how many succeeded.
  Pages a plan item selected but never attempted (a killed task) count as failed too.
- One transcript per (backend, page, variant) is scored: the successful one from the lowest-concurrency run,
  the latest if there are several (so a deliberate re-run replaces it). End-to-end latency uses that same one.
- Per-page CER and WER are capped at 1 (a failed page): a runaway page can't score worse than no answer.
- Per-page cost of a self-hosted model = GPU price per hour / pages per hour at its best *sustained*
  throughput (the fastest run with <=1% errors), i.e. assuming the GPU is kept busy. The cost at
  concurrency 1 is reported next to it. If no run stays under 1% errors, the run with the fewest errors is
  used and flagged.
- Unknown is not zero: a run with no token usage (or no successful request) has no cost, and is left out
  of the mean rather than counted as free.
"""

from __future__ import annotations

import html
import json
import re
import statistics as st
import unicodedata
from collections import Counter
from pathlib import Path

from rapidfuzz.distance import Levenshtein

from . import postprocess
from .fileio import read_jsonl, read_text

_TAGS = re.compile(r"</?[a-zA-Z][^>]*>")
# One number token: digits joined by . , : / - (dates, decimals, account numbers). Spaces separate tokens;
# numbers printed with space separators ("1 250,00", "455 46 959") are matched as token sequences.
_TOK = re.compile(r"\d+(?:[.,:/\-]\d+)*")
RUNAWAY_RATIO = 3.0  # output more than 3x the page length = repetition loop
SUSTAINED_MAX_ERROR_RATE = 0.01
RANKING_MIN_COVERAGE = 0.99  # facts.py: only pipelines this complete compete for cheapest / fastest


# --------------------------------------------------------------------------- text metrics
def normalise(text: str) -> str:
    """Compare content, not formatting: drop markdown/HTML syntax and entities, unify whitespace and
    checkbox glyphs."""
    t = unicodedata.normalize("NFC", text or "")
    t = html.unescape(_TAGS.sub(" ", t)).replace("\u00a0", " ").replace("\u202f", " ")
    t = t.replace("□", "☐").replace("✓", "☑").replace("✔", "☑")
    t = re.sub(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$", " ", t, flags=re.M)
    t = re.sub(r"[#*_`|>]+", " ", t)
    t = re.sub(r"^\s*[-+]\s+", " ", t, flags=re.M)
    return re.sub(r"\s+", " ", t).strip()


def levenshtein(a, b) -> int:
    return Levenshtein.distance(a, b)  # works on str and on lists of words


def _tokens(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(), m.start(), m.end()) for m in _TOK.finditer(text)]


def number_found(number: str, text: str, toks: list[tuple[str, int, int]] | None = None) -> bool:
    """`number` (e.g. "1 250,00") appears in `text` as whole tokens: "12" is not found in "1234"."""
    want = [t for t, _, _ in _tokens(number)]
    if not want:
        return False
    toks = toks if toks is not None else _tokens(text)
    for i in range(len(toks) - len(want) + 1):
        if [t for t, _, _ in toks[i : i + len(want)]] != want:
            continue
        gaps = [text[toks[j][2] : toks[j + 1][1]] for j in range(i, i + len(want) - 1)]
        if all(g.strip(" ") == "" and g for g in gaps):  # consecutive, separated only by spaces
            return True
    return False


def _error_rate(dist: int, n_ref: int, n_hyp: int) -> float:
    """Edit distance per reference unit, capped at 1 (a failed page). An empty reference scores 0 if the
    output is empty too, else 1."""
    if n_ref == 0:
        return 0.0 if n_hyp == 0 else 1.0
    return min(1.0, dist / n_ref)


def page_metrics(gt_text: str, gt_numbers: list[str], hyp: str) -> dict:
    g, h = normalise(gt_text), normalise(hyp)
    gw, hw = g.split(), h.split()
    htoks = _tokens(h)
    missing = [n for n in gt_numbers if not number_found(n, h, htoks)]
    gt_toks = Counter(t for t, _, _ in _tokens(g))
    halluc = [t for t, _, _ in htoks if sum(c.isdigit() for c in t) >= 3 and t not in gt_toks]
    gc, hc = Counter(w.casefold() for w in gw), Counter(w.casefold() for w in hw)
    overlap = sum((gc & hc).values())
    return {
        "cer": _error_rate(levenshtein(g, h), len(g), len(h)),
        "wer": _error_rate(levenshtein(gw, hw), len(gw), len(hw)),
        # nothing on the page to find = nothing missed
        "word_recall": overlap / sum(gc.values()) if gc else 1.0,
        "word_precision": overlap / max(1, sum(hc.values())),
        "num_recall": (len(gt_numbers) - len(missing)) / len(gt_numbers) if gt_numbers else 1.0,
        "num_missing": len(missing),
        "missing_examples": missing[:5],
        "num_hallucinated": len(halluc),
        "hallucinated_examples": halluc[:5],
        "len_ratio": len(h) / max(1, len(g)),
        "clean_text": h,
    }


def failed_page_metrics(gt_numbers: list[str]) -> dict:
    """A page with no successful transcript: worst score, so failures can't improve a backend's numbers."""
    return {
        "cer": 1.0,
        "wer": 1.0,
        "word_recall": 0.0,
        "word_precision": 0.0,
        "num_recall": 0.0,
        "num_missing": len(gt_numbers),
        "missing_examples": gt_numbers[:5],
        "num_hallucinated": 0,
        "hallucinated_examples": [],
        "len_ratio": 0.0,
        "clean_text": "",
    }


# --------------------------------------------------------------------------- field metrics
_CURRENCY = re.compile(r"^(?:kr|nok)\.?\s*(?=[\d\-−])|\s*(?:kr|nok)\.?$|,-$")


def norm_value(v) -> str:
    s = unicodedata.normalize("NFC", str(v)).replace("\u00a0", " ").replace("\u202f", " ")
    s = re.sub(r"\s+", " ", s).strip().casefold()
    s = _CURRENCY.sub("", s).strip()
    return s.strip(".;:").strip()


def field_correct(truth, pred) -> bool:
    if pred is None:
        return False
    if isinstance(truth, list):
        p = pred if isinstance(pred, list) else [pred]
        return sorted(map(norm_value, truth)) == sorted(map(norm_value, p))
    return norm_value(truth) == norm_value(pred if not isinstance(pred, list) else " ".join(map(str, pred)))


# --------------------------------------------------------------------------- helpers
def _pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def _token_cost(price: dict | None, pt: float | None, ct: float | None) -> float | None:
    """USD per call; None if there is no price or the token usage is unknown (never $0 for "unknown")."""
    if not price or pt is None or ct is None:
        return None
    return (pt * price.get("input", 0) + ct * price.get("output", 0)) / 1e6


def _mean_known(xs) -> float | None:
    """Mean of the reported values; missing (None) or zero usage means "not reported" and is left out."""
    known = [x for x in xs if x]
    return st.mean(known) if known else None


# --------------------------------------------------------------------------- score
def speed_rows(meta: dict, runs: list[dict], transcripts: list[dict]) -> list[dict]:
    be = meta["backends"]
    prices = {k: v.get("price_per_mtok") for k, v in be.items()}
    rows = []
    for run in runs:
        rs = [r for r in transcripts if r["run_id"] == run["run_id"] and r["backend"] == run["backend"]]
        ok = [r for r in rs if r["ok"]]
        lats = [r["latency_s"] for r in ok]
        pt = _mean_known(r.get("prompt_tokens") for r in ok)
        ct = _mean_known(r.get("completion_tokens") for r in ok)
        ppm = len(ok) / run["wall_s"] * 60 if run["wall_s"] else 0
        per_page = _token_cost(prices.get(run["backend"]), pt, ct)
        gpu_hour = (be.get(run["backend"]) or {}).get("price_per_hour_usd")  # self-hosted: billed per hour
        if per_page is None and gpu_hour and ppm:
            per_page = gpu_hour / (ppm * 60)
        rows.append(
            {
                "backend": run["backend"],
                "model": run["model"],
                "concurrency": run["concurrency"],
                "pages": len(rs),
                "ok": len(ok),
                "errors": len(rs) - len(ok),
                "wall_s": run["wall_s"],
                "pages_per_min": ppm,
                "p50_s": _pct(lats, 50),
                "p95_s": _pct(lats, 95),
                "max_s": max(lats) if lats else None,
                "mean_prompt_tokens": pt,
                "mean_completion_tokens": ct,
                "retries": sum(r.get("retries", 0) for r in rs),
                "cost_per_1k_pages_usd": None if per_page is None else per_page * 1000,
            }
        )
    return rows


def cost_basis(speed: list[dict], hourly: set[str], priced: set[str] | None = None) -> dict[str, dict]:
    """Per backend: the per-page cost used for pipeline totals, and why. `hourly`: backends billed per hour
    (self-hosted), whose cost per page falls with throughput. `priced`: backends with a price configured
    (to tell "no price" from "price, but no usable run")."""
    out: dict[str, dict] = {}
    for be in sorted({s["backend"] for s in speed}):
        rows = [s for s in speed if s["backend"] == be and s["cost_per_1k_pages_usd"] is not None]
        if not rows:
            if priced is None or be not in priced:
                why = "no price configured"
            elif be in hourly:
                why = "unknown: no run had successful pages"
            else:
                why = "unknown: no run had successful pages with token usage"
            out[be] = {"cost_per_1k_pages_usd": None, "basis": why}
            continue
        c1 = next((s for s in rows if s["concurrency"] == 1), None)
        if be in hourly:
            rate = [s["errors"] / s["pages"] if s["pages"] else 1.0 for s in rows]
            ok = [s for s, r in zip(rows, rate, strict=True) if r <= SUSTAINED_MAX_ERROR_RATE]
            if ok:
                best = max(ok, key=lambda s: s["pages_per_min"])
                why = (
                    f"best sustained throughput: {best['pages_per_min']:.1f} pages/min at concurrency "
                    f"{best['concurrency']} (GPU kept busy)"
                )
            else:  # nothing sustained: the most reliable run, not the fastest error-prone one
                i = min(range(len(rows)), key=lambda j: (rate[j], -rows[j]["pages_per_min"]))
                best = rows[i]
                why = (
                    f"no run stayed within {SUSTAINED_MAX_ERROR_RATE:.0%} errors, so the most reliable one: "
                    f"{best['pages_per_min']:.1f} pages/min at concurrency {best['concurrency']} "
                    f"({rate[i]:.0%} errors, GPU kept busy)"
                )
            out[be] = {
                "cost_per_1k_pages_usd": best["cost_per_1k_pages_usd"],
                "basis": why,
                "concurrency": best["concurrency"],
                "pages_per_min": best["pages_per_min"],
                "meets_error_threshold": bool(ok),
                "cost_at_concurrency_1": c1["cost_per_1k_pages_usd"] if c1 else None,
            }
        else:  # token-priced: cost per page doesn't depend on concurrency; average the runs with a known cost
            out[be] = {
                "cost_per_1k_pages_usd": st.mean(s["cost_per_1k_pages_usd"] for s in rows),
                "basis": "list price x tokens used (mean over runs)",
            }
    return out


def pick_transcript(current: dict | None, new: dict) -> dict | None:
    """Which successful transcript of a page is scored: the one from the lowest-concurrency run (the same
    basis as the latency figures), and among equals the most recent one, so a deliberate re-run replaces it."""
    if not new["ok"]:
        return current
    if current is None or new["concurrency"] <= current["concurrency"]:
        return new
    return current


def chosen_transcripts(transcripts: list[dict], manifest: dict) -> dict[tuple, dict | None]:
    """(backend, doc, page, variant) -> the transcript scored (pick_transcript), None if every attempt failed."""
    out: dict[tuple, dict | None] = {}
    for r in transcripts:
        if r["doc_id"] in manifest:
            k = (r["backend"], r["doc_id"], r["page"], r["variant"])
            out[k] = pick_transcript(out.get(k), r)
    return out


def expected_pages(meta: dict, docs: Path) -> dict[str, set[tuple]] | None:
    """Backend -> (doc, page, variant) its plan items selected (the selection transcribe.pages makes), so
    pages never attempted still count. None if run.json has no plan items (then: attempted pages only)."""
    from .transcribe import pages as select

    items = meta.get("plan_items")
    if not items:
        return None
    out: dict[str, set[tuple]] = {}
    for it in items:
        refs = select(docs, it.get("variant", "both"), int(it.get("limit", 0)))
        out.setdefault(it["backend"], set()).update((r.doc_id, r.page, r.variant) for r in refs)
    return out


def transcription(
    meta: dict,
    docs: Path,
    run_dir: Path,
    transcripts: list[dict],
    manifest: dict,
    expected: dict[str, set[tuple]] | None = None,
) -> tuple[list, list]:
    steps = {k: b.get("postprocess") or [] for k, b in meta["backends"].items()}
    attempted = chosen_transcripts(transcripts, manifest)  # failed attempts still mark the page as expected
    for be, keys in (expected or {}).items():
        for doc_id, page, variant in keys:
            if doc_id in manifest:
                attempted.setdefault((be, doc_id, page, variant), None)  # selected, never attempted
    pages = []
    for (be, doc_id, page, variant), r in sorted(attempted.items()):
        pg = next((p for p in manifest[doc_id]["pages"] if p["n"] == page), None)
        if pg is None:
            continue
        gt = read_text(docs / pg["gt"]) or ""
        nums = json.loads(read_text(docs / pg["gt_meta"]) or '{"numbers": []}')["numbers"]
        hyp = read_text(run_dir / r["output_file"]) if r else None
        if hyp is None:
            m, ok = failed_page_metrics(nums), False
        else:
            hyp = postprocess.apply(steps.get(be), hyp)
            m, ok = page_metrics(gt, nums, hyp), True
        pages.append(
            {
                "backend": be,
                "doc_id": doc_id,
                "page": page,
                "variant": variant,
                "ok": ok,
                "truncated": bool(r and r.get("finish_reason") == "length"),
                "output_file": r["output_file"] if r else None,
                **m,
            }
        )
    agg = []
    for be in sorted({p["backend"] for p in pages}):
        for v in ("clean", "degraded"):
            s = [p for p in pages if p["backend"] == be and p["variant"] == v]
            if not s:
                continue
            agg.append(
                {
                    "backend": be,
                    "variant": v,
                    "pages": len(s),
                    "coverage": sum(p["ok"] for p in s) / len(s),
                    **{
                        m: st.mean(p[m] for p in s)
                        for m in ("cer", "wer", "word_recall", "word_precision", "num_recall")
                    },
                    "cer_median": st.median(p["cer"] for p in s),
                    "runaway": sum(p["len_ratio"] > RUNAWAY_RATIO for p in s),
                    "truncated": sum(p["truncated"] for p in s),
                    "num_hallucinated_excl_runaway": sum(
                        p["num_hallucinated"] for p in s if p["len_ratio"] <= RUNAWAY_RATIO
                    ),
                    "num_missing": sum(p["num_missing"] for p in s),
                    "num_hallucinated": sum(p["num_hallucinated"] for p in s),
                }
            )
    return agg, pages


def latest_extractions(records: list[dict], manifest: dict) -> dict[tuple[str, str, str], dict]:
    """The latest record per (pipeline, doc, variant); a success beats a later failure."""
    latest: dict[tuple[str, str, str], dict] = {}
    for e in records:
        k = (e["pipeline"], e["doc_id"], e["variant"])
        if e["doc_id"] in manifest and (k not in latest or e["ok"] or not latest[k]["ok"]):
            latest[k] = e
    return latest


def score_extraction(e: dict, truth: dict, run_dir: Path) -> dict:
    """Field-by-field score of one extraction record. A failed extraction gets every field wrong."""
    raw = read_text(run_dir / e["output_file"]) if e["ok"] and e.get("output_file") else None
    pred = json.loads(raw) if raw else {}
    fields = {
        f: {"truth": t, "predicted": pred.get(f), "correct": field_correct(t, pred.get(f))} for f, t in truth.items()
    }
    n_ok = sum(x["correct"] for x in fields.values())
    return {
        "pipeline": e["pipeline"],
        "doc_id": e["doc_id"],
        "variant": e["variant"],
        "ok": bool(raw),
        "error": e.get("error"),
        "accuracy": n_ok / max(1, len(fields)),
        "fields_total": len(fields),
        "fields_correct": n_ok,
        "fields": fields,
        "output_file": e.get("output_file"),
        "latency_s": e.get("latency_s") or 0.0,
        "prompt_tokens": e.get("prompt_tokens"),
        "completion_tokens": e.get("completion_tokens"),
        "agent": e["agent"],
        "source": e["source"],
    }


def source_latency(transcripts: list[dict], manifest: dict) -> dict[tuple[str, str, str], float | None]:
    """(backend, doc, variant) -> transcription seconds for the whole document: the sum over its pages of the
    transcript each page is scored on (pick_transcript: lowest concurrency available for that page, latest
    if several). None if some page has no successful transcript: unknown, not undercounted."""
    chosen = chosen_transcripts(transcripts, manifest)
    out: dict[tuple[str, str, str], float | None] = {}
    for be, doc_id, variant in sorted({(b, d, v) for b, d, _, v in chosen}):
        lats = [(chosen.get((be, doc_id, p["n"], variant)) or {}).get("latency_s") for p in manifest[doc_id]["pages"]]
        known = [float(x) for x in lats if x is not None]
        out[(be, doc_id, variant)] = float(sum(known)) if len(known) == len(lats) else None
    return out


def _end_to_end(okd: list[dict], src: str, variant: str, src_lat: dict) -> float | None:
    """Mean per-document latency: the extraction call plus, for a transcript source, its transcription.
    Documents whose transcription time is unknown are left out; None if none is known."""
    if not okd:
        return None
    if src == "image":
        return st.mean(d["latency_s"] for d in okd)
    known = [d["latency_s"] + t for d in okd if (t := src_lat.get((src, d["doc_id"], variant))) is not None]
    return st.mean(known) if known else None


def _pipeline_cost(s: list[dict], okd: list[dict], price: dict | None, src_cost: float | None, manifest: dict):
    """USD per 1k documents: extractor tokens, plus the source's per-page cost x pages per document.
    None when either part has no price, or no successful extraction reported its token usage."""
    per_call = _token_cost(
        price,
        _mean_known(d["prompt_tokens"] for d in okd),
        _mean_known(d["completion_tokens"] for d in okd),
    )
    if per_call is None or not okd:
        return None
    cost = per_call * 1000
    if s[0]["source"] == "image":
        return cost
    if src_cost is None:
        return None
    return cost + src_cost * st.mean(len(manifest[d["doc_id"]]["pages"]) for d in s)


def extraction(
    meta: dict,
    docs: Path,
    run_dir: Path,
    records: list[dict],
    manifest: dict,
    transcripts: list[dict],
    basis: dict[str, dict],
) -> tuple[list, list]:
    prices = {k: v.get("price_per_mtok") for k, v in meta["backends"].items()}
    docs_out = []
    for (_, doc_id, _), e in sorted(latest_extractions(records, manifest).items()):
        truth = json.loads(read_text(docs / manifest[doc_id]["fields"]) or '{"fields": {}}')["fields"]
        docs_out.append(score_extraction(e, truth, run_dir))
    src_lat = source_latency(transcripts, manifest)

    agg = []
    for pipe in sorted({d["pipeline"] for d in docs_out}):
        for v in ("clean", "degraded"):
            s = [d for d in docs_out if d["pipeline"] == pipe and d["variant"] == v]
            if not s:
                continue
            src, okd = s[0]["source"], [d for d in s if d["ok"]]
            lat = st.mean(d["latency_s"] for d in okd) if okd else None
            e2e = _end_to_end(okd, src, v, src_lat)
            src_cost = (basis.get(src) or {}).get("cost_per_1k_pages_usd")
            tot, cor = sum(d["fields_total"] for d in s), sum(d["fields_correct"] for d in s)
            agg.append(
                {
                    "pipeline": pipe,
                    "variant": v,
                    "docs": len(s),
                    "coverage": len(okd) / len(s),
                    "field_accuracy": cor / max(1, tot),
                    "fields_total": tot,
                    "fields_correct": cor,
                    "mean_latency_s": lat,
                    "end_to_end_latency_s": e2e,
                    "cost_per_1k_docs_usd": _pipeline_cost(s, okd, prices.get(s[0]["agent"]), src_cost, manifest),
                }
            )
    return agg, docs_out


def score(meta: dict, run_dir: Path) -> dict:
    """Score runs/<id>/ using only its own files."""
    from .runs import upgrade

    meta = upgrade(meta)
    run_dir = Path(run_dir)
    docs = run_dir / "docs"
    manifest = {d["id"]: d for d in json.loads((docs / "manifest.json").read_text(encoding="utf-8"))}
    transcripts = read_jsonl(run_dir / "transcripts.jsonl")
    speed = speed_rows(meta, read_jsonl(run_dir / "runs.jsonl"), transcripts)
    be = meta["backends"]
    hourly = {k for k, b in be.items() if b["kind"] == "selfhosted"}
    priced = {k for k, b in be.items() if b.get("price_per_hour_usd" if k in hourly else "price_per_mtok")}
    basis = cost_basis(speed, hourly, priced)
    expected = expected_pages(meta, docs)
    tagg, tpages = transcription(meta, docs, run_dir, transcripts, manifest, expected)
    eagg, edocs = extraction(
        meta, docs, run_dir, read_jsonl(run_dir / "extractions.jsonl"), manifest, transcripts, basis
    )
    return {
        "run_id": meta["id"],
        "method": {
            "failures": "counted: failed pages score CER=1/recall 0, failed extractions score 0 (see coverage)",
            "expected_pages": "every page a plan item selected; one never attempted counts as failed"
            if expected is not None
            else "pages with a record (run.json has no plan items)",
            "transcript_per_page": "successful one from the lowest-concurrency run (latest if several)",
            "text_normalisation": "Markdown/HTML tags removed, HTML entities decoded, whitespace unified",
            "error_rate_cap": "per-page CER and WER capped at 1; empty ground truth: 0 if output empty, else 1",
            "empty_ground_truth_recall": "a page with no words / numbers has word / number recall 1",
            "end_to_end_latency": "extraction call + transcription of every page of the document (the "
            "transcript scored); documents with an unknown transcription time are left out of the mean",
            "unknown_cost": "runs/extractions without token usage are left out of cost means; none known = no cost",
            "runaway_ratio": RUNAWAY_RATIO,
            "sustained_max_error_rate": SUSTAINED_MAX_ERROR_RATE,
            "ranking_min_coverage": RANKING_MIN_COVERAGE,
            "cost_basis": basis,
        },
        "speed": speed,
        "transcription": {"aggregate": tagg, "pages": tpages},
        "extraction": {"aggregate": eagg, "docs": edocs},
    }
