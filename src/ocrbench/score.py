"""Stage 3: score a run folder against its ground truth. A pure function of runs/<id>/.

Everything comes from the run itself: documents (runs/<id>/docs), records (*.jsonl), and the setup
in run.json (prices, GPU price, post-processing). The live config is never consulted, so a run scores
the same on any machine, today or later.

Choices that affect comparisons, all reported in scores["method"]:
- Failed requests count. A page with no successful transcript scores CER=1 and recall 0; an extraction
  that failed (or had no transcript to read) scores 0 on every field. `coverage` shows how many succeeded.
- One transcript per (backend, page, variant) is scored: the successful one from the lowest-concurrency run,
  the latest if there are several (so a deliberate re-run replaces it).
- Per-page cost of a self-hosted model = GPU price per hour / pages per hour at its best *sustained*
  throughput (the fastest run with <=1% errors), i.e. assuming the GPU is kept busy. The cost at
  concurrency 1 is reported next to it.
"""

from __future__ import annotations

import json
import re
import statistics as st
import unicodedata
from collections import Counter
from pathlib import Path

from rapidfuzz.distance import Levenshtein

from . import postprocess

_TAGS = re.compile(r"</?[a-zA-Z][^>]*>")
# One number token: digits joined by . , : / - (dates, decimals, account numbers). Spaces separate tokens;
# numbers printed with space separators ("1 250,00", "455 46 959") are matched as token sequences.
_TOK = re.compile(r"\d+(?:[.,:/\-]\d+)*")
RUNAWAY_RATIO = 3.0  # output more than 3x the page length = repetition loop
SUSTAINED_MAX_ERROR_RATE = 0.01


# --------------------------------------------------------------------------- text metrics
def normalise(text: str) -> str:
    """Compare content, not formatting: drop markdown/HTML syntax, unify whitespace and checkbox glyphs."""
    t = unicodedata.normalize("NFC", text or "")
    t = _TAGS.sub(" ", t).replace("\u00a0", " ").replace("\u202f", " ")
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
        "cer": levenshtein(g, h) / max(1, len(g)),
        "wer": levenshtein(gw, hw) / max(1, len(gw)),
        "word_recall": overlap / max(1, sum(gc.values())),
        "word_precision": overlap / max(1, sum(hc.values())),
        "num_recall": (len(gt_numbers) - len(missing)) / max(1, len(gt_numbers)),
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
def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def _token_cost(price: dict | None, pt: float, ct: float) -> float | None:
    if not price:
        return None
    return (pt * price.get("input", 0) + ct * price.get("output", 0)) / 1e6


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


# --------------------------------------------------------------------------- score
def speed_rows(meta: dict, runs: list[dict], transcripts: list[dict]) -> list[dict]:
    be = meta["backends"]
    prices = {k: v.get("price_per_mtok") for k, v in be.items()}
    rows = []
    for run in runs:
        rs = [r for r in transcripts if r["run_id"] == run["run_id"] and r["backend"] == run["backend"]]
        ok = [r for r in rs if r["ok"]]
        lats = [r["latency_s"] for r in ok]
        pt = st.mean([r["prompt_tokens"] for r in ok if r.get("prompt_tokens")] or [0])
        ct = st.mean([r["completion_tokens"] for r in ok if r.get("completion_tokens")] or [0])
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


def cost_basis(speed: list[dict], hourly: set[str]) -> dict[str, dict]:
    """Per backend: the per-page cost used for pipeline totals, and why. `hourly`: backends billed per hour
    (self-hosted), whose cost per page falls with throughput."""
    out: dict[str, dict] = {}
    for be in sorted({s["backend"] for s in speed}):
        rows = [s for s in speed if s["backend"] == be and s["cost_per_1k_pages_usd"] is not None]
        if not rows:
            out[be] = {"cost_per_1k_pages_usd": None, "basis": "no price configured"}
            continue
        c1 = next((s for s in rows if s["concurrency"] == 1), None)
        if be in hourly:
            ok = [s for s in rows if s["pages"] and s["errors"] / s["pages"] <= SUSTAINED_MAX_ERROR_RATE]
            best = max(ok or rows, key=lambda s: s["pages_per_min"])
            out[be] = {
                "cost_per_1k_pages_usd": best["cost_per_1k_pages_usd"],
                "basis": f"best sustained throughput: {best['pages_per_min']:.1f} pages/min at concurrency "
                f"{best['concurrency']} (GPU kept busy)",
                "concurrency": best["concurrency"],
                "cost_at_concurrency_1": c1["cost_per_1k_pages_usd"] if c1 else None,
            }
        else:  # token-priced: cost per page doesn't depend on concurrency; average all runs
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


