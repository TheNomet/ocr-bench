"""ocrbench command line. Every command reads the same config (--config or $OCRBENCH_CONFIG).

Local commands
  docs                     generate synthetic documents into docs-out/
  tf-vars                  render Terraform inputs from the config into .state/<prefix>/
  upload-weights [--from-dir DIR]   stream the pinned HF snapshot to S3 (SHA-256 verified)
  images ocr|bench|all     build + push images with crane (no Docker)
  endpoint up|down|status  SageMaker endpoint with GPU instance-pool fallback
  run --plan P --tag T     stage 1 (plan) + stage 2 (extraction); local or Fargate (runner.mode)
  fetch --tag T            download results/<T>/ from S3
  report --tag T           score + results/<T>/report.md + site/<T>/index.html

Stage commands (used by `run`, also usable directly)
  transcribe --backend B --concurrency N [--variant clean|degraded|both] [--limit N] [--repeat N]
  extract [--pipeline P ...]
  task-run --plan P --tag T   entrypoint inside the Fargate task
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from . import config as config_mod
from .config import ROOT, Config


def state_dir(cfg: Config) -> Path:
    d = ROOT / ".state" / cfg.prefix
    d.mkdir(parents=True, exist_ok=True)
    return d


def outputs(cfg: Config) -> dict:
    p = state_dir(cfg) / "outputs.json"
    if not p.exists():
        raise SystemExit(f"{p} not found — run `just deploy` (it writes Terraform outputs there)")
    return {k: v["value"] for k, v in json.loads(p.read_text()).items()}


# ------------------------------------------------------------------ tf-vars
def cmd_tf_vars(cfg: Config, _a) -> None:
    r, net, ocr = cfg.raw, cfg["network"], cfg["ocr"]
    kms_arns = []
    bedrock_models = []
    for spec in r["llm_backends"].values():
        auth = spec.get("auth") or {}
        if auth.get("type") == "oauth2_kms_jwt":
            reg = auth.get("kms_region") or cfg.region
            kid = auth["kms_key_id"]
            kms_arns.append(kid if kid.startswith("arn:") else f"arn:aws:kms:{reg}:{r['aws']['account_id']}:key/{kid}")
        if spec.get("provider") == "bedrock":
            bedrock_models.append(spec["model_id"])
    tfvars = {
        "name_prefix": cfg.prefix,
        "account_id": str(r["aws"]["account_id"]),
        "region": cfg.region,
        "permissions_boundary_arn": r["aws"].get("permissions_boundary_arn"),
        "tags": r["aws"].get("tags") or {},
        "network_mode": net["mode"],
        "existing_vpc_id": net.get("existing", {}).get("vpc_id"),
        "existing_vpc_name_tag": net.get("existing", {}).get("vpc_name_tag"),
        "existing_subnet_ids": net.get("existing", {}).get("subnet_ids") or [],
        "existing_subnet_name_tag_glob": net.get("existing", {}).get("subnet_name_tag_glob"),
        "create_cidr": net.get("create", {}).get("cidr", "10.42.0.0/24"),
        "ecs_cluster": cfg["ecs"]["cluster"],
        "runner_cpu": int(r["runner"]["cpu"]),
        "runner_memory": int(r["runner"]["memory"]),
        "create_ocr_model": bool(_a.with_model),
        "ocr_image_tag": "sagemaker",
        "bench_image_tag": "latest",
        "model_s3_prefix": cfg.model_prefix,
        "sagemaker_network_isolation": bool(ocr["sagemaker"].get("network_isolation", True)),
        "max_num_seqs": "64",
        "kms_sign_key_arns": sorted(set(kms_arns)),
        "bedrock_model_ids": sorted(set(bedrock_models)),
    }
    sd = state_dir(cfg)
    (sd / "terraform.tfvars.json").write_text(json.dumps(tfvars, indent=1))
    ts = r["terraform_state"]
    if ts.get("bucket"):
        backend = (
            f'terraform {{\n  backend "s3" {{\n    bucket = "{ts["bucket"]}"\n    key = "{ts["key"]}"\n'
            f'    region = "{cfg.region}"\n    encrypt = true\n'
            + (f'    dynamodb_table = "{ts["dynamodb_table"]}"\n' if ts.get("dynamodb_table") else "")
            + "  }\n}\n"
        )
    else:
        backend = f'terraform {{\n  backend "local" {{\n    path = "{sd / "terraform.tfstate"}"\n  }}\n}}\n'
    (ROOT / "infra" / "terraform" / "backend.tf").write_text(backend)
    print(sd / "terraform.tfvars.json")


# ------------------------------------------------------------------ simple commands
def cmd_docs(cfg: Config, a) -> None:
    from .docgen import generate

    d = cfg["bench"]["docs"]
    m = generate(
        Path(a.docs), per_type=a.per_type or d.get("per_type", 6), seed=d.get("seed", 7), scale=d.get("scale", 2.0)
    )
    print(f"{len(m)} documents, {sum(len(x['pages']) for x in m)} pages -> {a.docs}")


def cmd_upload_weights(cfg: Config, a) -> None:
    from .weights import upload

    upload(cfg, Path(a.from_dir).expanduser() if a.from_dir else None)


def cmd_endpoint(cfg: Config, a) -> None:
    from . import aws

    {
        "up": aws.endpoint_up,
        "down": aws.endpoint_down,
        "wait": aws.endpoint_wait,
        "status": lambda c: print(aws.endpoint_instance_type(c) or "no endpoint"),
    }[a.action](cfg)


def cmd_images(cfg: Config, a) -> None:
    from . import images

    if a.which in ("ocr", "all"):
        images.build_ocr(cfg, skip_mirror=a.skip_mirror)
    if a.which in ("bench", "all"):
        images.build_bench(cfg)


def cmd_transcribe(cfg: Config, a) -> None:
    from .transcribe import run

    asyncio.run(
        run(
            cfg,
            Path(a.docs),
            Path(a.out),
            backend=a.backend,
            concurrency=a.concurrency,
            variant=a.variant,
            limit=a.limit,
            repeat=a.repeat,
            warmup=not a.no_warmup,
        )
    )


def cmd_extract(cfg: Config, a) -> None:
    from .extract import run

    asyncio.run(run(cfg, Path(a.docs), Path(a.out), pipelines=a.pipeline or None, variant=a.variant, limit=a.limit))


def _run_plan(cfg: Config, docs: Path, out: Path, plan: str, extract: bool) -> None:
    from .extract import run as extract_run
    from .transcribe import run as transcribe_run

    items = cfg.plan(plan)
    for i, it in enumerate(items, 1):
        print(f"\n=== [{i}/{len(items)}] {json.dumps(it)}", flush=True)
        asyncio.run(
            transcribe_run(
                cfg,
                docs,
                out,
                backend=it["backend"],
                concurrency=int(it.get("concurrency", 1)),
                variant=it.get("variant", "both"),
                limit=int(it.get("limit", 0)),
                repeat=int(it.get("repeat", 1)),
                warmup=it.get("warmup", True),
            )
        )
    if extract:
        lim = max((int(it.get("limit", 0)) for it in items), default=0)
        variant = (
            items[0].get("variant", "both")
            if all(it.get("variant") == items[0].get("variant") for it in items)
            else "both"
        )
        print("\n=== extraction", flush=True)
        asyncio.run(extract_run(cfg, docs, out, variant=variant, limit=lim))


def cmd_task_run(cfg: Config, a) -> None:
    """Inside Fargate: pull docs from S3, run plan + extraction, push results back after each step."""
    from . import aws

    work = Path(tempfile.mkdtemp(prefix="ocrbench-"))
    docs, out = work / "docs", work / "results" / a.tag
    n = aws.s3_sync_down(cfg.bucket, "docs", docs)
    print(f"synced {n} doc files", flush=True)
    try:
        if a.extract_only:
            from .extract import run as extract_run

            print(f"pulled {aws.s3_sync_down(cfg.bucket, f'results/{a.tag}', out)} existing result files", flush=True)
            asyncio.run(extract_run(cfg, docs, out))
        else:
            _run_plan(cfg, docs, out, a.plan, extract=not a.no_extract)
    finally:
        if out.exists():
            print(f"uploaded {aws.s3_sync_up(out, cfg.bucket, f'results/{a.tag}')} result files", flush=True)


def cmd_run(cfg: Config, a) -> None:
    from . import aws

    docs = Path(a.docs)
    if not (docs / "manifest.json").exists():
        raise SystemExit(f"{docs}/manifest.json missing — run `ocrbench docs` first")
    if cfg["runner"]["mode"] == "local":
        if a.extract_only:
            from .extract import run as extract_run

            asyncio.run(extract_run(cfg, docs, Path(a.out) / a.tag))
        else:
            _run_plan(cfg, docs, Path(a.out) / a.tag, a.plan, extract=not a.no_extract)
        return
    o = outputs(cfg)
    print(f"docs -> s3://{cfg.bucket}/docs: {aws.s3_sync_up(docs, cfg.bucket, 'docs')} files uploaded")
    import boto3

    boto3.client("s3").upload_file(str(cfg.path), cfg.bucket, "config/bench.yaml")
    args = ["--config", "s3://config/bench.yaml", "task-run", "--plan", a.plan, "--tag", a.tag]
    if a.no_extract:
        args.append("--no-extract")
    if a.extract_only:
        args.append("--extract-only")
    _, code = aws.run_task(cfg, o, args)
    cmd_fetch(cfg, a)
    if code != 0:
        raise SystemExit(f"bench task exited with {code}")


def cmd_fetch(cfg: Config, a) -> None:
    from . import aws

    n = aws.s3_sync_down(cfg.bucket, f"results/{a.tag}", Path(a.out) / a.tag)
    print(f"fetched {n} files -> {Path(a.out) / a.tag}")


def cmd_report(cfg: Config, a) -> None:
    from . import aws, reporting
    from .score import score

    run_dir = Path(a.out) / a.tag
    sm = cfg["ocr"].setdefault("sagemaker", {})
    if sm.get("price_per_hour_usd") is None:
        runs = run_dir / "runs.jsonl"
        itype = (
            next(
                (
                    json.loads(x).get("instance_type")
                    for x in runs.read_text().splitlines()
                    if x.strip() and json.loads(x).get("instance_type")
                ),
                None,
            )
            if runs.exists()
            else None
        )
        if itype:
            sm["price_per_hour_usd"] = aws.sagemaker_hourly_usd(cfg.region, itype)
            print(f"GPU price: {itype} = {sm['price_per_hour_usd']} USD/h")
    s = score(cfg, Path(a.docs), run_dir, a.tag)
    findings = run_dir / "findings.md"
    idx = reporting.write(
        s, Path(a.docs), run_dir, Path(a.site) / a.tag, findings.read_text() if findings.exists() else ""
    )
    print(f"report: {run_dir / 'report.md'}\nsite:   {idx}")


# ------------------------------------------------------------------ main
def _resolve_config(path: str | None) -> Config:
    if path and path.startswith("s3://"):  # inside the task: s3://<key> in the bench bucket
        import boto3

        key = path[len("s3://") :]
        local = Path(tempfile.mkdtemp()) / "bench.yaml"
        boto3.client("s3").download_file(os.environ["OCRBENCH_BUCKET"], key, str(local))
        path = str(local)
    return config_mod.load(path)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        prog="ocrbench", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--config", default=None, help="config YAML (default $OCRBENCH_CONFIG or config/example.yaml)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, out=True):
        p.add_argument("--docs", default=str(ROOT / "docs-out"))
        if out:
            p.add_argument("--out", default=str(ROOT / "results"))
        return p

    p = common(sub.add_parser("docs"), out=False)
    p.add_argument("--per-type", type=int, default=0)
    p = sub.add_parser("tf-vars")
    p.add_argument("--with-model", action="store_true", help="also create the SageMaker model (weights + image ready)")
    p = sub.add_parser("upload-weights")
    p.add_argument("--from-dir", default=None, help="folder with browser-downloaded large files")
    p = sub.add_parser("endpoint")
    p.add_argument("action", choices=["up", "down", "wait", "status"])
    p = sub.add_parser("images")
    p.add_argument("which", choices=["ocr", "bench", "all"])
    p.add_argument("--skip-mirror", action="store_true", help="reuse the already-mirrored :mirror tag")
    p = common(sub.add_parser("transcribe"))
    p.add_argument("--backend", required=True)
    p.add_argument("--concurrency", type=int, default=1)
    p.add_argument("--variant", default="both", choices=["clean", "degraded", "both"])
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--no-warmup", action="store_true")
    p = common(sub.add_parser("extract"))
    p.add_argument("--pipeline", action="append")
    p.add_argument("--variant", default="both", choices=["clean", "degraded", "both"])
    p.add_argument("--limit", type=int, default=0)
    for name in ("run", "task-run"):
        p = common(sub.add_parser(name))
        p.add_argument("--plan", required=True)
        p.add_argument("--tag", required=True)
        p.add_argument("--no-extract", action="store_true")
        p.add_argument(
            "--extract-only",
            action="store_true",
            help="skip stage 1; re-run stage 2 on the transcripts already in results/<tag>",
        )
    p = common(sub.add_parser("fetch"))
    p.add_argument("--tag", required=True)
    p = common(sub.add_parser("report"))
    p.add_argument("--tag", required=True)
    p.add_argument("--site", default=str(ROOT / "site"))

    a = ap.parse_args(argv)
    cfg = _resolve_config(a.config)
    handler = {
        "docs": cmd_docs,
        "tf-vars": cmd_tf_vars,
        "upload-weights": cmd_upload_weights,
        "endpoint": cmd_endpoint,
        "images": cmd_images,
        "transcribe": cmd_transcribe,
        "extract": cmd_extract,
        "run": cmd_run,
        "task-run": cmd_task_run,
        "fetch": cmd_fetch,
        "report": cmd_report,
    }[a.cmd]
    try:
        handler(cfg, a)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
