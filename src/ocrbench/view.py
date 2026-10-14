"""How a run describes its backends and pipelines, computed from run.json alone.

report.md, the site and the recommendation prompt all describe the setup through these helpers,
so they name every model, role and price the same way.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import explain, prompts


# --------------------------------------------------------------------------- one backend
def spec(meta: dict, name: str | None) -> dict:
    return (meta.get("backends") or {}).get(name or "") or {}


def is_selfhosted(meta: dict, name: str | None) -> bool:
    return spec(meta, name).get("kind") == "selfhosted"


def label(meta: dict, name: str | None) -> str:
    """Human name of the model behind a backend key: `label:` from the config if set, else derived
    from the model id ('Claude Sonnet 4.5 (2025-09-29)', 'baidu/Unlimited-OCR @ 07dea832')."""
    return _label(spec(meta, name), name)


def _label(b: dict, name: str | None) -> str:
    if b.get("label"):
        return b["label"]
    if b.get("kind") == "selfhosted":
        rev = (b.get("hf_revision") or "")[:8]
        return f"{b.get('hf_repo') or b.get('model') or name}" + (f" @ {rev}" if rev else "")
    return explain.model_label(b.get("model")) if b.get("model") else str(name)


def short_label(meta: dict, name: str | None) -> str:
    """Compact form for pipeline labels: the repo name without owner or revision for self-hosted models."""
    b = spec(meta, name)
    if b.get("kind") == "selfhosted" and not b.get("label"):
        return (b.get("hf_repo") or b.get("model") or str(name)).split("/")[-1]
    return label(meta, name)


def price_text(b: dict) -> str:
    if b.get("kind") == "selfhosted":
        p = b.get("price_per_hour_usd")
        return f"${p:.2f}/hour (on-demand)" if p else "–"
    p = b.get("price_per_mtok") or {}
    return f"${p.get('input')} in / ${p.get('output')} out per 1M tokens" if p else "–"


def selfhosted_in_run(meta: dict) -> list[str]:
    return [t for t in meta.get("transcribers") or [] if is_selfhosted(meta, t)]


def sampling(meta: dict) -> list[str]:
    """Backends with temperature > 0 among those whose results a run page shows (transcribers and the
    extractors of shown pipelines): their results vary between runs. The recommendation writer and
    hidden or unused backends don't count."""
    hidden = hidden_backends(meta)
    pipes = (meta.get("extraction") or {}).get("pipelines") or []
    shown = [t for t in meta.get("transcribers") or [] if t not in hidden]
    shown += [extractor(meta, p) for p in pipes if not pipe_hidden(meta, p, hidden)]
    return [k for k in dict.fromkeys(shown) if (spec(meta, k).get("temperature") or 0) > 0]


def summary_spec(meta: dict) -> dict:
    """The recommendation writer's backend: run.json `summary.backend` when recorded there (the writer ran
    with other settings than the same key in stages 1-3), else `backends.<summary agent>`."""
    sm = meta.get("summary") or {}
    if sm.get("backend"):
        return sm["backend"]
    return spec(meta, sm.get("agent") or (meta.get("extraction") or {}).get("agent"))


# --------------------------------------------------------------------------- pipelines
def source(pipe: str) -> str:
    """What the extractor reads: `image` or a transcriber backend (the left side of `<input>-><extractor>`)."""
    return pipe.split("->", 1)[0]


def extractor_key(agent: str, pipe: str) -> str:
    """`<input>->agent` is extracted by `agent` (the default extraction agent); `<input>-><backend>` by that backend."""
    key = pipe.split("->", 1)[1]
    return agent if key == "agent" else key


def extractor(meta: dict, pipe: str) -> str:
    """The backend that extracted `pipe` in this run, as recorded in run.json."""
    ex = meta.get("extraction") or {}
    return (ex.get("agents") or {}).get(pipe) or extractor_key(ex.get("agent") or "", pipe)


def same_model(meta: dict, pipe: str) -> bool:
    """True for `X->Y` where X's transcript is extracted by the same model X (it only measures itself)."""
    src = source(pipe)
    if src == "image" or is_selfhosted(meta, src):
        return False
    a, b = spec(meta, src).get("model"), spec(meta, extractor(meta, pipe)).get("model")
    return a is not None and a == b  # an unknown model is not the same model


def pipeline_label(meta: dict, pipe: str) -> str:
    """Readable form: 'page images → Claude Haiku 5.5', 'Unlimited-OCR text → Claude Sonnet 4.5 (…)'."""
    src = source(pipe)
    inp = "page images" if src == "image" else f"{short_label(meta, src)} text"
    return f"{inp} → {label(meta, extractor(meta, pipe))}"


