"""A run = one self-contained folder: runs/<id>/.

    runs/<id>/
      run.json            setup: models, GPU + price, documents, plan, stage timings, git commit
      config.yaml         snapshot of the config used (local only, never uploaded publicly)
      docs/               copy of the document set the run used
      runs.jsonl transcripts.jsonl extractions.jsonl outputs/     stage 1 + 2 records
      scores.json         scored results (stage 3)
      summary.md          LLM-written findings (stage 4)
      report.md           everything above as one readable document

The same folder is mirrored to s3://<bucket>/runs/<id>/ when the runner is Fargate.
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from . import prompts
from .config import ROOT, Config

RUNS = ROOT / "runs"
STAGES = ["transcribe", "extract", "score", "summarize"]

SCHEMA_VERSION = 2  # run.json layout; older runs are upgraded on load (see upgrade())

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


def upgrade(meta: dict) -> dict:
    """run.json written before schema 2 (`ocr` + `llm_backends` blocks) -> one `backends` map with a kind
    per backend, plus the prompts. Earlier runs used the default prompts (unchanged since the first release)."""
    if meta.get("schema_version", 1) >= SCHEMA_VERSION:
        return meta
    m = copy.deepcopy(meta)
    backends: dict[str, dict] = {}
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
    m.setdefault("prompts", prompts.DEFAULTS)
    return {"schema_version": SCHEMA_VERSION, **m}


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


def backend_summary(spec: dict) -> dict:
    """Model identity, settings and price of one backend: no hosts, URLs, credentials or auth details."""
    common = {"kind": spec["kind"], "label": spec.get("label"), "postprocess": list(spec.get("postprocess") or [])}
    if spec["kind"] != "selfhosted":
        return {
            **common,
            "model": spec.get("model") or spec.get("model_id"),
            "price_per_mtok": spec.get("price_per_mtok"),
            "temperature": spec.get("temperature", 0.0),
            "reasoning_effort": spec.get("reasoning_effort"),
            "via": "AWS Bedrock" if spec["kind"] == "bedrock" else "OpenAI-compatible gateway",
        }
    sm, req = spec.get("sagemaker") or {}, spec.get("request") or {}
    serving = "direct vLLM URL" if spec.get("direct_url") else "SageMaker real-time endpoint"
    return {
        **common,
        "model": spec["served_model_name"],
        "hf_repo": spec.get("hf_repo"),
        "hf_revision": spec.get("hf_revision"),
        "source_image": spec.get("source_image"),
        "serve_args": spec.get("serve_args"),
        "serving": serving,
        "via": f"vLLM on {serving}",
        "inference_ami_version": sm.get("inference_ami_version"),
        "instance_pools": sm.get("instance_pools"),
        "request": {k: req.get(k) for k in ("prompt", "max_tokens", "temperature", "extra_body")},
        "temperature": req.get("temperature", 0.0),
        "price_per_mtok": None,
        "instance_type": None,
        "price_per_hour_usd": None,
        "image_digest": None,
    }


def backends_summary(cfg: Config, names: list[str] | None = None) -> dict:
    return {k: backend_summary(cfg.backend(k)) for k in (names if names is not None else cfg.backends)}


def create(cfg: Config, plan: str, docs: Path, rid: str | None = None) -> Path:
    rid = rid or new_id(plan)
    d = run_dir(rid)
    d.mkdir(parents=True)
    shutil.copytree(docs, d / "docs")
    shutil.copy2(cfg.path, d / "config.yaml")
    ex = dict(cfg["bench"]["extraction"])
    ex["pipelines"] = (ex.get("plan_pipelines") or {}).get(plan, ex["pipelines"])
    summ = cfg["bench"].get("summary") or {}
    meta = {
        "schema_version": SCHEMA_VERSION,
        "id": rid,
        "plan": plan,
        "plan_description": (cfg["bench"].get("plan_descriptions") or {}).get(plan, ""),
        "plan_items": cfg.plan(plan) if plan in cfg["bench"]["plans"] else [],
        "report": {"hide_backends": ((cfg["bench"].get("plan_report") or {}).get(plan) or {}).get("hide_backends", [])},
        "created": datetime.now().isoformat(timespec="seconds"),
        "git_commit": _git_commit(),
        "region": cfg.region,
        "runner": {k: cfg["runner"].get(k) for k in ("mode", "cpu", "memory")},
        "documents": {
            **documents_summary(d / "docs"),
            **{k: cfg["bench"]["docs"].get(k) for k in ("seed", "per_type")},
        },
        "backends": backends_summary(cfg),
        "prompts": prompts.resolve(cfg["bench"]),
        "transcribers": list(
            dict.fromkeys(it["backend"] for it in (cfg.plan(plan) if plan in cfg["bench"]["plans"] else []))
        ),
        "extraction": {
            "agent": ex["agent"],
            "pipelines": ex["pipelines"],
            "agents": {
                p: (ex["agent"] if p.split("->", 1)[1] == "agent" else p.split("->", 1)[1]) for p in ex["pipelines"]
            },
        },
        "summary": {"agent": summ.get("agent", ex["agent"])},
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
    save(d, meta)
    return b


def stage(d: Path, name: str, **info) -> None:
    meta = load(d)
    meta["stages"].setdefault(name, {}).update(info)
    save(d, meta)


def all_runs() -> list[dict]:
    if not RUNS.exists():
        return []
    out = []
    for p in sorted(RUNS.iterdir(), reverse=True):
        if (p / "run.json").exists():
            out.append(load(p))
    return out


def refresh_summary_setup(cfg: Config, d: Path) -> None:
    """Re-summarising an old run uses the *current* config's summary model (and plan description).

    The run's config.yaml snapshot is patched so the Fargate task, which reads it, sees the same thing.
    Nothing that affected stages 1-3 is changed.
    """
    import yaml

    from .config import upgrade as upgrade_config

    meta = load(d)
    key = (cfg["bench"].get("summary") or {}).get("agent") or cfg["bench"]["extraction"]["agent"]
    snap = upgrade_config(yaml.safe_load((d / "config.yaml").read_text(encoding="utf-8")))
    snap.setdefault("backends", {})[key] = cfg.backend(key)
    snap.setdefault("bench", {})["summary"] = {"agent": key}
    (d / "config.yaml").write_text(yaml.safe_dump(snap, sort_keys=False, allow_unicode=True), encoding="utf-8")
    meta["summary"] = {"agent": key}
    meta["backends"][key] = backend_summary(cfg.backend(key))
    meta.setdefault("prompts", {})["summarize"] = prompts.resolve(cfg["bench"])["summarize"]
    desc = (cfg["bench"].get("plan_descriptions") or {}).get(meta["plan"])
    if desc:
        meta["plan_description"] = desc
    save(d, meta)
