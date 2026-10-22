"""A run = one self-contained folder: runs/<id>/.

    runs/<id>/
      run.json            setup: models, GPU + price, documents, plan, stage timings, git commit
      config.yaml         snapshot of the config used (local only, never uploaded publicly)
      docs/               copy of the document set the run used
      runs.jsonl transcripts.jsonl extractions.jsonl outputs/     stage 1 + 2 records
      scores.json         scored results (stage 3)
      summary.md          LLM-written recommendation (stage 4); summary.json: who wrote it, unverified numbers
      report.md           everything above as one readable document

The same folder is mirrored to s3://<bucket>/runs/<id>/ when the runner is Fargate.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import prompts, view
from .config import ROOT, Config

RUNS = ROOT / "runs"
STAGES = ["transcribe", "extract", "score", "summarize"]

SCHEMA_VERSION = 3  # run.json layout; older runs are upgraded on load (see upgrade())

# GPU as marketed (EC2 reports slightly less usable memory); host vCPU/RAM come from the EC2 API,
# with the per-family ratio below as a fallback.
GPU_INFO = {
    "ml.g4dn": "NVIDIA T4, 16 GB",
    "ml.g5": "NVIDIA A10G, 24 GB",
    "ml.g6": "NVIDIA L4, 24 GB",
    "ml.g6e": "NVIDIA L40S, 48 GB",
    "ml.p4d": "8x NVIDIA A100, 40 GB",
    "ml.p5": "8x NVIDIA H100, 80 GB",
}
_VCPU = {"xlarge": 4, "2xlarge": 8, "4xlarge": 16, "8xlarge": 32, "12xlarge": 48, "16xlarge": 64}
_GB_PER_VCPU = {"ml.g4dn": 4, "ml.g5": 4, "ml.g6": 4, "ml.g6e": 8}  # host RAM per vCPU, single-GPU sizes


def describe_instance(itype: str | None, region: str | None = None) -> dict:
    """GPU and host of a SageMaker instance type: from the EC2 API if reachable, else the table above."""
    if not itype:
        return {}
    family, _, size = itype.rpartition(".")
    vcpu, per = _VCPU.get(size), _GB_PER_VCPU.get(family)
    out = {"gpu": GPU_INFO.get(family, "?"), "host": f"{vcpu} vCPU, {vcpu * per} GB RAM" if vcpu and per else "?"}
    if region:
        from . import aws

        live = aws.instance_specs(region, itype)
        # EC2 reports usable GPU memory (22 GB for a 24 GB A10G): keep the marketed figure when known
        out = {
            "gpu": out["gpu"] if out["gpu"] != "?" else live.get("gpu", "?"),
            "host": live.get("host") or out["host"],
        }
    return out


def new_id(plan: str) -> str:
    base = f"{datetime.now():%Y-%m-%d}-{plan}"
    rid, n = base, 2
    while (RUNS / rid).exists():
        rid, n = f"{base}-{n}", n + 1
    return rid


def run_dir(rid: str) -> Path:
    return RUNS / rid


def _v1_to_v2(m: dict) -> dict:
    """`ocr` + `llm_backends` blocks -> one `backends` map with a kind per backend, plus the prompts."""
    backends: dict[str, dict] = dict(m.get("backends") or {})
    o = m.pop("ocr", None)
    if o is not None:
        o.pop("name", None)
        strip = o.pop("strip_grounding_tokens", True)
        req = {"prompt": o.pop("prompt", None), "temperature": o.pop("request_temperature", 0.0)}
        backends["ocr"] = {
            "kind": "selfhosted",
            "model": o.get("served_model_name"),
            "via": f"vLLM on {o.get('serving') or 'SageMaker real-time endpoint'}",
            **o,
            "request": req,
            "temperature": req["temperature"],
            "postprocess": ["strip_grounding"] if strip else [],
            "price_per_mtok": None,
        }
    for k, v in (m.pop("llm_backends", None) or {}).items():
        v = dict(v)
        backends[k] = {"kind": v.pop("provider", None), **v, "postprocess": []}
    m["backends"] = backends
    m.setdefault("prompts", copy.deepcopy(prompts.DEFAULTS))
    return m


def upgrade(meta: dict) -> dict:
    """run.json of an older schema -> the current one, one step per version, then stamped. Returns `meta`
    itself if it is current; never modifies it. Runs before schema 2 recorded no prompts: the defaults are
    assumed (they were not always the current ones: the summarize prompt once had max_tokens 3000). Runs
    before schema 3 extracted every document once (`extraction.repeat` 1)."""
    v = meta.get("schema_version", 1)
    if v >= SCHEMA_VERSION:
        return meta
    m = copy.deepcopy(meta)
    if v < 2:
        m = _v1_to_v2(m)
    if v < 3 and isinstance(m.get("extraction"), dict):
        m["extraction"].setdefault("repeat", 1)
    m["schema_version"] = SCHEMA_VERSION
    return m


def load(rid_or_dir: str | Path) -> dict:
    d = Path(rid_or_dir) if Path(rid_or_dir).is_dir() else run_dir(str(rid_or_dir))
    return upgrade(json.loads((d / "run.json").read_text(encoding="utf-8")))


def save(d: Path, meta: dict) -> None:
    (d / "run.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def _git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
        dirty = subprocess.run(
            ["git", "-C", str(ROOT), "status", "--porcelain", "--", "src"], capture_output=True, text=True
        )
        return out.stdout.strip() + ("+dirty" if dirty.stdout.strip() else "") if out.returncode == 0 else None
    except OSError:
        return None


def documents_summary(docs: Path) -> dict:
    manifest = json.loads((docs / "manifest.json").read_text(encoding="utf-8"))
    schemas = json.loads((docs / "schemas.json").read_text(encoding="utf-8"))
    types: dict[str, dict] = {}
    for d in manifest:
        t = types.setdefault(
            d["type"],
            {
                "docs": 0,
                "pages": 0,
                "description": schemas.get(d["type"], {}).get("description", ""),
                "fields": list(schemas.get(d["type"], {}).get("fields", {})),
            },
        )
        t["docs"] += 1
        t["pages"] += len(d["pages"])
    pages = sum(len(d["pages"]) for d in manifest)
    return {"count": len(manifest), "pages": pages, "page_images": pages * 2, "types": types}


_PUBLIC_REGISTRIES = {
    "docker.io",
    "registry-1.docker.io",
    "index.docker.io",
    "ghcr.io",
    "public.ecr.aws",
    "nvcr.io",
    "quay.io",
}
_PUBLIC_ARN_TYPES = {"foundation-model", "inference-profile"}  # their ids are public model ids


def public_model_id(model: str | None) -> str | None:
    """A model id safe to publish. An ARN carries the region and account id: foundation models and
    system inference profiles keep their public id (`eu.anthropic.claude-...`); account resources
    (application inference profiles, provisioned or custom models) become `<type>/<short hash>`, still
    distinct per resource so runs can be compared."""
    if not model or not model.startswith("arn:"):
        return model
    resource = model.split(":", 5)[-1]
    rtype, _, rid = resource.partition("/")
    if rtype in _PUBLIC_ARN_TYPES and rid:
        return rid
    return f"{rtype or 'arn'}/{hashlib.sha256(model.encode()).hexdigest()[:8]}"


def public_image(image: str | None) -> str | None:
    """A container image reference safe to publish: images on a public registry as they are; on a private
    registry (an ECR URI carries the account id, its path may name an organisation) only
    `<private-registry>/<last path segment>`, which keeps the image name and its tag or digest."""
    if not image:
        return image
    first, sep, rest = image.partition("/")
    if not sep or not ("." in first or ":" in first or first == "localhost"):
        return image  # Docker Hub short form
    return image if first.lower() in _PUBLIC_REGISTRIES else f"<private-registry>/{rest.rsplit('/', 1)[-1]}"


def backend_summary(spec: dict) -> dict:
    """Model identity, settings and price of one backend: no hosts, URLs, credentials, auth details or
    account ids."""
    common = {"kind": spec["kind"], "label": spec.get("label"), "postprocess": list(spec.get("postprocess") or [])}
    if spec["kind"] != "selfhosted":
        return {
            **common,
            "model": public_model_id(spec.get("model") or spec.get("model_id")),
            "price_per_mtok": spec.get("price_per_mtok"),
            "temperature": spec.get("temperature", 0.0),
            "reasoning_effort": spec.get("reasoning_effort"),
            "via": "AWS Bedrock" if spec["kind"] == "bedrock" else "OpenAI-compatible gateway",
        }
    sm, req = spec.get("sagemaker") or {}, spec.get("request") or {}
    serving = "direct vLLM URL" if spec.get("direct_url") else "SageMaker real-time endpoint"
    if sm.get("response_stream") and not spec.get("direct_url"):
        serving += " (streamed)"
    return {
        **common,
        "model": spec["served_model_name"],
        "hf_repo": spec.get("hf_repo"),
        "hf_revision": spec.get("hf_revision"),
        "source_image": public_image(spec.get("source_image")),
        "serve_args": spec.get("serve_args"),
        "serving": serving,
        "via": f"vLLM on {serving}",
        "inference_ami_version": sm.get("inference_ami_version"),
        "instance_pools": sm.get("instance_pools"),
        "response_stream": bool(sm.get("response_stream")),
        "request": {k: req.get(k) for k in ("prompt", "max_tokens", "temperature", "extra_body", "image_first")},
        "temperature": req.get("temperature", 0.0),
        "price_per_mtok": None,
        "instance_type": None,
        "price_per_hour_usd": None,
        "image_digest": None,
    }


def backends_summary(cfg: Config, names: list[str] | None = None) -> dict:
    return {k: backend_summary(cfg.backend(k)) for k in (names if names is not None else cfg.backends)}


def summary_temperature(spec: dict, summarize_prompt: dict) -> float | None:
    """The temperature the recommendation request is sent with (summarize.run): the backend's `temperature`
    override when it has one (null: not sent), else the summarize prompt's. backend_summary records the
    stage 1-2 value instead (0 without an override), which is not what the writer used."""
    if spec["kind"] == "selfhosted":
        return (spec.get("request") or {}).get("temperature", 0.0)  # vLLM sends its own request settings
    return spec["temperature"] if "temperature" in spec else float(summarize_prompt["temperature"])


def used_backends(items: list[dict], agent: str, agents: dict[str, str], summary_agent: str | None) -> list[str]:
    """Backends a run uses, in stage order: the plan's transcribers, the extractors, the summary writer."""
    keys = [it["backend"] for it in items] + [agent, *agents.values()]
    return list(dict.fromkeys(keys + ([summary_agent] if summary_agent else [])))


