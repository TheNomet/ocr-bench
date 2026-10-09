"""Score transcripts and extractions against ground truth; aggregate speed and cost."""

from __future__ import annotations

import json
import re
import statistics as st
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path

from .backends.vllm_ocr import strip_grounding
from .config import Config
from .transcribe import load_manifest

_TAGS = re.compile(r"</?[a-zA-Z][^>]*>")
_NUM = re.compile(r"\d[\d.,:/\-]*\d|\d")


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
    try:
        from rapidfuzz.distance import Levenshtein

        return Levenshtein.distance(a, b)  # works on str and on lists of words
    except ImportError:
        pass
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def page_metrics(gt_text: str, gt_numbers: list[str], hyp: str) -> dict:
    g, h = normalise(gt_text), normalise(hyp)
    gw, hw = g.split(), h.split()
    nh, ng = re.sub(r"\s", "", h), re.sub(r"\s", "", g)
    missing = [n for n in gt_numbers if re.sub(r"\s", "", n) not in nh]
    halluc = [t for t in _NUM.findall(h) if len(t) >= 3 and t not in ng]
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


def norm_value(v) -> str:
    s = unicodedata.normalize("NFC", str(v)).replace("\u00a0", " ").replace("\u202f", " ")
    s = re.sub(r"\s+", " ", s).strip().strip(".,;:").casefold()
    return re.sub(r"^(kr|nok)\s*", "", s)


def field_correct(truth, pred) -> bool:
    if pred is None:
        return False
    if isinstance(truth, list):
        p = pred if isinstance(pred, list) else [pred]
        return sorted(map(norm_value, truth)) == sorted(map(norm_value, p))
    return norm_value(truth) == norm_value(pred if not isinstance(pred, list) else " ".join(map(str, pred)))


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.exists() else []


def _pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def _price(cfg: Config, backend: str) -> dict | None:
    return None if backend == "ocr" else cfg["llm_backends"].get(backend, {}).get("price_per_mtok")


