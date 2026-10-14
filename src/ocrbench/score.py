"""Stage 3: score a run folder against its ground truth. A pure function of runs/<id>/.

Everything comes from the run itself: documents (runs/<id>/docs), records (*.jsonl), and the setup
in run.json (prices, GPU price, post-processing). The live config is never consulted, so a run scores
the same on any machine, today or later.

Choices that affect comparisons, all reported in scores["method"]:
- Failed requests count. A page with no successful transcript scores CER=1 and recall 0; an extraction
  that failed (or had no transcript to read) scores 0 on every field. `coverage` shows how many succeeded.
  Pages a plan item selected but never attempted (a killed task) count as failed too, and so do documents
  a pipeline's source was expected to cover but that have no extraction record.
- Fields are the ground truth's. A schema field the ground truth leaves out (an optional field absent from
  the document) counts as one wrong field if the extractor invented a value for it, so guessing never pays.
- One transcript per (backend, page, variant) is scored: the successful one from the lowest-concurrency run,
  the latest if there are several (so a deliberate re-run replaces it); within a run that sent every page
  N times (`repeat: N`), its last repeat. End-to-end latency uses that same one. Likewise one extraction per
  (pipeline, document, variant): the last repeat, a success beating a failure.
- Repeats (`repeat: N`, `extraction.repeat`) are also scored one by one, into the `variation` block: how much
  results move between identical runs (see VARIATION_METHOD). Only present when something was repeated.
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


def is_empty(v) -> bool:
    """No value: missing, null, blank, or a list of blanks."""
    if isinstance(v, list):
        return all(is_empty(x) for x in v)
    return v is None or norm_value(v) == ""


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


def _repeat(r: dict) -> int:
    """Repeat index of a record (0..N-1); records written before repeats were recorded are repeat 0."""
    return int(r.get("repeat") or 0)


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
    basis as the latency figures), and among equals the most recent run, so a deliberate re-run replaces it.
    Within one run that sent every page several times (`repeat: N`), its last repeat, whatever order the
    repeats finished in."""
    if not new["ok"]:
        return current
    if current is None or new["concurrency"] < current["concurrency"]:
        return new
    if new["concurrency"] == current["concurrency"] and (
        new.get("run_id") != current.get("run_id") or _repeat(new) >= _repeat(current)
    ):
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
    """The record scored per (pipeline, doc, variant): a success beats a failure; among equals the last
    repeat (`extraction.repeat` > 1), then the latest record."""
    latest: dict[tuple[str, str, str], dict] = {}
    for e in records:
        k = (e["pipeline"], e["doc_id"], e["variant"])
        if e["doc_id"] not in manifest:
            continue
        cur = latest.get(k)
        if (
            cur is None
            or (e["ok"] and not cur["ok"])
            or (bool(e["ok"]) == bool(cur["ok"]) and _repeat(e) >= _repeat(cur))
        ):
            latest[k] = e
    return latest


def score_extraction(e: dict, truth: dict, run_dir: Path, schema_fields: list[str] | None = None) -> dict:
    """Field-by-field score of one extraction record. A failed extraction gets every field wrong. A schema
    field the ground truth leaves out (an optional field absent from this document) is only scored when the
    extractor invented a value for it: one wrong field. Without a schema, only ground-truth fields count."""
    raw = read_text(run_dir / e["output_file"]) if e.get("ok") and e.get("output_file") else None
    pred = json.loads(raw) if raw else {}
    truth = {f: t for f, t in truth.items() if not is_empty(t)}
    fields = {
        f: {"truth": t, "predicted": pred.get(f), "correct": field_correct(t, pred.get(f))} for f, t in truth.items()
    }
    for f in schema_fields or []:
        if f not in truth and not is_empty(pred.get(f)):
            fields[f] = {"truth": None, "predicted": pred[f], "correct": False, "invented": True}
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


def expected_extractions(
    meta: dict, expected: dict[str, set[tuple]] | None, records: list[dict]
) -> dict[str, set[tuple[str, str]]] | None:
    """Pipeline -> (doc, variant) it should have extracted: the documents its source was expected to cover
    (expected_pages: every selected document for `image->X`, the transcriber's selection for `T->X`), so a
    document never attempted still counts. None if run.json has no plan items or no extraction ran (then:
    attempted documents only)."""
    from .view import source

    if expected is None or not records:
        return None
    planned: list[str] = (meta.get("extraction") or {}).get("pipelines") or []
    pipes: list[str] = list(dict.fromkeys([*planned, *(str(r["pipeline"]) for r in records)]))
    by_src: dict[str, set[tuple[str, str]]] = {be: {(d, v) for d, _, v in keys} for be, keys in expected.items()}
    every: set[tuple[str, str]] = set().union(*by_src.values())
    return {p: every if source(p) == "image" else by_src.get(source(p), set()) for p in pipes}