def stage_backends(meta: dict) -> set[str]:
    """Backends whose settings shaped stages 1-3 (transcribers and extractors), as recorded in run.json."""
    ex = meta.get("extraction") or {}
    out = {it["backend"] for it in meta.get("plan_items") or []} | set(meta.get("transcribers") or [])
    out |= {view.extractor(meta, p) for p in ex.get("pipelines") or []}
    return out | ({ex["agent"]} if ex.get("agent") else set())


def plan_pipelines(cfg: Config, plan: str) -> list[str]:
    """The plan's pipelines (bench.extraction.plan_pipelines.<plan>, else .pipelines), minus those reading a
    transcript the plan doesn't produce: they could only fail."""
    ex = cfg["bench"]["extraction"]
    pipes = (ex.get("plan_pipelines") or {}).get(plan, ex["pipelines"])
    made = {it["backend"] for it in cfg.plan(plan)}
    keep = [p for p in pipes if view.source(p) in made | {"image"}]
    dropped = [p for p in pipes if p not in keep]
    if dropped:
        print(f"note: plan {plan!r} doesn't transcribe with their source, so skipping {', '.join(dropped)}")
    return keep


def create(cfg: Config, plan: str, docs: Path, rid: str | None = None) -> Path:
    """New runs/<id>/ for `plan`. Raises KeyError (before creating anything) if the plan isn't defined."""
    items = cfg.plan(plan)
    rid = rid or new_id(plan)
    d = run_dir(rid)
    d.mkdir(parents=True)
    shutil.copytree(docs, d / "docs")
    shutil.copy2(cfg.path, d / "config.yaml")
    ex = dict(cfg["bench"]["extraction"])
    ex["pipelines"] = plan_pipelines(cfg, plan)
    agents = {p: view.extractor_key(ex["agent"], p) for p in ex["pipelines"]}
    used_prompts = prompts.resolve(cfg["bench"])
    meta = {
        "schema_version": SCHEMA_VERSION,
        "id": rid,
        "plan": plan,
        "plan_description": cfg.plan_description(plan),
        "plan_items": items,
        "report": {"hide_backends": cfg.plan_report(plan).get("hide_backends") or []},
        "created": datetime.now().isoformat(timespec="seconds"),
        "git_commit": _git_commit(),
        "region": cfg.region,
        "runner": {k: cfg["runner"].get(k) for k in ("mode", "cpu", "memory")},
        "documents": {
            **documents_summary(d / "docs"),
            **{k: cfg["bench"]["docs"][k] for k in ("seed", "per_type")},
        },
        "backends": backends_summary(cfg, used_backends(items, ex["agent"], agents, cfg.summary_agent)),
        "prompts": used_prompts,
        "transcribers": list(dict.fromkeys(it["backend"] for it in items)),
        "extraction": {
            "agent": ex["agent"],
            "pipelines": ex["pipelines"],
            "agents": agents,
            "repeat": cfg.extraction_repeat(plan),
        },
        "summary": {
            "agent": cfg.summary_agent,
            "temperature": summary_temperature(cfg.backend(cfg.summary_agent), used_prompts["summarize"]),
        },
        "stages": {},
    }
    save(d, meta)
    return d


