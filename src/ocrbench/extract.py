"""Stage 2: an LLM agent extracts the per-type fields for every document.

Pipelines (bench.extraction.pipelines):
  "<transcriber>->agent"  the agent reads that transcriber's text (all pages, in order)
  "image->agent"          the agent reads the page images directly (one-step baseline)

The agent gets the type's schema and must answer with one JSON object. Values must be
copied exactly as written so they can be compared to the ground truth.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from .backends import Request, get_backend
from .backends.vllm_ocr import strip_grounding
from .config import Config
from .transcribe import call_with_retries, load_manifest

SYSTEM = (
    "You extract structured data from documents. You copy values exactly as they appear in the "
    "document (same number formatting, same spelling). You answer with one JSON object and nothing else."
)


def prompt(doc_type: str, schema: dict, source_kind: str) -> str:
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
        f"From {src}, extract these fields:\n"
        + "\n".join(lines)
        + "\nCopy values verbatim. For list fields, return every matching item. "
        "If a value is genuinely absent, omit the key. Reply with JSON only."
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


def first_transcripts(run_dir: Path, backend: str) -> dict[tuple[str, int, str], Path]:
    """(doc_id, page, variant) -> output file of the first successful transcript for that backend."""
    out: dict[tuple[str, int, str], Path] = {}
    f = run_dir / "transcripts.jsonl"
    if not f.exists():
        return out
    for line in f.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        k = (r["doc_id"], r["page"], r["variant"])
        if r["backend"] == backend and r["ok"] and k not in out:
            out[k] = run_dir / r["output_file"]
    return out


async def run(
    cfg: Config, docs: Path, run_dir: Path, *, pipelines: list[str] | None = None, variant: str = "both", limit: int = 0
) -> None:
    ex = cfg["bench"]["extraction"]
    agent = get_backend(cfg, ex["agent"])
    await agent.warmup()
    schemas = json.loads((docs / "schemas.json").read_text(encoding="utf-8"))
    manifest = load_manifest(docs)
    if limit:
        manifest = sorted(manifest, key=lambda d: (int(d["id"].rsplit("-", 1)[1]), d["id"]))[:limit]
    variants = ["clean", "degraded"] if variant == "both" else [variant]
    sem = asyncio.Semaphore(int(ex.get("concurrency", 8)))
    strip = bool(cfg["ocr"].get("strip_grounding_tokens", True))
    max_retries = cfg["bench"].get("max_retries", 4)
    out = run_dir / "extractions.jsonl"
    if out.exists() and not pipelines:  # full re-run: start clean
        out.rename(run_dir / "extractions.prev.jsonl")

    for pipe in pipelines or ex["pipelines"]:
        source = pipe.split("->")[0]
        slug = pipe.replace("->", "__")
        texts = first_transcripts(run_dir, source) if source != "image" else {}
        jobs = []
        for d in manifest:
            for v in variants:
                if source == "image":
                    req = Request(
                        SYSTEM,
                        prompt(d["type"], schemas[d["type"]], "image"),
                        [docs / p[v] for p in d["pages"]],
                        max_tokens=2048,
                    )
                else:
                    files = [texts.get((d["id"], p["n"], v)) for p in d["pages"]]
                    if not all(files):
                        continue  # source has no transcript for every page
                    parts = []
                    for p, f in zip(d["pages"], files, strict=True):
                        t = f.read_text(encoding="utf-8")
                        if source == "ocr" and strip:
                            t = strip_grounding(t)
                        parts.append(f"--- page {p['n']} ---\n{t}")
                    req = Request(
                        SYSTEM,
                        prompt(d["type"], schemas[d["type"]], "text") + "\n\n" + "\n\n".join(parts),
                        max_tokens=2048,
                    )
                jobs.append((d, v, req))
        if not jobs:
            print(f"[extract {pipe}] nothing to do (no transcripts for {source!r} in {run_dir})", flush=True)
            continue
        print(f"[extract {pipe}] {len(jobs)} documents via {agent.name}", flush=True)

        async def one(d, v, req, pipe=pipe, source=source, slug=slug):
            async with sem:
                t0 = time.perf_counter()
                rec = {
                    "pipeline": pipe,
                    "source": source,
                    "agent": agent.name,
                    "agent_model": agent.model,
                    "doc_id": d["id"],
                    "variant": v,
                    "ok": False,
                    "error": None,
                    "output_file": None,
                    "prompt_tokens": None,
                    "completion_tokens": None,
                }
                try:
                    resp, _ = await call_with_retries(lambda: agent.complete(req), max_retries)
                    rec.update(prompt_tokens=resp.prompt_tokens, completion_tokens=resp.completion_tokens)
                    fields = parse_json(resp.text)
                    rel = Path("outputs") / "extract" / slug / f"{d['id']}.{v}.json"
                    (run_dir / rel).parent.mkdir(parents=True, exist_ok=True)
                    (run_dir / rel).write_text(json.dumps(fields, ensure_ascii=False, indent=1), encoding="utf-8")
                    rec.update(ok=True, output_file=str(rel))
                except Exception as e:  # noqa: BLE001
                    rec["error"] = f"{type(e).__name__}: {e}"[:400]
                rec["latency_s"] = time.perf_counter() - t0
                return rec

        recs = await asyncio.gather(*(one(d, v, r) for d, v, r in jobs))
        with out.open("a", encoding="utf-8") as fh:
            for r in recs:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        bad = [r for r in recs if not r["ok"]]
        print(f"  ok {len(recs) - len(bad)}/{len(recs)}", flush=True)
        for r in bad[:5]:
            print(f"  ERR {r['doc_id']} {r['variant']}: {r['error']}", flush=True)