def _not_attempted(meta: dict, pipe: str, doc_id: str, variant: str, repeat: int | None = None) -> dict:
    """The record of a document a pipeline was expected to extract but never attempted: a failed extraction."""
    from .view import extractor, source

    rec = {
        "pipeline": pipe,
        "source": source(pipe),
        "agent": extractor(meta, pipe),
        "doc_id": doc_id,
        "variant": variant,
        "ok": False,
        "error": "not attempted",
        "output_file": None,
        "latency_s": 0.0,
    }
    return rec if repeat is None else {**rec, "repeat": repeat}


def _truth(docs: Path, manifest: dict, schemas: dict, doc_id: str) -> tuple[dict, list[str]]:
    """(ground-truth fields, schema field names) of a document."""
    truth = json.loads(read_text(docs / manifest[doc_id]["fields"]) or '{"fields": {}}')["fields"]
    return truth, list((schemas.get(manifest[doc_id]["type"]) or {}).get("fields") or {})


def extraction(
    meta: dict,
    docs: Path,
    run_dir: Path,
    records: list[dict],
    manifest: dict,
    transcripts: list[dict],
    basis: dict[str, dict],
    expected: dict[str, set[tuple[str, str]]] | None = None,
) -> tuple[list, list]:
    prices = {k: v.get("price_per_mtok") for k, v in meta["backends"].items()}
    schemas = json.loads(read_text(docs / "schemas.json") or "{}")
    chosen: dict[tuple[str, str, str], dict] = dict(latest_extractions(records, manifest))
    for pipe, keys in (expected or {}).items():
        for doc_id, variant in keys:
            if doc_id in manifest:  # selected, never attempted: a failed extraction
                chosen.setdefault((pipe, doc_id, variant), _not_attempted(meta, pipe, doc_id, variant))
    docs_out = []
    for (_, doc_id, _), e in sorted(chosen.items()):
        truth, schema_fields = _truth(docs, manifest, schemas, doc_id)
        docs_out.append(score_extraction(e, truth, run_dir, schema_fields))
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


# --------------------------------------------------------------------------- run-to-run variation
VARIATION_TEXT_METRICS = ("cer", "cer_median", "word_recall", "num_recall")
VARIATION_METHOD = {
    "repeats": "transcription: the repeat indices (plan item `repeat: N`) at the backend's lowest concurrency "
    "with more than one; extraction: run.json `extraction.repeat` (and any repeat index recorded)",
    "pages": "every repeat is scored over the same pages: those selected at that concurrency; a page a repeat "
    "has no successful transcript for scores as failed (CER 1, recall 0)",
    "documents": "every repeat is scored over the documents of the main table, with the same rules (a missing "
    "or failed extraction scores 0, an invented optional field is one wrong field)",
    "std": "sample standard deviation (n-1) of the per-repeat values",
    "pages_differing": "share of pages whose normalised transcripts (as scored) are not identical in every "
    "repeat; a failed request counts as a different answer",
    "page_cer_range_mean": "mean over pages of the per-page CER range (max - min over the repeats)",
    "flip_rate": "share of (document, field) answers whose normalised value is not the same in every repeat "
    "(a failed extraction or an omitted field is no value)",
    "correct_flip_rate": "share of (document, field) answers that are right in some repeats and wrong in others "
    "(an omitted optional field the document doesn't have is right)",
    "main_tables": "the main tables score one repeat per page and document: the last one (a success beats a failure)",
    "transcript_input": "the repeats of a `<transcriber>->X` pipeline all read the one transcript scored per page, "
    "so they measure the extractor alone",
}


def _spread(values: list[float], key: str) -> dict:
    return {
        f"{key}_mean": st.mean(values),
        f"{key}_min": min(values),
        f"{key}_max": max(values),
        f"{key}_std": st.stdev(values) if len(values) > 1 else 0.0,
    }


def _planned_repeats(meta: dict) -> dict[tuple[str, int], list[dict]]:
    """(backend, concurrency) -> the plan items that ran it."""
    out: dict[tuple[str, int], list[dict]] = {}
    for it in meta.get("plan_items") or []:
        out.setdefault((it["backend"], int(it.get("concurrency", 1))), []).append(it)
    return out


