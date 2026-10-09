"""Small AWS helpers shared by the CLI (S3 sync, endpoint lifecycle, Fargate task, pricing)."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import boto3

from .config import Config

SM_POOL_NOTE = (
    "Endpoint config + endpoint are created here, not in Terraform: they use "
    "ProductionVariant.InstancePools (GPU-type fallback), which the AWS provider lacks."
)


# ---------------------------------------------------------------------------- S3
def s3_sync_up(local: Path, bucket: str, prefix: str) -> int:
    s3 = boto3.client("s3")
    existing = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix + "/"):
        for o in page.get("Contents", []):
            existing[o["Key"]] = o["Size"]
    n = 0
    for f in sorted(local.rglob("*")):
        if not f.is_file():
            continue
        key = f"{prefix}/{f.relative_to(local).as_posix()}"
        if existing.get(key) != f.stat().st_size:
            s3.upload_file(str(f), bucket, key)
            n += 1
    return n


def s3_sync_down(bucket: str, prefix: str, local: Path) -> int:
    s3 = boto3.client("s3")
    n = 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix.rstrip("/") + "/"):
        for o in page.get("Contents", []):
            dst = local / o["Key"][len(prefix.rstrip("/")) + 1 :]
            if dst.exists() and dst.stat().st_size == o["Size"]:
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, o["Key"], str(dst))
            n += 1
    return n


# ---------------------------------------------------------------------------- SageMaker
def endpoint_up(cfg: Config) -> None:
    sm = boto3.client("sagemaker", region_name=cfg.region)
    s = cfg["ocr"]["sagemaker"]
    name = cfg.endpoint_name
    conf = f"{name}-{datetime.now():%Y%m%d%H%M%S}"
    pools = [{"InstanceType": t, "Priority": i} for i, t in enumerate(s["instance_pools"], start=1)]
    tags = [{"Key": k, "Value": str(v)} for k, v in (cfg["aws"].get("tags") or {}).items()]
    sm.create_endpoint_config(
        EndpointConfigName=conf,
        Tags=tags,
        ProductionVariants=[
            {
                "VariantName": "primary",
                "ModelName": cfg.model_name,
                "InitialInstanceCount": 1,
                "InstancePools": pools,
                "InferenceAmiVersion": s["inference_ami_version"],
                "ContainerStartupHealthCheckTimeoutInSeconds": int(s.get("container_startup_timeout_s", 1800)),
                "ModelDataDownloadTimeoutInSeconds": int(s.get("model_download_timeout_s", 1800)),
            }
        ],
    )
    print(f"endpoint config {conf}: {', '.join(p['InstanceType'] for p in pools)}")
    try:
        sm.describe_endpoint(EndpointName=name)
        sm.update_endpoint(EndpointName=name, EndpointConfigName=conf)
        print(f"updating {name}")
    except sm.exceptions.ClientError:
        sm.create_endpoint(EndpointName=name, EndpointConfigName=conf, Tags=tags)
        print(f"creating {name} (cold start is typically 10-20 min)")
    endpoint_wait(cfg)


def endpoint_wait(cfg: Config) -> None:
    sm = boto3.client("sagemaker", region_name=cfg.region)
    t0 = time.time()
    while True:
        d = sm.describe_endpoint(EndpointName=cfg.endpoint_name)
        st = d["EndpointStatus"]
        print(f"  {int(time.time() - t0):5d}s {st}", flush=True)
        if st == "InService":
            print(f"  instance: {endpoint_instance_type(cfg)}")
            return
        if st in ("Failed", "OutOfService"):
            raise SystemExit(f"endpoint failed: {d.get('FailureReason')}")
        time.sleep(30)


def endpoint_info(cfg: Config) -> dict:
    """Live endpoint facts: instance type actually placed (instance pools) and the resolved image digest."""
    sm = boto3.client("sagemaker", region_name=cfg.region)
    try:
        v = sm.describe_endpoint(EndpointName=cfg.endpoint_name)["ProductionVariants"][0]
    except Exception:  # noqa: BLE001
        return {}
    live = [p["InstanceType"] for p in v.get("InstancePools") or [] if p.get("CurrentInstanceCount")]
    imgs = v.get("DeployedImages") or [{}]
    resolved = imgs[0].get("ResolvedImage", "")
    return {
        "instance_type": (live or [v.get("CurrentInstanceType")])[0],
        "image_digest": resolved.split("@", 1)[1] if "@" in resolved else None,
    }


def endpoint_instance_type(cfg: Config) -> str | None:
    sm = boto3.client("sagemaker", region_name=cfg.region)
    try:
        v = sm.describe_endpoint(EndpointName=cfg.endpoint_name)["ProductionVariants"][0]
    except Exception:  # noqa: BLE001
        return None
    pools = v.get("InstancePools") or []
    live = [p["InstanceType"] for p in pools if p.get("CurrentInstanceCount")]
    return (live or [v.get("CurrentInstanceType") or v.get("InstanceType")])[0]


def endpoint_down(cfg: Config) -> None:
    sm = boto3.client("sagemaker", region_name=cfg.region)
    name = cfg.endpoint_name
    try:
        sm.delete_endpoint(EndpointName=name)
        print(f"deleting endpoint {name}")
        while True:
            try:
                sm.describe_endpoint(EndpointName=name)
                time.sleep(10)
            except sm.exceptions.ClientError:
                break
    except sm.exceptions.ClientError as e:
        print(f"endpoint: {e.response['Error']['Message']}")
    for c in sm.list_endpoint_configs(NameContains=name, MaxResults=100)["EndpointConfigs"]:
        sm.delete_endpoint_config(EndpointConfigName=c["EndpointConfigName"])
        print(f"deleted config {c['EndpointConfigName']}")


def sagemaker_hourly_usd(region: str, instance_type: str) -> float | None:
    try:
        pr = boto3.client("pricing", region_name="us-east-1")
        r = pr.get_products(
            ServiceCode="AmazonSageMaker",
            MaxResults=20,
            Filters=[
                {"Type": "TERM_MATCH", "Field": "regionCode", "Value": region},
                {"Type": "TERM_MATCH", "Field": "instanceName", "Value": instance_type},
            ],
        )
        for p in r["PriceList"]:
            p = json.loads(p)
            if "Hosting" not in p["product"]["attributes"].get("component", ""):
                continue
            for t in p["terms"]["OnDemand"].values():
                for dim in t["priceDimensions"].values():
                    return float(dim["pricePerUnit"]["USD"])
    except Exception:  # noqa: BLE001 — pricing is informational
        return None
    return None


# ---------------------------------------------------------------------------- Fargate
def run_task(cfg: Config, outputs: dict, args: list[str]) -> tuple[str, int | None]:
    ecs = boto3.client("ecs", region_name=cfg.region)
    r = ecs.run_task(
        cluster=outputs["ecs_cluster"],
        launchType="FARGATE",
        taskDefinition=outputs["bench_task_definition"],
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": outputs["subnet_ids"],
                "securityGroups": [outputs["security_group_id"]],
                "assignPublicIp": "DISABLED",
            }
        },
        overrides={"containerOverrides": [{"name": "bench", "command": args}]},
    )
    if r.get("failures"):
        raise SystemExit(f"run_task failed: {r['failures']}")
    arn = r["tasks"][0]["taskArn"]
    task_id = arn.rsplit("/", 1)[1]
    print(f"task {task_id} started; streaming logs", flush=True)
    logs = boto3.client("logs", region_name=cfg.region)
    group, stream = outputs["log_group"], f"bench/bench/{task_id}"
    token, status = None, "PENDING"
    while True:
        status = ecs.describe_tasks(cluster=outputs["ecs_cluster"], tasks=[arn])["tasks"][0]["lastStatus"]
        try:
            kw = {"logGroupName": group, "logStreamName": stream, "startFromHead": True}
            if token:
                kw["nextToken"] = token
            ev = logs.get_log_events(**kw)
            for e in ev["events"]:
                print(e["message"], flush=True)
            token = ev["nextForwardToken"]
        except logs.exceptions.ResourceNotFoundException:
            pass
        if status == "STOPPED":
            break
        time.sleep(10)
    for _ in range(30):  # exitCode is filled in a little after STOPPED
        t = ecs.describe_tasks(cluster=outputs["ecs_cluster"], tasks=[arn])["tasks"][0]
        # pick ours by name: accounts may inject sidecars (e.g. a security agent) into every task
        ours = next((c for c in t["containers"] if c["name"] == "bench"), t["containers"][0])
        code = ours.get("exitCode")
        if code is not None:
            break
        time.sleep(2)
    print(f"task stopped: {t.get('stoppedReason')} (exit {code})")
    return task_id, code
