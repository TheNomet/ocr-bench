"""How a run describes its backends and pipelines, computed from run.json alone.

report.md, the site and the recommendation prompt all describe the setup through these helpers,
so they name every model, role and price the same way.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import explain


# --------------------------------------------------------------------------- one backend
def spec(meta: dict, name: str | None) -> dict:
    return (meta.get("backends") or {}).get(name or "") or {}


def is_selfhosted(meta: dict, name: str | None) -> bool:
    return spec(meta, name).get("kind") == "selfhosted"


def label(meta: dict, name: str | None) -> str:
    """Human name of the model behind a backend key: `label:` from the config if set, else derived
    from the model id ('Claude Sonnet 4.5 (2025-09-29)', 'baidu/Unlimited-OCR @ 07dea832')."""
    b = spec(meta, name)
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
    """Backends with temperature > 0: their results vary between runs."""
    return [k for k, b in (meta.get("backends") or {}).items() if (b.get("temperature") or 0) > 0]


# --------------------------------------------------------------------------- pipelines
def extractor(meta: dict, pipe: str) -> str:
    ex = meta.get("extraction") or {}
    return (ex.get("agents") or {}).get(pipe) or ex.get("agent") or ""


def same_model(meta: dict, pipe: str) -> bool:
    """True for `X->Y` where X's transcript is extracted by the same model X (it only measures itself)."""
    src = pipe.split("->")[0]
    if src == "image" or is_selfhosted(meta, src):
        return False
    return spec(meta, src).get("model") == spec(meta, extractor(meta, pipe)).get("model")


def pipeline_label(meta: dict, pipe: str) -> str:
    """Readable form: 'page images → Claude Haiku 5.5', 'Unlimited-OCR text → Claude Sonnet 4.5 (…)'."""
    src = pipe.split("->")[0]
    inp = "page images" if src == "image" else f"{short_label(meta, src)} text"
    return f"{inp} → {label(meta, extractor(meta, pipe))}"


def pipeline_text(meta: dict, pipe: str) -> str:
    src, ext = pipe.split("->")[0], extractor(meta, pipe)
    if src == "image":
        t = explain.PIPELINES["image"]
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


# --------------------------------------------------------------------------- the models table
@dataclass
class ModelRow:
    key: str
    roles: list[str] = field(default_factory=list)
    label: str = ""
    model_id: str = ""
    detail: str = ""  # second line on the site: model id, or the HF revision for self-hosted models
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
        roles.setdefault(agent, []).append(ROLE_AGENT)
    for k in dict.fromkeys(v for v in (ex.get("agents") or {}).values() if v != agent):
        roles.setdefault(k, []).append(ROLE_EXTRACTOR)
    writer = (meta.get("summary") or {}).get("agent") or agent
    if writer:
        roles.setdefault(writer, []).append(ROLE_WRITER)
    rows = []
    for k, rs in roles.items():
        b = spec(meta, k)
        sh = b.get("kind") == "selfhosted"
        rows.append(
            ModelRow(
                key=k,
                roles=rs,
                label=label(meta, k),
                model_id=b.get("model") or "?",
                detail=f"HF revision {b.get('hf_revision', '?')}" if sh else (b.get("model") or ""),
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