def transcription_variation(
    meta: dict, docs: Path, run_dir: Path, transcripts: list[dict], manifest: dict, tpages: list[dict]
) -> list[dict]:
    """Per transcriber x variant: how much the scores move between repeats of the same pages (see
    VARIATION_METHOD). Only backends with more than one repeat at some concurrency."""
    from .transcribe import pages as select

    steps = {k: b.get("postprocess") or [] for k, b in meta["backends"].items()}
    items = _planned_repeats(meta)
    seen: dict[tuple[str, int], set[int]] = {}
    for r in transcripts:
        if r["doc_id"] in manifest:
            seen.setdefault((r["backend"], int(r["concurrency"])), set()).add(_repeat(r))
    for k, its in items.items():
        seen.setdefault(k, set()).update(range(max(int(it.get("repeat", 1)) for it in its)))
    scored = {(p["backend"], p["doc_id"], p["page"], p["variant"]) for p in tpages}
    gt_cache: dict[tuple[str, int], tuple[str, list[str]]] = {}

    def gt_of(doc_id: str, page: int) -> tuple[str, list[str]]:
        if (doc_id, page) not in gt_cache:
            pg = next(p for p in manifest[doc_id]["pages"] if p["n"] == page)
            nums = json.loads(read_text(docs / pg["gt_meta"]) or '{"numbers": []}')["numbers"]
            gt_cache[(doc_id, page)] = (read_text(docs / pg["gt"]) or "", nums)
        return gt_cache[(doc_id, page)]

    out = []
    for be in sorted({b for b, _ in seen}):
        concs = sorted(c for b, c in seen if b == be and len(seen[(b, c)]) > 1)
        if not concs:
            continue
        conc, reps = concs[0], sorted(seen[(be, concs[0])])
        chosen: dict[tuple, dict | None] = {}
        for r in transcripts:
            if r["backend"] == be and int(r["concurrency"]) == conc and r["doc_id"] in manifest:
                k = (r["doc_id"], r["page"], r["variant"], _repeat(r))
                chosen[k] = pick_transcript(chosen.get(k), r)
        wanted = {(d, p, v) for d, p, v, _ in chosen}
        for it in items.get((be, conc), []):
            wanted |= {
                (x.doc_id, x.page, x.variant) for x in select(docs, it.get("variant", "both"), int(it.get("limit", 0)))
            }
        for v in ("clean", "degraded"):
            keys = sorted((d, p) for d, p, vv in wanted if vv == v and (be, d, p, v) in scored)
            if not keys:
                continue
            per: dict[int, list[dict]] = {rep: [] for rep in reps}
            differing, ranges = 0, []
            for doc_id, page in keys:
                gt, nums = gt_of(doc_id, page)
                ms = []
                for rep in reps:
                    r = chosen.get((doc_id, page, v, rep))
                    hyp = read_text(run_dir / r["output_file"]) if r else None
                    if hyp is None:
                        m = {**failed_page_metrics(nums), "ok": False}
                    else:
                        m = {**page_metrics(gt, nums, postprocess.apply(steps.get(be), hyp)), "ok": True}
                    m["truncated"] = bool(r and r.get("finish_reason") == "length")
                    per[rep].append(m)
                    ms.append(m)
                differing += len({m["clean_text"] if m["ok"] else None for m in ms}) > 1
                ranges.append(max(m["cer"] for m in ms) - min(m["cer"] for m in ms))
            rows = [
                {
                    "repeat": rep,
                    "pages": len(ms),
                    "coverage": sum(m["ok"] for m in ms) / len(ms),
                    "cer": st.mean(m["cer"] for m in ms),
                    "cer_median": st.median(m["cer"] for m in ms),
                    "word_recall": st.mean(m["word_recall"] for m in ms),
                    "num_recall": st.mean(m["num_recall"] for m in ms),
                    "truncated": sum(m["truncated"] for m in ms),
                }
                for rep, ms in per.items()
            ]
            entry: dict = {"backend": be, "variant": v, "concurrency": conc, "repeats": len(reps), "pages": len(keys)}
            for m in VARIATION_TEXT_METRICS:
                entry.update(_spread([row[m] for row in rows], m))
            entry.update(pages_differing=differing / len(keys), page_cer_range_mean=st.mean(ranges), per_repeat=rows)
            out.append(entry)
    return out


def _answer(v) -> object:
    """A field value as compared between repeats: normalised like scoring; no value = None."""
    if is_empty(v):
        return None
    if isinstance(v, list):
        return tuple(sorted(norm_value(x) for x in v))
    return norm_value(v)


