"""Stage 1 records: failures keep their retry count and request time, and never crash the batch."""

from __future__ import annotations

import asyncio
import json
import random

import pytest

from ocrbench import transcribe
from ocrbench.backends.base import HTTPError, Response


class Fake:
    def __init__(self, name="fake", outcomes=None):
        self.name, self.model, self.instance_type = name, f"m:{name}", None
        self.outcomes = list(outcomes or [])
        self.calls = 0

    async def warmup(self):
        pass

    async def complete(self, req):
        self.calls += 1
        o = self.outcomes.pop(0) if self.outcomes else Response("text", 1, 1, "stop")
        if isinstance(o, Exception):
            raise o
        return o


def test_failure_carries_retries_and_last_attempt_time(monkeypatch):
    async def no_sleep(_s):
        pass

    monkeypatch.setattr(transcribe.asyncio, "sleep", no_sleep)

    async def always_busy():
        raise HTTPError(429, "slow down")

    with pytest.raises(HTTPError) as ei:
        asyncio.run(transcribe.call_with_retries(always_busy, 3))
    assert ei.value.retries == 3 and 0 <= ei.value.attempt_s < 1  # pyright: ignore[reportAttributeAccessIssue]

    async def bad():
        raise ValueError("not retried")

    with pytest.raises(ValueError) as ev:
        asyncio.run(transcribe.call_with_retries(bad, 3))
    assert ev.value.retries == 0  # pyright: ignore[reportAttributeAccessIssue]


def test_backoff_is_jittered_and_capped():
    random.seed(1)
    delays = [transcribe.backoff_s(2) for _ in range(50)]
    assert all(2.0 <= d <= 4.0 for d in delays) and len(set(delays)) > 1
    assert all(15.0 <= transcribe.backoff_s(9) <= 30.0 for _ in range(20))


def _one(be, tmp_path, max_retries=2):
    ref = transcribe.PageRef("invoice-00", 1, "clean", tmp_path / "p1.png")
    prompt = transcribe.DEFAULTS["transcribe"]
    sem = asyncio.Semaphore(1)
    return asyncio.run(
        transcribe._one(be, ref, sem, conc=1, run_id="r", out_dir=tmp_path, max_retries=max_retries, prompt=prompt)
    )


def test_failed_page_records_real_retries_and_excludes_backoff(tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe, "backoff_s", lambda _n: 0.2)
    rec = _one(Fake(outcomes=[HTTPError(503, "down")] * 5), tmp_path, max_retries=2)
    assert not rec.ok and rec.retries == 2 and "503" in (rec.error or "")
    assert rec.latency_s < 0.2  # the last attempt, not the 0.4 s of backoff sleeps


def test_success_after_retries_records_them(tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe, "backoff_s", lambda _n: 0.0)
    rec = _one(Fake(outcomes=[HTTPError(429, "x")]), tmp_path)
    assert rec.ok and rec.retries == 1 and rec.finish_reason == "stop"


def test_non_string_text_is_a_failed_page(tmp_path):
    rec = _one(Fake(outcomes=[Response(["not", "text"])]), tmp_path)  # pyright: ignore[reportArgumentType]
    assert not rec.ok and rec.output_file is None and rec.error


def test_unwritable_output_is_a_failed_page_and_the_run_finishes(fake_docs, tmp_path, monkeypatch):
    out = tmp_path / "run"
    (out / "outputs").mkdir(parents=True)
    (out / "outputs" / "fake").write_text("a file where the folder should be")
    monkeypatch.setattr(transcribe, "get_backend", lambda c, n: Fake(n))
    cfg = {"bench": {"max_retries": 0}}
    meta = asyncio.run(transcribe.run(cfg, fake_docs, out, backend="fake", limit=2, warmup=False))  # pyright: ignore[reportArgumentType]
    recs = [json.loads(x) for x in (out / "transcripts.jsonl").read_text().splitlines()]
    assert len(recs) == 2 and not any(r["ok"] for r in recs)
    assert json.loads((out / "runs.jsonl").read_text())["run_id"] == meta["run_id"]
