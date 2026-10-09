"""Stage 4: an LLM comments on the computed key facts (summary.md).

The LLM gets the key-facts table (computed in code, see facts.py) and a short description of
the setup. It is asked to interpret, not to restate or recompute. Afterwards every number in
its text is checked against the facts; anything that doesn't appear there is listed in the
report as "unverified".
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from . import explain
from .backends import Request, get_backend
from .config import Config
from .facts import compute, to_md
from .transcribe import call_with_retries

SYSTEM = (
    "You are a careful technical analyst. An engineering team must choose how to read documents. You are "
    "given key facts that were computed exactly from a benchmark. Your job is to interpret them: explain "
    "what they mean, the trade-offs, and what to choose for which goal. You never compute new numbers."
)

INSTRUCTIONS = """Write Markdown with exactly these sections:

## Bottom line
2-3 sentences: what to choose and the single most important reason.

## What the facts say
4-6 bullets interpreting the key facts: what explains the differences between pipelines and readers, what the
failure patterns (most-missed fields, runaway pages, degraded pages) suggest, where the self-hosted model helps
or doesn't.

## Recommendation
A table: goal -> choice -> why. Goals: highest accuracy, lowest latency, lowest cost, checking/second opinion,
and when (if ever) to self-host the OCR model. "Lowest cost" and "lowest latency" must name the cheapest and
fastest pipeline from the key facts.

## Caveats
3-4 bullets on how far this generalises.

Rules:
- Do not compute, estimate or derive numbers. You may quote a number only if it appears verbatim in the key facts
  or setup below; prefer naming the pipeline and saying "most accurate", "cheapest", etc.
- Use the exact pipeline and backend names. Under 400 words."""


def build_prompt(meta: dict, scores: dict) -> tuple[str, str]:
    facts_md = to_md(compute(meta, scores))
    be, o = meta["llm_backends"], meta["ocr"]
    lines = [
        f"- `ocr`: self-hosted {explain.ocr_label(o)} on {o.get('instance_type')} ({o.get('gpu')}, {o.get('host')})"
    ]
    for t in meta.get("transcribers") or []:
        if t != "ocr":
            lines.append(f"- `{t}`: {explain.model_label(be.get(t, {}).get('model'))} (transcriber)")
    agent = meta["extraction"]["agent"]
    lines.append(f"- {explain.agent_note(be.get(agent, {}).get('model'))}")
    d = meta["documents"]
    lines.append(
        f"- documents: {d['count']} synthetic Norwegian documents, {d['page_images']} page images, "
        f"types: {', '.join(d['types'])}; each page clean and degraded (simulated phone photo)"
    )
    pipes = "\n".join(
        f"- `{p}`: {explain.pipeline_text(p, o.get('hf_repo', 'ocr'))}" for p in meta["extraction"]["pipelines"]
    )
    setup = "\n".join(lines)
    prompt = f"{INSTRUCTIONS}\n\n# Setup\n{setup}\n\n# Pipelines\n{explain.ARROW}\n{pipes}\n\n# Key facts\n{facts_md}"
    return prompt, facts_md + "\n" + setup


_NUM = re.compile(r"\d[\d,.\s]*\d|\d")


def _norm(n: str) -> str:
    return re.sub(r"[\s,]", "", n).rstrip(".")


def unverified_numbers(text: str, source: str) -> list[str]:
    """Numbers in `text` that do not occur in `source` (ignoring spacing/thousand separators, % and $)."""
    known = {_norm(m) for m in _NUM.findall(source)}
    known |= {k.lstrip("0") for k in known}
    out = []
    for m in _NUM.findall(text):
        n = _norm(m)
        if len(n.replace(".", "")) < 2 or n in known or n.lstrip("0") in known:
            continue
        # allow rounding of a known value, e.g. 98.8% -> 99%
        try:
            v = float(n)
            if any(abs(v - float(k)) <= 0.5 for k in known if re.fullmatch(r"\d+(\.\d+)?", k)):
                continue
        except ValueError:
            pass
        out.append(m.strip())
    return sorted(set(out))


def run(cfg: Config, run_dir: Path, meta: dict, scores: dict) -> str:
    name = (cfg["bench"].get("summary") or {}).get("agent") or cfg["bench"]["extraction"]["agent"]
    be = get_backend(cfg, name)
    prompt, source = build_prompt(meta, scores)

    async def go():
        await be.warmup()
        resp, _ = await call_with_retries(
            lambda: be.complete(Request(SYSTEM, prompt, max_tokens=2000, temperature=0.2)),
            cfg["bench"].get("max_retries", 4),
        )
        return resp

    resp = asyncio.run(go())
    text = resp.text.strip()
    bad = unverified_numbers(text, source)
    header = (
        f"<!-- LLM comment by {name} ({be.model}) on the computed key facts; "
        f"{resp.prompt_tokens} in / {resp.completion_tokens} out tokens; "
        f"unverified numbers: {', '.join(bad) if bad else 'none'} -->\n"
    )
    (run_dir / "summary.md").write_text(header + text + "\n", encoding="utf-8")
    print(f"summary written by {name}: {len(text)} chars; unverified numbers: {bad or 'none'}", flush=True)
    return text