def pipeline_labels(meta: dict) -> dict[str, str]:
    """pipeline_label for every pipeline of the run, in run order."""
    return {p: pipeline_label(meta, p) for p in (meta.get("extraction") or {}).get("pipelines") or []}


def pipeline_text(meta: dict, pipe: str) -> str:
    src, ext = source(pipe), extractor(meta, pipe)
    if src == "image":
        t = explain.PIPELINES["image"]
        if pipe.split("->", 1)[1] != "agent":  # a named extractor, not the run's default agent
            t = t.replace("the extraction agent reads", "the extractor reads")
    elif is_selfhosted(meta, src):
        t = (
            f"Two steps: the self-hosted model `{src}` ({label(meta, src)}) transcribes every page to text, then "
            "the extractor reads that text (not the images) and returns the fields."
        )
    else:
        t = (
            f"Two steps: `{src}` ({label(meta, src)}) transcribes every page to text, then the extractor reads "
            "that text and returns the fields."
        )
    return f"{t} Extractor: `{ext}` ({label(meta, ext)})."


def default_agent_shown(meta: dict) -> bool:
    """False when focused_meta replaced a hidden default agent: no shown pipeline uses the run's agent."""
    return not (meta.get("extraction") or {}).get("agent_hidden")


def agent_note(meta: dict) -> str:
    """One paragraph on the default extraction agent: model, settings, and the other extractors used.
    When the default agent is hidden (report.hide_backends), lists the extractors the shown pipelines use."""
    agent = meta["extraction"]["agent"]
    max_tokens = int(prompts.of(meta, "extract")["max_tokens"])
    if not default_agent_shown(meta):
        keys = dict.fromkeys(extractor(meta, p) for p in meta["extraction"]["pipelines"])
        return explain.extractors_note(
            [(label(meta, k), k, spec(meta, k).get("model"), spec(meta, k).get("temperature", 0.0)) for k in keys],
            max_tokens=max_tokens,
        )
    a = spec(meta, agent)
    others = [
        f"`{k}`" for k in dict.fromkeys(extractor(meta, p) for p in meta["extraction"]["pipelines"]) if k != agent
    ]
    return explain.agent_note(
        a.get("model"),
        a.get("temperature", 0.0),
        others,
        label=label(meta, agent),
        max_tokens=max_tokens,
    )


# --------------------------------------------------------------------------- what a run page shows
def hidden_backends(meta: dict) -> set[str]:
    """Backends a run reports on but doesn't focus on (run.json `report.hide_backends`): e.g. models
    re-run only as a reproducibility check. Their data stays on disk and in the cross-run comparison."""
    return set((meta.get("report") or {}).get("hide_backends") or [])


def pipe_hidden(meta: dict, pipe: str, hidden: set[str]) -> bool:
    """Not shown on a run page: one model reading and extracting (`X->X`), or a hidden backend on either side."""
    return same_model(meta, pipe) or source(pipe) in hidden or extractor(meta, pipe) in hidden


def focused_meta(meta: dict) -> dict:
    """run.json as a run page should describe it: hidden backends removed from transcribers and pipelines,
    and the default agent replaced by a shown extractor if it was hidden."""
    hidden = hidden_backends(meta)
    ex = dict(meta.get("extraction") or {})
    keep = [p for p in ex.get("pipelines", []) if not pipe_hidden(meta, p, hidden)]
    ex["pipelines"] = keep
    ex["agents"] = {p: extractor(meta, p) for p in keep}  # resolved before the default agent is replaced
    if ex.get("agent") in hidden:
        shown = [ex["agents"][p] for p in keep if ex["agents"][p] not in hidden]
        ex["agent"] = shown[0] if shown else ex.get("agent")
        ex["agent_hidden"] = True  # ex["agent"] is a stand-in: don't call it the default agent
    return {
        **meta,
        "extraction": ex,
        "transcribers": [t for t in meta.get("transcribers") or [] if t not in hidden],
        "plan_items": [i for i in meta.get("plan_items") or [] if i.get("backend") not in hidden],
    }


