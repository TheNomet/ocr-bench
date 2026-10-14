"""Stage 4: an LLM writes the recommendation from the computed key facts (summary.md).

The LLM gets the key-facts table (computed in code, see facts.py) and a short description of
the setup. It is asked to interpret, not to restate or recompute, and to write quantities in digits.
Afterwards the numbers in its text are checked against the facts (see unverified_numbers); any that
don't appear there are listed in the report as "unverified". The report re-checks against the
current facts, so a re-score flags numbers that went stale.
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
- Write every quantity in digits, never in words ("8 types", not "eight types"), and no ratios or multiples
  ("twice as fast", "3x cheaper") unless the key facts state them: numbers are checked against the facts.
- Only pipelines listed under Pipelines are in the key facts. Pipelines that were run but are not shown are named
  in the setup with the reason; don't describe them as not run.
- A pipeline without a price is not "cheap": its cost is unknown. Say so instead of ranking it.
- Use the exact pipeline and backend names. Under 400 words."""


def _hidden_note(meta: dict, focused: dict) -> str:
    """Setup line naming the pipelines that were run but are left out of the key facts, and why."""
    shown = set(focused["extraction"]["pipelines"])
    hidden_be = view.hidden_backends(meta)
    same, repeat = [], []
    for p in (meta.get("extraction") or {}).get("pipelines") or []:
        if p in shown:
            continue
        (same if view.same_model(meta, p) else repeat).append(f"`{p}`")
    parts = []
    if same:
        parts.append(
            f"{', '.join(same)}: run, but left out of the key facts because one model both transcribes and "
            "extracts, so it only measures that model against itself"
        )
    if repeat:
        parts.append(
            f"{', '.join(repeat)}: run, but left out of the key facts because they use "
            + ", ".join(f"`{b}`" for b in sorted(hidden_be))
            + ", re-run only as a reproducibility check"
        )
    return ("- Pipelines run but not shown: " + "; ".join(parts)) if parts else ""


def build_prompt(meta: dict, scores: dict) -> tuple[str, str]:
    full = meta
    meta, scores = view.focus(meta, scores)
    facts_md = to_md(compute(meta, scores), view.pipeline_labels(meta))
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
    lines.append("- " + view.agent_note(meta))
    lines.append(
        "- Extractor per pipeline (the model after the arrow): "
        + "; ".join(f"`{p}` -> {view.label(meta, view.extractor(meta, p))}" for p in meta["extraction"]["pipelines"])
    )
    hidden = _hidden_note(full, meta)
    if hidden:
        lines.append(hidden)
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
_FACTOR = re.compile(r"\s*(?:×|x(?![a-z])|times\b|-?fold\b)", re.I)
_PERCENT = re.compile(r"\s*%")
_UNIT_AFTER = re.compile(
    r"\s*(?:ms|s|secs?|seconds?|min|minutes?|h|hours?|pages?|docs?|documents?|USD|NOK|kr)\b"
    r"|\s*/\s*1k|\s*per\b",
    re.I,
)
_UNIT_BEFORE = re.compile(r"(?:\$|\b(?:USD|NOK|kr)\s?)$")
# A model name or version, not a claim: "Sonnet 4.5", "opus-5.5", "Qwen2.5" (the last never matches _NUM).
_MODEL_BEFORE = re.compile(
    r"(?:\b(?:claude|sonnet|opus|haiku|gpt|gemini|gemma|llama|qwen|mistral|pixtral|deepseek|phi)\s?|[A-Za-z]-)$",
    re.I,
)
_IDENT_CODE = re.compile(r"`[^`\n]*[A-Za-z][^`\n]*`")  # backend and pipeline keys: `sonnet-4.5->agent`
_DATE = re.compile(r"(?<![\w.-])\d{4}-\d{2}-\d{2}(?![\w-])")
_CONCURRENCY = re.compile(r"\bconcurrency\s?$", re.I)


def _value(tok: str) -> tuple[float, int]:
    """(value, decimals) of a number token."""
    m = re.fullmatch(r"(\d{1,3}(?:[ \u00a0\u202f,]\d{3})+)([.,]\d+)?", tok)
    if m:
        intpart, dec = re.sub(r"\D", "", m.group(1)), (m.group(2) or "")[1:]
    else:
        intpart, _, dec = tok.replace(",", ".").partition(".")
    return float(f"{intpart}.{dec}" if dec else intpart), len(dec)


def _numbers(s: str) -> tuple[list[tuple[str, str]], set[str]]:
    """([(token, kind)], dates) in `s`. kind: factor ("3x"), pct ("5%"), unit ("$6", "3 s", "5 pages"),
    concurrency ("concurrency 32") or plain. Dates, model versions and numbers inside code-span keys are left out."""
    s = _IDENT_CODE.sub(" ", s)
    dates = set(_DATE.findall(s))
    s = _DATE.sub(" ", s)
    out = []
    for m in _NUM.finditer(s):
        before = s[max(0, m.start() - 16) : m.start()]
        if _FACTOR.match(s, m.end()):
            kind = "factor"
        elif _PERCENT.match(s, m.end()):
            kind = "pct"
        elif _UNIT_AFTER.match(s, m.end()) or _UNIT_BEFORE.search(before):
            kind = "unit"
        elif _MODEL_BEFORE.search(before):
            continue
        elif _CONCURRENCY.search(before):
            kind = "concurrency"
        else:
            kind = "plain"
        out.append((m.group(), kind))
    return out, dates


def unverified_numbers(text: str, source: str) -> list[str]:
    """Numbers in `text` that are not in `source`, nor a rounding of a number in `source`.

    Plain single-digit integers are ignored (counts, list items, "step 1"), but not when they carry a unit,
    currency, percent or factor ("$6", "3 s", "5%", "3x"). Factors and percentages only match factors and
    percentages in `source`, so "3x" isn't verified by a count of 3. Dates, concurrency levels, model versions
    ("Sonnet 4.5") and backend keys in code spans never verify a claim; dates in `text` must appear in `source`.
    """
    src, src_dates = _numbers(source)
    pools: dict[str, list[tuple[float, int]]] = {"factor": [], "pct": [], "concurrency": [], "other": []}
    for tok, kind in src:
        pools[kind if kind in pools else "other"].append(_value(tok))
    found, dates = _numbers(text)
    out = sorted(dates - src_dates)
    for tok, kind in found:
        v, d = _value(tok)
        if kind in ("plain", "concurrency") and v < 10 and d == 0:
            continue
        if kind in ("factor", "pct"):
            known = pools[kind]
        else:  # the source's concurrency levels verify only a concurrency level
            known = pools["other"] + (pools["concurrency"] if kind == "concurrency" else [])
        if any(v == k or (d < kd and round(k, d) == v) for k, kd in known):
            continue
        out.append(tok)
    return sorted(set(out))


def run(cfg: Config, run_dir: Path, meta: dict, scores: dict) -> str:
    name = cfg.summary_agent
    be = get_backend(cfg, name)
    prompt, source = build_prompt(meta, scores)
    pr = prompts.of(meta, "summarize")

    async def go():
        await be.warmup()
        req = Request(pr["system"], prompt, max_tokens=int(pr["max_tokens"]), temperature=float(pr["temperature"]))
        resp, _, _ = await call_with_retries(lambda: be.complete(req), int(cfg["bench"]["max_retries"]))
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
