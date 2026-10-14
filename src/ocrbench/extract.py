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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import postprocess, prompts, view
from .backends import Backend, Request, get_backend
from .config import Config
from .fileio import read_jsonl
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


def latest_transcripts(run_dir: Path, backend: str) -> dict[tuple[str, int, str], Path]:
    """(doc_id, page, variant) -> output file of the transcript scoring uses (score.pick_transcript)."""
    from .score import pick_transcript

    chosen: dict = {}
    out: dict[tuple[str, int, str], Path] = {}
    for r in read_jsonl(run_dir / "transcripts.jsonl"):
        k = (r["doc_id"], r["page"], r["variant"])
        if r["backend"] == backend:
            chosen[k] = pick_transcript(chosen.get(k), r)
    for k, r in chosen.items():
        if r:
            out[k] = run_dir / r["output_file"]
    return out


def targets(manifest: list[dict], run_dir: Path, variants: list[str], limit: int = 0) -> set[tuple[str, str]]:
    """(doc_id, variant) pairs to extract: the same set for every pipeline. The pairs stage 1 attempted,
    if it ran; otherwise the first `limit` documents (all if 0)."""
    attempted = {(r["doc_id"], r["variant"]) for r in read_jsonl(run_dir / "transcripts.jsonl")}
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


async def extract_one(extractor, build, rec: dict, run_dir: Path, slug: str, max_retries: int) -> dict:
    """Build (`build()` -> Request, or None if the source lacks a page) and run one request, and complete its
    record (the record keeps the historical `agent` field names). latency_s is the last attempt's request
    time (backoff sleeps excluded), for a failure too. Repeat i > 0 (`rec["repeat"]`) writes `.x<i>.json`."""
    rec = {**rec, "ok": False, "error": None, "output_file": None, "prompt_tokens": None, "completion_tokens": None}
    try:  # e.g. a transcript recorded as ok whose output file is gone: a failed extraction, not a failed stage
        req = build()
    except Exception as e:  # noqa: BLE001
        return {**rec, "error": f"building the request: {error_text(e)}", "latency_s": 0.0}
    if req is None:
        return {**rec, "error": "missing transcript: the source failed on at least one page", "latency_s": 0.0}
    attempt_s = 0.0
    try:
        resp, _, attempt_s = await call_with_retries(lambda: extractor.complete(req), max_retries)
        rec.update(prompt_tokens=resp.prompt_tokens, completion_tokens=resp.completion_tokens)
        fields = parse_json(resp.text)
        rep = int(rec.get("repeat") or 0)
        rel = Path("outputs") / "extract" / slug / f"{rec['doc_id']}.{rec['variant']}{f'.x{rep}' if rep else ''}.json"
        (run_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (run_dir / rel).write_text(json.dumps(fields, ensure_ascii=False, indent=1), encoding="utf-8")
        rec.update(ok=True, output_file=str(rel), latency_s=attempt_s)
    except Exception as e:  # noqa: BLE001 — record and keep going
        rec.update(error=error_text(e), latency_s=getattr(e, "attempt_s", attempt_s))
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
    repeat: int = 1,
) -> None:
    """Extract every pipeline, every document `repeat` times (run.json `extraction.repeat`; records carry
    `repeat` 0..N-1). For a `<transcriber>->X` pipeline every repeat reads the same transcript (the one
    scoring uses), so repeats measure the extractor alone. The extractor of each pipeline is the one run.json
    recorded (`meta`), so a resumed run uses the same models even if the config changed since; the config's
    agent is only the fallback for a pipeline run.json doesn't know."""
    ex = cfg["bench"]["extraction"]
    pr = prompts.of(meta, "extract")
    steps = {k: b.get("postprocess") or [] for k, b in meta["backends"].items()}
    schemas = json.loads((docs / "schemas.json").read_text(encoding="utf-8"))
    manifest = load_manifest(docs)
    variants = ["clean", "degraded"] if variant == "both" else [variant]
    todo = targets(manifest, run_dir, variants, limit)
    concurrency = int(ex["concurrency"])
    sem = asyncio.Semaphore(concurrency)
    # boto3 backends run in threads: make sure the pool never caps the requested concurrency
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=max(32, concurrency + 8)))
    max_retries = int(cfg["bench"]["max_retries"])
    # A re-run replaces the previous extraction pass instead of mixing with it. Records are appended to a
    # partial file as each one finishes; it becomes extractions.jsonl (the old one -> .prev) only when the
    # pass ends, so a crash keeps the previous complete pass and the finished part of the new one.
    out, partial = run_dir / "extractions.jsonl", run_dir / "extractions.partial.jsonl"
    extractors: dict[str, Backend] = {}
    written = 0
    fh = partial.open("w", encoding="utf-8")

    def sink(rec: dict) -> None:
        nonlocal written
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()
        written += 1

    try:
        for pipe in pipelines or ex["pipelines"]:
            source: str = view.source(pipe)
            slug = pipe.replace("->", "__")
            key: str = view.extractor(meta, pipe) or view.extractor_key(ex["agent"], pipe)
            if key not in extractors:
                extractors[key] = get_backend(cfg, key)
                await extractors[key].warmup()
            extractor = extractors[key]
            texts = latest_transcripts(run_dir, source) if source != "image" else {}
            src_steps = steps.get(source) or []
            jobs = [
                (d, v, i) for i in range(max(1, repeat)) for d in manifest for v in variants if (d["id"], v) in todo
            ]
            if not jobs:
                print(f"[extract {pipe}] nothing to do (no documents in {run_dir})", flush=True)
                continue
            times = f" x {repeat} repeats" if repeat > 1 else ""
            print(f"[extract {pipe}] {len(jobs) // max(1, repeat)} documents{times} via {extractor.name}", flush=True)
            base = {"pipeline": pipe, "source": source, "agent": extractor.name, "agent_model": extractor.model}

            def build(d, v, source=source, texts=texts, src_steps=src_steps) -> Request | None:
                schema = schemas[d["type"]]
                return build_request(d, v, source, docs=docs, schema=schema, pr=pr, texts=texts, steps=src_steps)

            async def one(d, v, i, base=base, slug=slug, extractor=extractor, build=build) -> dict:
                async with sem:
                    rec = {**base, "doc_id": d["id"], "variant": v, "repeat": i}
                    rec = await extract_one(extractor, lambda: build(d, v), rec, run_dir, slug, max_retries)
                    sink(rec)
                    return rec

            recs = await asyncio.gather(*(one(d, v, i) for d, v, i in jobs))
            bad = [r for r in recs if not r["ok"]]
            print(f"  ok {len(recs) - len(bad)}/{len(recs)}", flush=True)
            for r in bad[:5]:
                print(f"  ERR {r['doc_id']} {r['variant']} (repeat {r['repeat']}): {r['error']}", flush=True)
    finally:
        fh.close()
    if not written:  # nothing to do: keep the last pass
        partial.unlink()
        return
    if out.exists():
        out.replace(run_dir / "extractions.prev.jsonl")
    partial.replace(out)