def visible(meta: dict, scores: dict, focus: bool = True) -> dict:
    """Scores as shown on a run page.

    Always hides `X->X` pipelines (same model reading and extracting). With focus=True, also hides
    everything involving a backend in `report.hide_backends` (as transcriber or extractor).
    """
    hidden = hidden_backends(meta) if focus else set()
    ex = scores.get("extraction") or {}
    tr = scores.get("transcription") or {}
    return {
        **scores,
        "speed": [r for r in scores.get("speed") or [] if r["backend"] not in hidden],
        "transcription": {
            "aggregate": [a for a in tr.get("aggregate") or [] if a["backend"] not in hidden],
            "pages": [p for p in tr.get("pages") or [] if p["backend"] not in hidden],
        },
        "extraction": {
            "aggregate": [a for a in ex.get("aggregate") or [] if not pipe_hidden(meta, a["pipeline"], hidden)],
            "docs": [d for d in ex.get("docs") or [] if not pipe_hidden(meta, d["pipeline"], hidden)],
        },
    }


def focus(meta: dict, scores: dict) -> tuple[dict, dict]:
    """(run.json, scores) as a run page shows them: see focused_meta and visible."""
    return focused_meta(meta), visible(meta, scores)


# --------------------------------------------------------------------------- the models table
@dataclass
class ModelRow:
    key: str
    roles: list[str] = field(default_factory=list)
    label: str = ""
    model_id: str = ""
    detail: str = ""  # second line on the site: model id, or the short HF revision for self-hosted models
    detail_title: str = ""  # tooltip for `detail`: the full HF revision
    via: str = ""
    temperature: float | None = 0.0
    price: str = "–"
    reasoning_effort: str | None = None


ROLE_SELFHOSTED, ROLE_TRANSCRIBER = "transcriber (self-hosted)", "transcriber"
ROLE_AGENT, ROLE_EXTRACTOR, ROLE_WRITER = "extraction agent (default)", "extractor", "recommendation writer"


def model_rows(meta: dict) -> list[ModelRow]:
    """One row per backend the run used, with every role it played, in the order the stages use them."""
    ex = meta.get("extraction") or {}
    agent = ex.get("agent")
    roles: dict[str, list[str]] = {}
    for t in meta.get("transcribers") or []:
        roles.setdefault(t, []).append(ROLE_SELFHOSTED if is_selfhosted(meta, t) else ROLE_TRANSCRIBER)
    if agent:
        roles.setdefault(agent, []).append(ROLE_AGENT if default_agent_shown(meta) else ROLE_EXTRACTOR)
    for k in dict.fromkeys(v for v in (ex.get("agents") or {}).values() if v != agent):
        roles.setdefault(k, []).append(ROLE_EXTRACTOR)
    writer: str = (meta.get("summary") or {}).get("agent") or agent or ""
    wspec = summary_spec(meta)
    own_row = writer in roles and wspec != spec(meta, writer)  # the writer ran with other settings than stages 1-3
    if writer and not own_row:
        roles.setdefault(writer, []).append(ROLE_WRITER)
    rows_of = [(k, rs, wspec if rs == [ROLE_WRITER] else spec(meta, k)) for k, rs in roles.items()]
    if own_row:
        rows_of.append((writer, [ROLE_WRITER], wspec))
    rows = []
    for k, rs, b in rows_of:
        sh = b.get("kind") == "selfhosted"
        rev = str(b.get("hf_revision") or "?")
        rows.append(
            ModelRow(
                key=k,
                roles=rs,
                label=_label(b, k),
                model_id=b.get("model") or "?",
                detail=f"HF revision {rev[:8]}" if sh else (b.get("model") or ""),
                detail_title=f"HF revision {rev}" if sh else "",
                via=b.get("via") or "?",
                temperature=b.get("temperature", 0.0),
                price=price_text(b),
                reasoning_effort=b.get("reasoning_effort"),
            )
        )
    return rows


def gpu_rows(meta: dict, name: str) -> list[tuple[str, str]]:
    """(label, Markdown value) about the host serving a self-hosted backend."""
    b = spec(meta, name)
    pools = ", ".join(b.get("instance_pools") or []) or "–"
    return [
        ("instance", f"`{b.get('instance_type') or 'unknown'}` (picked from pools: {pools})"),
        ("GPU", b.get("gpu") or "?"),
        ("host", b.get("host") or "?"),
        ("price", f"{price_text(b)}, billed while the endpoint exists, busy or idle"),
        ("region", str(meta.get("region"))),
        ("host image", f"`{b.get('inference_ami_version') or '–'}`"),
        ("container", f"`{b.get('source_image')}` (digest `{(b.get('image_digest') or '–')[:19]}`)"),
        ("serving flags", f"`{' '.join(b.get('serve_args') or [])}`"),
        ("post-processing", ", ".join(f"`{s}`" for s in b.get("postprocess") or []) or "none"),
    ]
