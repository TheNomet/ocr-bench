"""ocrbench command line. Every command reads the same config (--config, $CONFIG or $OCRBENCH_CONFIG).

Setup        docs | tf-vars | upload-weights [--backend B] | images <self-hosted name>|bench|all
             endpoint up|down|wait|status [--backend B]   (B: a self-hosted backend; optional if only one)
Runs         run --plan P [--stages ...]      new run folder runs/<date>-<plan>/, all stages
             run --id ID --stages a,b         resume / redo stages of an existing run
             fetch --run ID | list | report [--run ID]   (report also rebuilds site/index.html)
Gateways     probe-models / probe-task        which model ids does a gateway accept?

Stages: transcribe (1) -> extract (2) -> score (3) -> summarize (4, LLM-written findings).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from . import config as config_mod
from .config import ROOT, Config
from .fileio import write_if_changed


def state_dir(cfg: Config) -> Path:
    return cfg.state_dir


def outputs(cfg: Config) -> dict:
    p = state_dir(cfg) / "outputs.json"
    if not p.exists():
        raise SystemExit(f"{p} not found — run `just deploy` (it writes Terraform outputs there)")
    return {k: v["value"] for k, v in json.loads(p.read_text()).items()}


# ------------------------------------------------------------------ tf-vars
def _pinned_images(cfg: Config) -> dict:
    """Deploy exactly the images that were built (by digest); tags only as a first-deploy fallback."""
    from .images import recorded

    refs = recorded(cfg)
    sh = refs.get("selfhosted") or {}
    return {
        "selfhosted": {
            k: {
                "image_ref": (sh.get(k) or {}).get("image", ""),
                "network_isolation": bool((cfg.backend(k).get("sagemaker") or {}).get("network_isolation", True)),
            }
            for k in cfg.selfhosted()
            if not cfg.backend(k).get("direct_url")
        },
        "bench_image_ref": refs.get("bench_image", ""),
    }


def cmd_tf_vars(cfg: Config, _a) -> None:
    r, net = cfg.raw, cfg["network"]
    kms_arns = []
    bedrock_models = []
    for spec in cfg.backends.values():
        auth = spec.get("auth") or {}
        if auth.get("type") == "oauth2_kms_jwt":
            reg = auth.get("kms_region") or cfg.region
            kid = auth["kms_key_id"]
            kms_arns.append(kid if kid.startswith("arn:") else f"arn:aws:kms:{reg}:{r['aws']['account_id']}:key/{kid}")
        if spec["kind"] == "bedrock":
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
        "runner_max_minutes": int(r["runner"].get("max_minutes", 240)),
        "create_selfhosted_models": bool(_a.with_model),
        **_pinned_images(cfg),
        "kms_sign_key_arns": sorted(set(kms_arns)),
        "bedrock_model_ids": sorted(set(bedrock_models)),
    }
    sd = state_dir(cfg)
    (sd / "terraform.tfvars.json").write_text(json.dumps(tfvars, indent=1))
    ts = r["terraform_state"]
    if ts.get("bucket"):
        kind, kv = "s3", {"bucket": f'"{ts["bucket"]}"', "key": f'"{ts["key"]}"', "region": f'"{cfg.region}"'}
        kv["encrypt"] = "true"
        if ts.get("dynamodb_table"):
            kv["dynamodb_table"] = f'"{ts["dynamodb_table"]}"'
    else:
        kind, kv = "local", {"path": f'"{sd / "terraform.tfstate"}"'}
    w = max(map(len, kv))  # aligned like `terraform fmt`, so `just lint` passes on the generated file
    body = "".join(f"    {k.ljust(w)} = {v}\n" for k, v in kv.items())
    backend = f'terraform {{\n  backend "{kind}" {{\n{body}  }}\n}}\n'
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

    upload(cfg, cfg.one_selfhosted(a.backend), Path(a.from_dir).expanduser() if a.from_dir else None)


def cmd_images(cfg: Config, a) -> None:
    from . import images

    sh = cfg.selfhosted()
    if a.which not in ("all", "bench", *sh):
        raise SystemExit(f"images: choose all, bench or a self-hosted backend ({', '.join(sh) or 'none'})")
    for name in sh if a.which == "all" else [a.which] if a.which in sh else []:
        if not cfg.backend(name).get("direct_url"):
            images.build_selfhosted(cfg, name)
    if a.which in ("bench", "all"):
        images.build_bench(cfg)


def cmd_endpoint(cfg: Config, a) -> None:
    from . import aws

    name = cfg.one_selfhosted(a.backend)

    def up(c, n):
        role = None
        with contextlib.suppress(SystemExit):
            role = outputs(c).get("scheduler_role_arn")
        aws.endpoint_up(c, n, role)

    {
        "up": up,
        "down": aws.endpoint_down,
        "wait": aws.endpoint_wait,
        "status": lambda c, n: print(f"{n}: {aws.endpoint_instance_type(c, n) or 'no endpoint'}"),
    }[a.action](cfg, name)


def cmd_transcribe(cfg: Config, a) -> None:
    from . import prompts
    from .transcribe import run

    asyncio.run(
        run(
            cfg,
            Path(a.docs),
            Path(a.out),
            prompt=prompts.resolve(cfg["bench"])["transcribe"],
            backend=a.backend,
            concurrency=a.concurrency,
            variant=a.variant,
            limit=a.limit,
            repeat=a.repeat,
            warmup=not a.no_warmup,
        )
    )


# ------------------------------------------------------------------ stages over a run folder
def _parse_stages(s: str) -> list[str]:
    from .runs import STAGES

    stages = STAGES if s == "all" else [x.strip() for x in s.split(",") if x.strip()]
    bad = [x for x in stages if x not in STAGES]
    if bad:
        raise SystemExit(f"unknown stage(s) {bad}; choose from {STAGES} or 'all'")
    return [x for x in STAGES if x in stages]  # canonical order


def execute_stages(cfg: Config, d: Path, stages: list[str], sync=None) -> None:
    """Run stages over runs/<id>/. `sync` (optional) is called after each stage (Fargate: upload to S3)."""
    from . import prompts, runs
    from .extract import run as extract_run
    from .score import score
    from .transcribe import run as transcribe_run

    meta = runs.load(d)
    docs = d / "docs"
    items = meta.get("plan_items") or []
    for st in stages:
        t0 = time.time()
        print(f"\n##### stage: {st}", flush=True)
        if st == "transcribe":
            for i, it in enumerate(items, 1):
                print(f"\n=== [{i}/{len(items)}] {json.dumps(it)}", flush=True)
                asyncio.run(
                    transcribe_run(
                        cfg,
                        docs,
                        d,
                        prompt=prompts.of(meta, "transcribe"),
                        backend=it["backend"],
                        concurrency=int(it.get("concurrency", 1)),
                        variant=it.get("variant", "both"),
                        limit=int(it.get("limit", 0)),
                        repeat=int(it.get("repeat", 1)),
                        warmup=it.get("warmup", True),
                    )
                )
        elif st == "extract":
            lim = 0  # extraction uses exactly the documents stage 1 attempted
            variants = {it.get("variant", "both") for it in items}
            asyncio.run(
                extract_run(
                    cfg,
                    docs,
                    d,
                    meta=meta,
                    pipelines=meta["extraction"]["pipelines"],
                    variant=variants.pop() if len(variants) == 1 else "both",
                    limit=lim,
                )
            )
        elif st == "score":
            s = score(runs.load(d), d)
            write_if_changed(d / "scores.json", json.dumps(s, ensure_ascii=False, indent=1))
            print(f"scored: {len(s['transcription']['pages'])} pages, {len(s['extraction']['docs'])} extractions")
        elif st == "summarize":
            from . import summarize

            sp = d / "scores.json"
            if not sp.exists():
                raise SystemExit("summarize needs scores.json: run the score stage first")
            summarize.run(cfg, d, runs.load(d), json.loads(sp.read_text(encoding="utf-8")))
        runs.stage(d, st, finished=datetime.now().isoformat(timespec="seconds"), seconds=round(time.time() - t0, 1))
        if sync:
            sync()


def cmd_task_run(cfg: Config, a) -> None:
    """Inside Fargate: pull runs/<id>/ from S3, run the stages, push back after each stage."""
    from . import aws

    d = Path(tempfile.mkdtemp(prefix="ocrbench-")) / a.run
    print(f"pulled {aws.s3_sync_down(cfg.bucket, f'runs/{a.run}', d)} files of run {a.run}", flush=True)

    def sync():
        print(f"uploaded {aws.s3_sync_up(d, cfg.bucket, f'runs/{a.run}')} changed files", flush=True)

    try:
        execute_stages(cfg, d, _parse_stages(a.stages), sync)
    finally:
        sync()


def cmd_run(cfg: Config, a) -> None:
    from . import aws, runs

    stages = _parse_stages(a.stages)
    if a.id and runs.run_dir(a.id).exists():
        d = runs.run_dir(a.id)
        print(f"resuming run {a.id}: stages {stages}")
        if "summarize" in stages:
            runs.refresh_summary_setup(cfg, d)
            print(f"summary model: {runs.load(d)['summary']['agent']}")
    else:
        if not a.plan:
            raise SystemExit("--plan is required for a new run")
        docs = Path(a.docs)
        if not (docs / "manifest.json").exists():
            raise SystemExit(f"{docs}/manifest.json missing: run `ocrbench docs` first")
        d = runs.create(cfg, a.plan, docs, a.id)
        print(f"new run {d.name}: {d}")
    meta = runs.load(d)
    if "transcribe" in stages:
        for name in dict.fromkeys(it["backend"] for it in meta.get("plan_items") or []):
            if cfg.kind(name) == "selfhosted":
                b = runs.record_endpoint(cfg, d, name)
                print(f"{name} host: {b.get('instance_type')} ({b.get('gpu')}), {b.get('price_per_hour_usd')} USD/h")
    if cfg["runner"]["mode"] == "local":
        execute_stages(cfg, d, stages)
    else:
        o = outputs(cfg)
        print(f"run -> s3://{cfg.bucket}/runs/{d.name}: {aws.s3_sync_up(d, cfg.bucket, f'runs/{d.name}')} files")
        args = [
            "--config",
            f"s3://runs/{d.name}/config.yaml",
            "task-run",
            "--run",
            d.name,
            "--stages",
            ",".join(stages),
        ]
        _, code = aws.run_task(cfg, o, args)
        print(f"fetched {aws.s3_sync_down(cfg.bucket, f'runs/{d.name}', d)} files -> {d}")
        if code != 0:
            raise SystemExit(f"bench task exited with {code}")
    _report(cfg, [d.name])


def cmd_fetch(cfg: Config, a) -> None:
    from . import aws, runs

    d = runs.run_dir(a.run)
    print(f"fetched {aws.s3_sync_down(cfg.bucket, f'runs/{a.run}', d)} files -> {d}")


def _report(cfg: Config, ids: list[str]) -> None:
    from . import reporting, runs
    from .report import build_experiments_index
    from .score import score

    site = ROOT / "site"
    for rid in ids:
        d = runs.run_dir(rid)
        meta = runs.load(d)
        if not (d / "transcripts.jsonl").exists():
            print(f"{rid}: no transcripts yet, skipped")
            continue
        s = score(meta, d)
        idx = reporting.write(meta, s, d, site)
        print(f"{rid}: {d / 'report.md'}  |  {idx}")
    items = []
    for meta in runs.all_runs():
        d = runs.run_dir(meta["id"])
        sp = d / "scores.json"
        if not sp.exists():
            continue
        s = json.loads(sp.read_text(encoding="utf-8"))
        body, _ = reporting.read_summary(d)
        bl = ""
        if body:  # first paragraph under "## Bottom line"
            parts = body.split("## Bottom line", 1)
            bl = parts[1].split("\n## ", 1)[0].strip() if len(parts) == 2 else ""
        items.append(
            {
                "meta": meta,
                "card_meta": reporting.focused_meta(meta),
                "scores": s,
                "headline": reporting.headline(meta, s),
                "bottom_line": bl,
            }
        )
    cmp_md = reporting.comparison_md(reporting.comparison(items))
    guide = reporting.guide_md(items[0]["meta"] if items else None)
    print(f"experiments index: {build_experiments_index(items, site, comparison_md=cmp_md, guide_md=guide)}")


def cmd_report(cfg: Config, a) -> None:
    from . import runs

    _report(cfg, [a.run] if a.run else [m["id"] for m in runs.all_runs()])


def cmd_list(cfg: Config, a) -> None:
    from . import runs

    for m in runs.all_runs():
        gpus = [b.get("instance_type") or "?" for b in m["backends"].values() if b["kind"] == "selfhosted"]
        done = ",".join(m.get("stages", {})) or "-"
        print(f"{m['id']:32s} plan={m.get('plan'):8s} gpu={','.join(gpus) or '-':16s} stages={done}")


def cmd_probe_models(cfg: Config, a) -> None:
    """List what each openai_compatible backend's gateway offers and try a 1-token call per candidate id."""
    import httpx

    from .backends import Request, get_backend

    async def go():
        for name, spec in cfg.backends.items():
            if spec["kind"] != "openai_compatible" or (a.backend and name not in a.backend):
                continue
            be = get_backend(cfg, name)
            print(f"\n== {name} ({spec['base_url']})", flush=True)
            try:
                hdr = await be._auth(False)
                r = await be.client.get(spec["base_url"].rstrip("/") + "/v1/models", headers=hdr)
                ids = [m.get("id") for m in (r.json().get("data") or [])] if r.status_code < 400 else []
                print(f"  /v1/models: HTTP {r.status_code}, {len(ids)} models")
                for i in sorted(x for x in ids if x and (not a.filter or a.filter in x)):
                    print(f"    {i}")
            except (httpx.HTTPError, ValueError) as e:
                print(f"  /v1/models failed: {e}")
            for mid in a.model or [spec["model"]]:
                be.model = mid
                try:
                    resp = await be.complete(Request("Reply with OK.", "Say OK.", max_tokens=5))
                    print(f"  call {mid}: OK ({resp.text.strip()[:20]!r})", flush=True)
                except Exception as e:  # noqa: BLE001
                    print(f"  call {mid}: FAILED {str(e)[:200]}", flush=True)

    asyncio.run(go())