def record_endpoint(cfg: Config, d: Path, name: str) -> dict:
    """Fill in the GPU host actually serving a self-hosted backend, its price and the deployed image digest."""
    from . import aws

    meta = load(d)
    spec, b = cfg.backend(name), meta["backends"][name]
    if spec.get("direct_url"):
        b["instance_type"] = "external (direct_url)"
    else:
        info = aws.endpoint_info(cfg, name)
        b.update(info)
        if info.get("instance_type"):
            override = (spec.get("sagemaker") or {}).get("price_per_hour_usd")
            b["price_per_hour_usd"] = override or aws.sagemaker_hourly_usd(cfg.region, info["instance_type"])
            b.update(describe_instance(info["instance_type"], cfg.region))
            if b["price_per_hour_usd"] is None:
                print(
                    f"warning: no hourly price found for {info['instance_type']} ({name}): its costs will be "
                    f"missing; set backends.{name}.sagemaker.price_per_hour_usd",
                    file=sys.stderr,
                )
        else:
            print(
                f"warning: could not read the instance type of endpoint {cfg.endpoint_name(name)} ({name}): "
                "GPU, host and price will be missing from run.json",
                file=sys.stderr,
            )
    save(d, meta)
    return b


def stage(d: Path, name: str, **info) -> None:
    meta = load(d)
    meta["stages"].setdefault(name, {}).update(info)
    save(d, meta)