def score(cfg: Config, docs: Path, run_dir: Path, tag: str, gpu_hour: float | None = None) -> dict:
    manifest = {d["id"]: d for d in load_manifest(docs)}
    transcripts = _jsonl(run_dir / "transcripts.jsonl")
    runs = _jsonl(run_dir / "runs.jsonl")
    strip = bool(cfg["ocr"].get("strip_grounding_tokens", True))
    gpu_hour = gpu_hour or cfg["ocr"].get("sagemaker", {}).get("price_per_hour_usd")

    # ---- speed / cost per run ----
    speed = []
    for run in runs:
        rs = [r for r in transcripts if r["run_id"] == run["run_id"] and r["backend"] == run["backend"]]
        ok = [r for r in rs if r["ok"]]
        lats = [r["latency_s"] for r in ok]
        pt = st.mean([r["prompt_tokens"] for r in ok if r.get("prompt_tokens")] or [0])
        ct = st.mean([r["completion_tokens"] for r in ok if r.get("completion_tokens")] or [0])
        ppm = len(ok) / run["wall_s"] * 60 if run["wall_s"] else 0
        price = _price(cfg, run["backend"])
        if price:
            cost = (pt * price.get("input", 0) + ct * price.get("output", 0)) / 1e6 * 1000
        elif run["backend"] == "ocr" and gpu_hour and ppm:
            cost = gpu_hour / (ppm * 60) * 1000  # at this run's throughput, GPU fully used
        else:
            cost = None
        speed.append(
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
                "retries": sum(r["retries"] for r in rs),
                "cost_per_1k_pages_usd": cost,
            }
        )

    # ---- transcription quality: first successful transcript per (backend, page, variant) ----
    seen, tpages = set(), []
    for r in transcripts:
        k = (r["backend"], r["doc_id"], r["page"], r["variant"])
        if not r["ok"] or k in seen or r["doc_id"] not in manifest:
            continue
        seen.add(k)
        pg = next(p for p in manifest[r["doc_id"]]["pages"] if p["n"] == r["page"])
        gt = (docs / pg["gt"]).read_text(encoding="utf-8")
        nums = json.loads((docs / pg["gt_meta"]).read_text(encoding="utf-8"))["numbers"]
        hyp = (run_dir / r["output_file"]).read_text(encoding="utf-8")
        if r["backend"] == "ocr" and strip:
            hyp = strip_grounding(hyp)
        tpages.append(
            {
                "backend": r["backend"],
                "doc_id": r["doc_id"],
                "page": r["page"],
                "variant": r["variant"],
                "output_file": r["output_file"],
                **page_metrics(gt, nums, hyp),
            }
        )
    tagg = []
    for be in sorted({p["backend"] for p in tpages}):
        for v in ("clean", "degraded"):
            s = [p for p in tpages if p["backend"] == be and p["variant"] == v]
            if s:
                tagg.append(
                    {
                        "backend": be,
                        "variant": v,
                        "pages": len(s),
                        **{
                            m: st.mean(p[m] for p in s)
                            for m in ("cer", "wer", "word_recall", "word_precision", "num_recall")
                        },
                        "cer_median": st.median(p["cer"] for p in s),
                        "runaway": sum(p["len_ratio"] > 3 for p in s),
                        "num_missing": sum(p["num_missing"] for p in s),
                        "num_hallucinated": sum(p["num_hallucinated"] for p in s),
                    }
                )

    # ---- extraction ----
    edocs = []
    extractions = _jsonl(run_dir / "extractions.jsonl")
    seen_e = set()
    for e in extractions:
        k = (e["pipeline"], e["doc_id"], e["variant"])
        if k in seen_e or e["doc_id"] not in manifest:
            continue
        seen_e.add(k)
        truth = json.loads((docs / manifest[e["doc_id"]]["fields"]).read_text(encoding="utf-8"))["fields"]
        pred = json.loads((run_dir / e["output_file"]).read_text(encoding="utf-8")) if e["ok"] else {}
        fields = {
            f: {"truth": t, "predicted": pred.get(f), "correct": field_correct(t, pred.get(f))}
            for f, t in truth.items()
        }
        n_ok = sum(x["correct"] for x in fields.values())
        edocs.append(
            {
                "pipeline": e["pipeline"],
                "doc_id": e["doc_id"],
                "variant": e["variant"],
                "ok": e["ok"],
                "accuracy": n_ok / max(1, len(fields)),
                "fields_total": len(fields),
                "fields_correct": n_ok,
                "fields": fields,
                "output_file": e["output_file"],
                "latency_s": e["latency_s"],
                "prompt_tokens": e.get("prompt_tokens"),
                "completion_tokens": e.get("completion_tokens"),
                "agent": e["agent"],
                "source": e["source"],
            }
        )

    # per-doc source transcription latency (sum over pages, concurrency-1 runs preferred)
    src_lat: dict[tuple[str, str, str], float] = {}
    for r in sorted(transcripts, key=lambda r: r["concurrency"]):
        k = (r["backend"], r["doc_id"], r["variant"])
        if r["ok"] and (r["backend"], r["doc_id"], r["page"], r["variant"], "lat") not in seen:
            seen.add((r["backend"], r["doc_id"], r["page"], r["variant"], "lat"))
            src_lat[k] = src_lat.get(k, 0.0) + r["latency_s"]
    src_cost: dict[str, float | None] = {}
    for row in speed:
        src_cost.setdefault(row["backend"], row["cost_per_1k_pages_usd"])

    eagg = []
    for pipe in sorted({d["pipeline"] for d in edocs}):
        for v in ("clean", "degraded"):
            s = [d for d in edocs if d["pipeline"] == pipe and d["variant"] == v]
            if not s:
                continue
            src = s[0]["source"]
            lat = st.mean(d["latency_s"] for d in s)
            e2e = st.mean(d["latency_s"] + src_lat.get((src, d["doc_id"], v), 0.0) for d in s)
            price = _price(cfg, s[0]["agent"])
            cost = None
            if price:
                pt = st.mean([d["prompt_tokens"] or 0 for d in s])
                ct = st.mean([d["completion_tokens"] or 0 for d in s])
                cost = (pt * price.get("input", 0) + ct * price.get("output", 0)) / 1e6 * 1000
                if src != "image":
                    pages_per_doc = st.mean(len(manifest[d["doc_id"]]["pages"]) for d in s)
                    sc = src_cost.get(src)
                    cost = cost + sc * pages_per_doc if sc is not None else None
            tot = sum(d["fields_total"] for d in s)
            cor = sum(d["fields_correct"] for d in s)
            eagg.append(
                {
                    "pipeline": pipe,
                    "variant": v,
                    "docs": len(s),
                    "field_accuracy": cor / max(1, tot),
                    "fields_total": tot,
                    "fields_correct": cor,
                    "mean_latency_s": lat,
                    "end_to_end_latency_s": e2e if src != "image" else lat,
                    "cost_per_1k_docs_usd": cost,
                }
            )

    return {
        "tag": tag,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "summary_md": "",
        "speed": speed,
        "transcription": {"aggregate": tagg, "pages": tpages},
        "extraction": {"aggregate": eagg, "docs": edocs},
    }