def cmd_probe_task(cfg: Config, a) -> None:
    """Run probe-models inside the VPC (Fargate), for gateways only reachable there."""
    import boto3

    from . import aws

    boto3.client("s3").upload_file(str(cfg.path), cfg.bucket, "runs/_probe/config.yaml")
    args = ["--config", "s3://runs/_probe/config.yaml", "probe-models"]
    for m in a.model or []:
        args += ["--model", m]
    for b in a.backend or []:
        args += ["--backend", b]
    if a.filter:
        args += ["--filter", a.filter]
    aws.run_task(cfg, outputs(cfg), args)


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
    ap.add_argument(
        "--config", default=None, help="config YAML (default $CONFIG / $OCRBENCH_CONFIG / config/example.yaml)"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("docs", help="generate the synthetic document set into docs-out/")
    p.add_argument("--docs", default=str(ROOT / "docs-out"))
    p.add_argument("--per-type", type=int, default=0)
    p = sub.add_parser("tf-vars")
    p.add_argument("--with-model", action="store_true", help="also create the SageMaker model (weights + image ready)")
    p = sub.add_parser("upload-weights")
    p.add_argument("--backend", default=None, help="self-hosted backend (optional if there is only one)")
    p.add_argument("--from-dir", default=None, help="folder with browser-downloaded large files")
    p = sub.add_parser("images")
    p.add_argument("which", help="all | bench | <self-hosted backend name>")
    p = sub.add_parser("endpoint")
    p.add_argument("action", choices=["up", "down", "wait", "status"])
    p.add_argument("--backend", default=None, help="self-hosted backend (optional if there is only one)")
    p = sub.add_parser("transcribe", help="low-level: one stage-1 run into an arbitrary folder")
    p.add_argument("--docs", default=str(ROOT / "docs-out"))
    p.add_argument("--out", required=True)
    p.add_argument("--backend", required=True)
    p.add_argument("--concurrency", type=int, default=1)
    p.add_argument("--variant", default="both", choices=["clean", "degraded", "both"])
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--no-warmup", action="store_true")
    p = sub.add_parser("run", help="new run (or resume one with --id) through the given stages")
    p.add_argument("--plan", default=None)
    p.add_argument("--id", default=None, help="existing run id to resume, or a custom id for a new run")
    p.add_argument("--stages", default="all", help="comma list of transcribe,extract,score,summarize or 'all'")
    p.add_argument("--docs", default=str(ROOT / "docs-out"))
    p = sub.add_parser("task-run", help="entrypoint inside the Fargate task")
    p.add_argument("--run", required=True)
    p.add_argument("--stages", default="all")
    p = sub.add_parser("fetch")
    p.add_argument("--run", required=True)
    p = sub.add_parser("report", help="re-score + report.md + site for one run (or all) + experiments index")
    p.add_argument("--run", default=None)
    sub.add_parser("list", help="list runs")
    for name in ("probe-models", "probe-task"):
        p = sub.add_parser(
            name, help="check which model ids a gateway accepts" + (" (from Fargate)" if name == "probe-task" else "")
        )
        p.add_argument("--model", action="append", help="model id to try (repeatable); default: the configured one")
        p.add_argument("--backend", action="append", help="limit to these backends")
        p.add_argument("--filter", default=None, help="only list /v1/models ids containing this")

    a = ap.parse_args(argv)
    cfg = _resolve_config(a.config)
    handler = {
        "docs": cmd_docs,
        "tf-vars": cmd_tf_vars,
        "upload-weights": cmd_upload_weights,
        "images": cmd_images,
        "endpoint": cmd_endpoint,
        "transcribe": cmd_transcribe,
        "run": cmd_run,
        "task-run": cmd_task_run,
        "fetch": cmd_fetch,
        "report": cmd_report,
        "list": cmd_list,
        "probe-models": cmd_probe_models,
        "probe-task": cmd_probe_task,
    }[a.cmd]
    try:
        handler(cfg, a)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
