"""Stage 2: an LLM agent extracts the per-type fields for every document.

Pipelines (bench.extraction.pipelines), `<input>-><extractor>`:
  "<transcriber>->agent"  the default agent reads that transcriber's text (all pages, in order),
                          after the transcriber's post-processing steps
  "image->agent"          the agent reads the page images directly (one-step baseline)
  "<input>-><backend>"    that backend extracts instead of the default agent

The agent gets the type's schema and must answer with one JSON object. Values must be
copied exactly as written so they can be compared to the ground truth.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from . import postprocess, prompts
from .backends import Request, get_backend
from .config import Config
from .redact import error_text
from .transcribe import call_with_retries, load_manifest


def prompt(doc_type: str, schema: dict, source_kind: str, rules: str = prompts.DEFAULTS["extract"]["rules"]) -> str:
    lines = []
    for k, f in schema["fields"].items():
        kind = "list of strings" if f["type"] == "list" else "string"
        opt = " (omit if not present)" if f.get("optional") else ""
        lines.append(f'- "{k}" ({kind}){opt}: {f["description"]}')
    src = (
        "the OCR transcript below (it may contain recognition errors and odd reading order)"
        if source_kind == "text"
        else "the attached page image(s)"
    )
    return (
        f"Document type: {doc_type} — {schema['description']}.\n"
        f"From {src}, extract these fields:\n" + "\n".join(lines) + "\n" + rules
    )


def parse_json(text: str) -> dict:
    body = (text or "").strip()
    if body.startswith("```"):
        body = body.strip("`").removeprefix("json").strip()
    start = body.find("{")
    if start == -1:
        raise json.JSONDecodeError("no JSON object", body, 0)
    obj, _ = json.JSONDecoder().raw_decode(body[start:])  # ignores trailing prose / a second object
    return obj


def _records(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.exists() else []


def latest_transcripts(run_dir: Path, backend: str) -> dict[tuple[str, int, str], Path]:
    """(doc_id, page, variant) -> output file of the transcript scoring uses (score.pick_transcript)."""
    from .score import pick_transcript

    chosen: dict = {}
    out: dict[tuple[str, int, str], Path] = {}
    f = run_dir / "transcripts.jsonl"
    if not f.exists():
        return out
    for line in f.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        k = (r["doc_id"], r["page"], r["variant"])
        if r["backend"] == backend:
            chosen[k] = pick_transcript(chosen.get(k), r)
    for k, r in chosen.items():
        if r:
            out[k] = run_dir / r["output_file"]
    return out


def extractor_key(ex: dict, pipe: str) -> str:
    """`<input>->agent` uses bench.extraction.agent; `<input>-><backend>` uses that backend."""
    key = pipe.split("->", 1)[1]
    return ex["agent"] if key == "agent" else key


def targets(manifest: list[dict], run_dir: Path, variants: list[str], limit: int = 0) -> set[tuple[str, str]]:
    """(doc_id, variant) pairs to extract: the same set for every pipeline. The pairs stage 1 attempted,
    if it ran; otherwise the first `limit` documents (all if 0)."""
    attempted = {(r["doc_id"], r["variant"]) for r in _records(run_dir / "transcripts.jsonl")}
    if attempted:
        return {(d["id"], v) for d in manifest for v in variants if (d["id"], v) in attempted}
    chosen = sorted(manifest, key=lambda d: (int(d["id"].rsplit("-", 1)[1]), d["id"]))[: limit or None]
    return {(d["id"], v) for d in chosen for v in variants}


def build_request(
    doc: dict, variant: str, source: str, *, docs: Path, schema: dict, pr: dict, texts: dict, steps: list[str]
) -> Request | None:
    """The extraction request for one document, or None if the source has no transcript of some page
    (recorded as a failure, so it counts against the pipeline)."""
    if source == "image":
        return Request(
            pr["system"],
            prompt(doc["type"], schema, "image", pr["rules"]),
            [docs / p[variant] for p in doc["pages"]],
            max_tokens=int(pr["max_tokens"]),
        )
    parts = []
    for p in doc["pages"]:
        f = texts.get((doc["id"], p["n"], variant))
        if f is None:
            return None
        parts.append(f"--- page {p['n']} ---\n{postprocess.apply(steps, f.read_text(encoding='utf-8'))}")
    return Request(
        pr["system"],
        prompt(doc["type"], schema, "text", pr["rules"]) + "\n\n" + "\n\n".join(parts),
        max_tokens=int(pr["max_tokens"]),
    )


async def extract_one(extractor, req: Request | None, rec: dict, run_dir: Path, slug: str, max_retries: int) -> dict:
    """Run one request and complete its record (the record keeps the historical `agent` field names)."""
    rec = {**rec, "ok": False, "error": None, "output_file": None, "prompt_tokens": None, "completion_tokens": None}
    if req is None:
        return {**rec, "error": "missing transcript: the source failed on at least one page", "latency_s": 0.0}
    t0 = time.perf_counter()
    try:
        resp, _, attempt_s = await call_with_retries(lambda: extractor.complete(req), max_retries)
        rec.update(prompt_tokens=resp.prompt_tokens, completion_tokens=resp.completion_tokens)
        fields = parse_json(resp.text)
        rel = Path("outputs") / "extract" / slug / f"{rec['doc_id']}.{rec['variant']}.json"
        (run_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (run_dir / rel).write_text(json.dumps(fields, ensure_ascii=False, indent=1), encoding="utf-8")
        rec.update(ok=True, output_file=str(rel), latency_s=attempt_s)
    except Exception as e:  # noqa: BLE001 — record and keep going
        rec.update(error=error_text(e), latency_s=time.perf_counter() - t0)
    return rec


async def run(
    cfg: Config,
    docs: Path,
    run_dir: Path,
    *,
    meta: dict,
    pipelines: list[str] | None = None,
    variant: str = "both",
    limit: int = 0,
) -> None:
    ex = cfg["bench"]["extraction"]
    pr = prompts.of(meta, "extract")
    steps = {k: b.get("postprocess") or [] for k, b in meta["backends"].items()}
    schemas = json.loads((docs / "schemas.json").read_text(encoding="utf-8"))
    manifest = load_manifest(docs)
    variants = ["clean", "degraded"] if variant == "both" else [variant]
    todo = targets(manifest, run_dir, variants, limit)
    sem = asyncio.Semaphore(int(ex.get("concurrency", 8)))
    max_retries = int(cfg["bench"].get("max_retries", 4))
    out = run_dir / "extractions.jsonl"
    if out.exists():  # a re-run replaces the previous extraction pass instead of mixing with it
        out.replace(run_dir / "extractions.prev.jsonl")
    extractors: dict = {}

    for pipe in pipelines or ex["pipelines"]:
        source, key, slug = pipe.split("->")[0], extractor_key(ex, pipe), pipe.replace("->", "__")
        if key not in extractors:
            extractors[key] = get_backend(cfg, key)
            await extractors[key].warmup()
        extractor = extractors[key]
        texts = latest_transcripts(run_dir, source) if source != "image" else {}
        src_steps = steps.get(source) or []
        jobs = [
            (
                d,
                v,
                build_request(d, v, source, docs=docs, schema=schemas[d["type"]], pr=pr, texts=texts, steps=src_steps),
            )
            for d in manifest
            for v in variants
            if (d["id"], v) in todo
        ]
        if not jobs:
            print(f"[extract {pipe}] nothing to do (no documents in {run_dir})", flush=True)
            continue
        print(f"[extract {pipe}] {len(jobs)} documents via {extractor.name}", flush=True)
        base = {"pipeline": pipe, "source": source, "agent": extractor.name, "agent_model": extractor.model}

        async def one(d, v, req, base=base, slug=slug, extractor=extractor):
            async with sem:
                rec = {**base, "doc_id": d["id"], "variant": v}
                return await extract_one(extractor, req, rec, run_dir, slug, max_retries)

        recs = await asyncio.gather(*(one(d, v, r) for d, v, r in jobs))
        with out.open("a", encoding="utf-8") as fh:
            fh.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in recs)
        bad = [r for r in recs if not r["ok"]]
        print(f"  ok {len(recs) - len(bad)}/{len(recs)}", flush=True)
        for r in bad[:5]:
            print(f"  ERR {r['doc_id']} {r['variant']}: {r['error']}", flush=True)
