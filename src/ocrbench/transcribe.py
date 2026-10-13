"""Stage 1: send every page image to a transcriber backend at a given concurrency."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .backends import HTTPError, Request, get_backend
from .backends.base import RETRYABLE
from .config import Config
from .redact import error_text

SYSTEM = "You are a precise OCR engine. You transcribe documents; you never summarise, translate or correct them."
PROMPT = (
    "Transcribe this document page to Markdown. Reproduce all text exactly as written, in reading order, "
    "including every number, date, account number and table (as Markdown tables). Do not summarise, "
    "translate, correct or add anything. Output only the transcription."
)


@dataclass
class PageRef:
    doc_id: str
    page: int
    variant: str
    path: Path

    @property
    def key(self) -> str:
        return f"{self.doc_id}.p{self.page}.{self.variant}"


def load_manifest(docs: Path) -> list[dict]:
    return json.loads((docs / "manifest.json").read_text(encoding="utf-8"))


def pages(docs: Path, variant: str = "both", limit: int = 0, types: list[str] | None = None) -> list[PageRef]:
    out = []
    for d in load_manifest(docs):
        if types and d["type"] not in types:
            continue
        for p in d["pages"]:
            for v in ("clean", "degraded"):
                if variant in (v, "both"):
                    out.append(PageRef(d["id"], p["n"], v, docs / p[v]))
    # Interleave types so a --limit sample still covers several document kinds.
    out.sort(key=lambda r: (int(r.doc_id.rsplit("-", 1)[1]), r.variant != "clean", r.doc_id, r.page))
    return out[:limit] if limit else out


@dataclass
class Transcript:
    run_id: str
    backend: str
    model: str
    doc_id: str
    page: int
    variant: str
    concurrency: int
    started: float
    latency_s: float
    ok: bool
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    finish_reason: str | None = None
    retries: int = 0
    error: str | None = None
    output_file: str | None = None


async def call_with_retries(fn, max_retries: int):
    """Returns (result, retries, seconds of the successful attempt): backoff sleeps are not latency."""
    retries = 0
    while True:
        t0 = time.perf_counter()
        try:
            return await fn(), retries, time.perf_counter() - t0
        except HTTPError as e:
            if e.status in RETRYABLE and retries < max_retries:
                retries += 1
                await asyncio.sleep(min(30.0, 2.0**retries))
                continue
            raise


async def _one(be, ref: PageRef, sem, *, conc, run_id, out_dir, max_retries, rep=0, sink=None) -> Transcript:
    async with sem:
        t0, started = time.perf_counter(), time.time()
        req = Request(SYSTEM, PROMPT, [ref.path])
        try:
            resp, retries, lat = await call_with_retries(lambda: be.complete(req), max_retries)
        except Exception as e:  # noqa: BLE001 — record and keep the batch going
            rec = Transcript(
                run_id,
                be.name,
                be.model,
                ref.doc_id,
                ref.page,
                ref.variant,
                conc,
                started,
                time.perf_counter() - t0,
                False,
                error=error_text(e),
            )
        else:
            suffix = f".x{rep}" if rep else ""
            rel = Path("outputs") / be.name / f"{ref.key}.r{run_id}{suffix}.md"
            (out_dir / rel).parent.mkdir(parents=True, exist_ok=True)
            (out_dir / rel).write_text(resp.text, encoding="utf-8")
            rec = Transcript(
                run_id,
                be.name,
                be.model,
                ref.doc_id,
                ref.page,
                ref.variant,
                conc,
                started,
                lat,
                True,
                resp.prompt_tokens,
                resp.completion_tokens,
                resp.finish_reason,
                retries,
                None,
                str(rel),
            )
        if sink:
            sink(rec)
        return rec


async def run(
    cfg: Config,
    docs: Path,
    out_dir: Path,
    *,
    backend: str,
    concurrency: int = 1,
    variant: str = "both",
    limit: int = 0,
    repeat: int = 1,
    warmup: bool = True,
) -> dict:
    base = pages(docs, variant, limit)
    refs = [(r, i) for i in range(repeat) for r in base]
    be = get_backend(cfg, backend)
    run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"  # unique per plan item
    # boto3 backends run in threads: make sure the pool never caps the requested concurrency
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=max(32, concurrency + 8)))
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[{be.name}] {be.model}: {len(refs)} pages at concurrency {concurrency}", flush=True)
    await be.warmup()
    if warmup and refs:  # untimed: token fetch, CUDA graphs, connection pools
        try:
            await be.complete(Request(SYSTEM, PROMPT, [refs[0][0].path]))
        except Exception as e:  # noqa: BLE001
            print(f"  warmup failed: {e}", flush=True)
    sem = asyncio.Semaphore(concurrency)
    t0, started = time.perf_counter(), time.time()
    fh = (out_dir / "transcripts.jsonl").open("a", encoding="utf-8")

    def sink(rec: Transcript) -> None:  # written as each request finishes: a crash keeps finished work
        fh.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")
        fh.flush()

    kw = dict(
        conc=concurrency, run_id=run_id, out_dir=out_dir, max_retries=cfg["bench"].get("max_retries", 4), sink=sink
    )
    try:
        results = await asyncio.gather(*(_one(be, r, sem, rep=i, **kw) for r, i in refs))
    finally:
        fh.close()
    wall = time.perf_counter() - t0
    meta = {
        "run_id": run_id,
        "backend": be.name,
        "model": be.model,
        "concurrency": concurrency,
        "pages": len(refs),
        "wall_s": wall,
        "started": started,
        "instance_type": getattr(be, "instance_type", None),
    }
    with (out_dir / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(meta) + "\n")

    ok = sorted(r.latency_s for r in results if r.ok)
    print(
        f"  ok {len(ok)}/{len(results)}  wall {wall:.1f}s  {len(ok) / wall * 60 if wall else 0:.1f} pages/min",
        flush=True,
    )
    if ok:
        print(f"  latency p50 {ok[len(ok) // 2]:.2f}s  max {ok[-1]:.2f}s", flush=True)
    for r in results:
        if not r.ok:
            print(f"  ERR {r.doc_id} p{r.page} {r.variant}: {r.error}", flush=True)
    return meta