def extraction_variation(
    meta: dict, docs: Path, run_dir: Path, records: list[dict], manifest: dict, edocs: list[dict]
) -> list[dict]:
    """Per pipeline x variant: how much field accuracy moves between repeats of the same extractions (see
    VARIATION_METHOD). Only pipelines with more than one repeat."""
    planned = int((meta.get("extraction") or {}).get("repeat") or 1)
    schemas = json.loads(read_text(docs / "schemas.json") or "{}")
    reps_of: dict[str, set[int]] = {}
    for e in records:
        if e["doc_id"] in manifest:
            reps_of.setdefault(e["pipeline"], set()).add(_repeat(e))
    for d in edocs:
        reps_of.setdefault(d["pipeline"], set()).update(range(planned))
    out = []
    for pipe in sorted(reps_of):
        reps = sorted(reps_of[pipe])
        if len(reps) < 2:
            continue
        by_rep = {
            rep: latest_extractions([e for e in records if e["pipeline"] == pipe and _repeat(e) == rep], manifest)
            for rep in reps
        }
        for v in ("clean", "degraded"):
            keys = sorted({d["doc_id"] for d in edocs if d["pipeline"] == pipe and d["variant"] == v})
            if not keys:
                continue
            scored: dict[int, list[dict]] = {rep: [] for rep in reps}
            for doc_id in keys:
                truth, schema_fields = _truth(docs, manifest, schemas, doc_id)
                for rep in reps:
                    e = by_rep[rep].get((pipe, doc_id, v)) or _not_attempted(meta, pipe, doc_id, v, rep)
                    scored[rep].append(score_extraction(e, truth, run_dir, schema_fields))
            rows = []
            for rep, ds in scored.items():
                tot, cor = sum(d["fields_total"] for d in ds), sum(d["fields_correct"] for d in ds)
                okd = [d for d in ds if d["ok"]]
                rows.append(
                    {
                        "repeat": rep,
                        "docs": len(ds),
                        "coverage": len(okd) / len(ds),
                        "field_accuracy": cor / max(1, tot),
                        "fields_correct": cor,
                        "fields_total": tot,
                        "mean_latency_s": st.mean(d["latency_s"] for d in okd) if okd else None,
                    }
                )
            answers = flips = correct_flips = 0
            for i in range(len(keys)):
                names = dict.fromkeys(f for rep in reps for f in scored[rep][i]["fields"])
                for f in names:
                    xs = [scored[rep][i]["fields"].get(f) for rep in reps]
                    answers += 1
                    flips += len({_answer(x["predicted"]) if x else None for x in xs}) > 1
                    correct_flips += len({bool(x["correct"]) if x else True for x in xs}) > 1
            entry: dict = {"pipeline": pipe, "variant": v, "repeats": len(reps), "docs": len(keys)}
            entry.update(_spread([r["field_accuracy"] for r in rows], "field_accuracy"))
            entry.update(
                answers=answers,
                flip_rate=flips / answers if answers else 0.0,
                correct_flip_rate=correct_flips / answers if answers else 0.0,
                per_repeat=rows,
            )
            out.append(entry)
    return out


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
    records = read_jsonl(run_dir / "extractions.jsonl")
    expected_ex = expected_extractions(meta, expected, records)
    eagg, edocs = extraction(meta, docs, run_dir, records, manifest, transcripts, basis, expected_ex)
    var_t = transcription_variation(meta, docs, run_dir, transcripts, manifest, tpages)
    var_e = extraction_variation(meta, docs, run_dir, records, manifest, edocs)
    out: dict = {
        "run_id": meta["id"],
        "method": {
            "failures": "counted: failed pages score CER=1/recall 0, failed extractions score 0 (see coverage)",
            "expected_pages": "every page a plan item selected; one never attempted counts as failed"
            if expected is not None
            else "pages with a record (run.json has no plan items)",
            "expected_documents": "every document the pipeline's source was expected to cover (image: all "
            "selected documents; a transcriber: the documents its plan items selected); one never attempted "
            "counts as a failed extraction"
            if expected_ex is not None
            else "documents with an extraction record (run.json has no plan items, or no extraction ran)",
            "invented_optional_fields": "count as wrong: a schema field absent from the ground truth is scored "
            "only if the extractor returned a non-empty value for it (one wrong field)",
            "transcript_per_page": "successful one from the lowest-concurrency run (latest if several; within a "
            "run that repeated every page, its last repeat)"
            if var_t
            else "successful one from the lowest-concurrency run (latest if several)",
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
    if var_t or var_e:  # only runs that repeated something: other scores.json files stay as they were
        out["method"]["variation"] = VARIATION_METHOD
        out["variation"] = {"transcription": var_t, "extraction": var_e}
    return out