def transcription(meta: dict, docs: Path, run_dir: Path, transcripts: list[dict], manifest: dict) -> tuple[list, list]:
    steps = {k: b.get("postprocess") or [] for k, b in meta["backends"].items()}
    attempted: dict[tuple, dict | None] = {}
    for r in transcripts:  # see pick_transcript; failed attempts still mark the page as expected
        k = (r["backend"], r["doc_id"], r["page"], r["variant"])
        if r["doc_id"] not in manifest:
            continue
        attempted[k] = pick_transcript(attempted.get(k), r)
    pages = []
    for (be, doc_id, page, variant), r in sorted(attempted.items()):
        pg = next((p for p in manifest[doc_id]["pages"] if p["n"] == page), None)
        if pg is None:
            continue
        gt = _read(docs / pg["gt"]) or ""
        nums = json.loads(_read(docs / pg["gt_meta"]) or '{"numbers": []}')["numbers"]
        hyp = _read(run_dir / r["output_file"]) if r else None
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
    raw = _read(run_dir / e["output_file"]) if e["ok"] and e.get("output_file") else None
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


def source_latency(transcripts: list[dict]) -> dict[tuple[str, str, str], float]:
    """(backend, doc, variant) -> transcription seconds for the whole document: the sum over its pages,
    from the backend's lowest-concurrency run (the same basis as the latency figures)."""
    lowest: dict[str, int] = {}
    for r in transcripts:
        if r["ok"]:
            lowest[r["backend"]] = min(lowest.get(r["backend"], 10**9), r["concurrency"])
    out: dict[tuple[str, str, str], float] = {}
    counted = set()
    for r in transcripts:
        k = (r["backend"], r["doc_id"], r["page"], r["variant"])
        if r["ok"] and r["concurrency"] == lowest[r["backend"]] and k not in counted:
            counted.add(k)
            dk = (r["backend"], r["doc_id"], r["variant"])
            out[dk] = out.get(dk, 0.0) + r["latency_s"]
    return out


def _pipeline_cost(s: list[dict], okd: list[dict], price: dict | None, src_cost: float | None, manifest: dict):
    """USD per 1k documents: extractor tokens, plus the source's per-page cost x pages per document.
    None when either part has no price."""
    per_call = _token_cost(
        price,
        st.mean([d["prompt_tokens"] or 0 for d in okd] or [0]),
        st.mean([d["completion_tokens"] or 0 for d in okd] or [0]),
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
        truth = json.loads(_read(docs / manifest[doc_id]["fields"]) or '{"fields": {}}')["fields"]
        docs_out.append(score_extraction(e, truth, run_dir))
    src_lat = source_latency(transcripts)

    agg = []
    for pipe in sorted({d["pipeline"] for d in docs_out}):
        for v in ("clean", "degraded"):
            s = [d for d in docs_out if d["pipeline"] == pipe and d["variant"] == v]
            if not s:
                continue
            src, okd = s[0]["source"], [d for d in s if d["ok"]]
            lat = st.mean(d["latency_s"] for d in okd) if okd else None
            e2e = (
                st.mean(d["latency_s"] + src_lat.get((src, d["doc_id"], v), 0.0) for d in okd)
                if okd and src != "image"
                else lat
            )
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
    transcripts = _jsonl(run_dir / "transcripts.jsonl")
    speed = speed_rows(meta, _jsonl(run_dir / "runs.jsonl"), transcripts)
    basis = cost_basis(speed, {k for k, b in meta["backends"].items() if b["kind"] == "selfhosted"})
    tagg, tpages = transcription(meta, docs, run_dir, transcripts, manifest)
    eagg, edocs = extraction(meta, docs, run_dir, _jsonl(run_dir / "extractions.jsonl"), manifest, transcripts, basis)
    return {
        "run_id": meta["id"],
        "method": {
            "failures": "counted: failed pages score CER=1/recall 0, failed extractions score 0 (see coverage)",
            "transcript_per_page": "successful one from the lowest-concurrency run (latest if several)",
            "runaway_ratio": RUNAWAY_RATIO,
            "cost_basis": basis,
        },
        "speed": speed,
        "transcription": {"aggregate": tagg, "pages": tpages},
        "extraction": {"aggregate": eagg, "docs": edocs},
    }