def all_runs() -> list[dict]:
    """Every run, newest first: by `created` (an id sorts "-10" before "-9"), the id when it is missing."""
    if not RUNS.exists():
        return []
    out = [load(p) for p in RUNS.iterdir() if (p / "run.json").exists()]
    return sorted(out, key=lambda m: (m.get("created") or m.get("id") or "", m.get("id") or ""), reverse=True)


def refresh_summary_setup(cfg: Config, d: Path) -> None:
    """Re-summarising an old run uses the *current* config's recommendation model (and plan description).

    Nothing that affected stages 1-3 is changed. If the summary agent wasn't used in stages 1-3, its entry in
    run.json `backends` is (re)written. If it was, that entry keeps the settings stages 1-3 used, and the
    writer's current settings go to `summary.backend` when they differ.

    The run's config.yaml snapshot (gitignored, so absent on a fresh clone: then skipped) is patched so the
    Fargate task, which reads it, uses the same writer; a backend used in stages 1-3 is never redefined there:
    the writer is added under its own key (`summary.config_key`) instead.
    """
    meta = load(d)
    key = cfg.summary_agent
    spec = cfg.backend(key)
    entry = backend_summary(spec)
    used = stage_backends(meta)
    summarize_prompt = prompts.resolve(cfg["bench"])["summarize"]
    summary: dict = {"agent": key, "temperature": summary_temperature(spec, summarize_prompt)}
    meta["summary"] = summary
    if key not in used:
        meta["backends"][key] = entry
    elif meta["backends"].get(key) != entry:
        summary["backend"] = entry
    snap_key = _patch_snapshot(d, key, spec, key in used)
    if snap_key and snap_key != key:
        summary["config_key"] = snap_key
    meta.setdefault("prompts", {})["summarize"] = summarize_prompt
    desc = cfg.plan_description(meta["plan"])
    if desc:
        meta["plan_description"] = desc
    save(d, meta)


def fill_prices(cfg: Config, d: Path) -> dict[str, dict]:
    """Add list prices that were not known when the run was made: a backend the run recorded without
    `price_per_mtok` takes the current config's price if the config names the same model. A recorded price
    is never changed. Each filled entry is marked `price_added` (date). Returns {backend: price} filled."""
    from .explain import model_label

    meta = load(d)
    filled = {}
    for key, b in meta["backends"].items():
        spec = cfg["backends"].get(key)
        if b["kind"] == "selfhosted" or b.get("price_per_mtok") is not None or not spec:
            continue
        price = spec.get("price_per_mtok")
        if price is None or model_label(b.get("model")) != model_label(spec.get("model") or spec.get("model_id")):
            continue
        b["price_per_mtok"], b["price_added"] = price, datetime.now().strftime("%Y-%m-%d")
        filled[key] = price
    if filled:
        save(d, meta)
    return filled


def _patch_snapshot(d: Path, key: str, spec: dict, used_in_stages: bool) -> str | None:
    """Point the snapshot's bench.summary.agent at the writer; returns the snapshot key used (None: no snapshot)."""
    import yaml

    from .config import upgrade as upgrade_config

    p = d / "config.yaml"
    if not p.exists():
        return None
    snap = upgrade_config(yaml.safe_load(p.read_text(encoding="utf-8")) or {})
    backends = snap.setdefault("backends", {})
    target = key
    if used_in_stages and backends.get(key) not in (None, spec):
        target, n = f"{key}-summary", 2
        while target in backends and backends[target] != spec:
            target, n = f"{key}-summary-{n}", n + 1
    backends[target] = spec
    snap.setdefault("bench", {})["summary"] = {"agent": target}
    p.write_text(yaml.safe_dump(snap, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return target
