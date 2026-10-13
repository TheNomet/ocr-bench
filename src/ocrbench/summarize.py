"""Stage 4: an LLM writes the recommendation from the computed key facts (summary.md).

The LLM gets the key-facts table (computed in code, see facts.py) and a short description of
the setup. It is asked to interpret, not to restate or recompute. Afterwards every number in
its text is checked against the facts; anything that doesn't appear there is listed in the
report as "unverified".
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from . import explain, prompts, view
from .backends import Request, get_backend
from .config import Config
from .facts import compute, to_md
from .transcribe import call_with_retries

INSTRUCTIONS = """Write Markdown with exactly these sections:

## Bottom line
2-3 sentences: what to choose and the single most important reason.

## What the facts say
4-6 bullets interpreting the key facts: what explains the differences between pipelines and readers, what the
failure patterns (most-missed fields, runaway pages, degraded pages) suggest, where self-hosted models help
or don't.

## Recommendation
A table: goal -> choice -> why. Goals: highest accuracy, lowest latency, lowest cost, checking/second opinion,
and when (if ever) to self-host a model. "Lowest cost" and "lowest latency" must name the cheapest and
fastest pipeline from the key facts.

## Caveats
3-4 bullets on how far this generalises.

Rules:
- Do not compute, estimate or derive numbers. You may quote a number only if it appears verbatim in the key facts
  or setup below; prefer naming the pipeline and saying "most accurate", "cheapest", etc.
- A pipeline without a price is not "cheap": its cost is unknown. Say so instead of ranking it.
- Use the exact pipeline and backend names. Under 400 words."""


def build_prompt(meta: dict, scores: dict) -> tuple[str, str]:
    from .reporting import focused_meta, visible

    scores = visible(meta, scores)
    meta = focused_meta(meta)
    facts_md = to_md(compute(meta, scores))
    agent = meta["extraction"]["agent"]
    lines = []
    for t in meta.get("transcribers") or []:
        b = view.spec(meta, t)
        if b.get("kind") == "selfhosted":
            lines.append(
                f"- `{t}`: self-hosted {view.label(meta, t)} on {b.get('instance_type')} "
                f"({b.get('gpu')}, {b.get('host')}) (transcriber)"
            )
        else:
            lines.append(f"- `{t}`: {view.label(meta, t)}, temperature {b.get('temperature', 0.0)} (transcriber)")
    others = sorted({view.extractor(meta, p) for p in meta["extraction"]["pipelines"]} - {agent})
    a = view.spec(meta, agent)
    lines.append(
        "- "
        + explain.agent_note(
            a.get("model"),
            a.get("temperature", 0.0),
            others,
            label=view.label(meta, agent),
            max_tokens=int(prompts.of(meta, "extract")["max_tokens"]),
        )
    )
    lines.append(
        "- Extractor per pipeline (the model after the arrow): "
        + "; ".join(f"`{p}` -> {view.label(meta, view.extractor(meta, p))}" for p in meta["extraction"]["pipelines"])
    )
    d = meta["documents"]
    lines.append(
        f"- documents: {d['count']} synthetic Norwegian documents, {d['page_images']} page images, "
        f"types: {', '.join(d['types'])}; each page clean and degraded (simulated phone photo)"
    )
    pipes = "\n".join(f"- `{p}`: {view.pipeline_text(meta, p)}" for p in meta["extraction"]["pipelines"])
    setup = "\n".join(lines)
    prompt = f"{INSTRUCTIONS}\n\n# Setup\n{setup}\n\n# Pipelines\n{explain.ARROW}\n{pipes}\n\n# Key facts\n{facts_md}"
    return prompt, facts_md + "\n" + setup


# A number: optional thousands groups (space, no-break/thin space or comma + exactly 3 digits), optional
# decimals with "." or "," (Norwegian decimal comma). Separators never span other whitespace or newlines.
_NUM = re.compile(
    r"(?<![\w.,])\d{1,3}(?:[ \u00a0\u202f,]\d{3})+(?:[.,]\d+)?(?![\d.,]\d)(?!\d)"
    r"|(?<![\w.,])\d+(?:[.,]\d+)?(?![.,]\d)(?!\d)"
)
_FACTOR = re.compile(r"\s*(?:x|×|times|%|-fold)\b|\s*×", re.I)


def _value(tok: str) -> tuple[float, int]:
    """(value, decimals) of a number token."""
    m = re.fullmatch(r"(\d{1,3}(?:[ \u00a0\u202f,]\d{3})+)([.,]\d+)?", tok)
    if m:
        intpart, dec = re.sub(r"\D", "", m.group(1)), (m.group(2) or "")[1:]
    else:
        intpart, _, dec = tok.replace(",", ".").partition(".")
    return float(f"{intpart}.{dec}" if dec else intpart), len(dec)


def unverified_numbers(text: str, source: str) -> list[str]:
    """Numbers in `text` that are not in `source`, nor a rounding of a number in `source`.

    Single-digit numbers are ignored (counts, list items) unless they read as a factor or percentage
    ("3x cheaper", "5%"), which the model would have had to compute.
    """
    known = [_value(m.group()) for m in _NUM.finditer(source)]
    out = []
    for m in _NUM.finditer(text):
        tok = m.group()
        v, d = _value(tok)
        if v < 10 and d == 0 and not _FACTOR.match(text, m.end()):
            continue
        if any(v == k or (d < kd and round(k, d) == v) for k, kd in known):
            continue
        out.append(tok)
    return sorted(set(out))


def run(cfg: Config, run_dir: Path, meta: dict, scores: dict) -> str:
    name = (cfg["bench"].get("summary") or {}).get("agent") or cfg["bench"]["extraction"]["agent"]
    be = get_backend(cfg, name)
    prompt, source = build_prompt(meta, scores)
    pr = prompts.of(meta, "summarize")

    async def go():
        await be.warmup()
        req = Request(pr["system"], prompt, max_tokens=int(pr["max_tokens"]), temperature=float(pr["temperature"]))
        resp, _, _ = await call_with_retries(
            lambda: be.complete(req),
            cfg["bench"].get("max_retries", 4),
        )
        return resp

    resp = asyncio.run(go())
    text = resp.text.strip()
    bad = unverified_numbers(text, source)
    (run_dir / "summary.md").write_text(text + "\n", encoding="utf-8")
    info = {
        "backend": name,
        "model": be.model,
        "input": "computed key facts + setup description",
        "prompt_tokens": resp.prompt_tokens,
        "completion_tokens": resp.completion_tokens,
        "unverified_numbers": bad,
    }
    (run_dir / "summary.json").write_text(json.dumps(info, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"recommendation written by {name}: {len(text)} chars; unverified numbers: {bad or 'none'}", flush=True)
    return text
