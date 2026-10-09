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

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from .config import ROOT, Config

RUNS = ROOT / "runs"
STAGES = ["transcribe", "extract", "score", "summarize"]

# What the reader needs to know about each GPU host type (on-demand price comes from the Pricing API).
GPU_INFO = {
    "ml.g4dn": "NVIDIA T4, 16 GB",
    "ml.g5": "NVIDIA A10G, 24 GB",
    "ml.g6": "NVIDIA L4, 24 GB",
    "ml.g6e": "NVIDIA L40S, 48 GB",
    "ml.p4d": "8x NVIDIA A100, 40 GB",
    "ml.p5": "8x NVIDIA H100, 80 GB",
}
HOST_INFO = {  # vCPU / RAM of the host: matters for image preprocessing
    "xlarge": "4 vCPU, 16 GB RAM",
    "2xlarge": "8 vCPU, 32 GB RAM",
    "4xlarge": "16 vCPU, 64 GB RAM",
    "8xlarge": "32 vCPU, 128 GB RAM",
    "12xlarge": "48 vCPU, 192 GB RAM",
}


def describe_instance(itype: str | None) -> dict:
    if not itype:
        return {}
    family, _, size = itype.rpartition(".")
    return {"gpu": GPU_INFO.get(family, "?"), "host": HOST_INFO.get(size, "?")}


def new_id(plan: str) -> str:
    base = f"{datetime.now():%Y-%m-%d}-{plan}"
    rid, n = base, 2
    while (RUNS / rid).exists():
        rid, n = f"{base}-{n}", n + 1
    return rid


def run_dir(rid: str) -> Path:
    return RUNS / rid


def load(rid_or_dir: str | Path) -> dict:
    d = Path(rid_or_dir) if Path(rid_or_dir).is_dir() else run_dir(str(rid_or_dir))
    return json.loads((d / "run.json").read_text(encoding="utf-8"))


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


def backends_summary(cfg: Config) -> dict:
    """Model identity and price per backend: no hosts, credentials or auth details."""
    out = {}
    for k, spec in cfg["llm_backends"].items():
        out[k] = {
            "provider": spec.get("provider"),
            "model": spec.get("model") or spec.get("model_id"),
            "price_per_mtok": spec.get("price_per_mtok"),
            "temperature": spec.get("temperature", 0.0),
            "via": "AWS Bedrock" if spec.get("provider") == "bedrock" else "OpenAI-compatible gateway",
        }
    return out


def create(cfg: Config, plan: str, docs: Path, rid: str | None = None) -> Path:
    rid = rid or new_id(plan)
    d = run_dir(rid)
    d.mkdir(parents=True)
    shutil.copytree(docs, d / "docs")
    shutil.copy2(cfg.path, d / "config.yaml")
    o, ex = cfg["ocr"], dict(cfg["bench"]["extraction"])
    ex["pipelines"] = (ex.get("plan_pipelines") or {}).get(plan, ex["pipelines"])
    summ = cfg["bench"].get("summary") or {}
    meta = {
        "id": rid,
        "plan": plan,
        "plan_items": cfg.plan(plan) if plan in cfg["bench"]["plans"] else [],
        "created": datetime.now().isoformat(timespec="seconds"),
        "git_commit": _git_commit(),
        "region": cfg.region,
        "runner": {k: cfg["runner"].get(k) for k in ("mode", "cpu", "memory")},
        "documents": {
            **documents_summary(d / "docs"),
            **{k: cfg["bench"]["docs"].get(k) for k in ("seed", "per_type")},
        },
        "ocr": {
            "name": o.get("name", "ocr"),
            "hf_repo": o["hf_repo"],
            "hf_revision": o["hf_revision"],
            "served_model_name": o["served_model_name"],
            "source_image": o["source_image"],
            "serving": "direct vLLM URL" if o.get("direct_url") else "SageMaker real-time endpoint",
            "inference_ami_version": o.get("sagemaker", {}).get("inference_ami_version"),
            "instance_pools": o.get("sagemaker", {}).get("instance_pools"),
            "serve_args": o.get("serve_args"),
            "prompt": o.get("request", {}).get("prompt"),
            "instance_type": None,
            "price_per_hour_usd": None,
            "image_digest": None,
        },
        "llm_backends": backends_summary(cfg),
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


def record_endpoint(cfg: Config, d: Path) -> None:
    """Fill in the GPU host actually serving the model, its price and the deployed image digest."""
    from . import aws

    meta = load(d)
    if cfg["ocr"].get("direct_url"):
        meta["ocr"]["instance_type"] = "external (ocr.direct_url)"
    else:
        info = aws.endpoint_info(cfg)
        meta["ocr"].update(info)
        if info.get("instance_type"):
            override = cfg["ocr"].get("sagemaker", {}).get("price_per_hour_usd")
            meta["ocr"]["price_per_hour_usd"] = override or aws.sagemaker_hourly_usd(cfg.region, info["instance_type"])
            meta["ocr"].update(describe_instance(info["instance_type"]))
    save(d, meta)


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
