"""Small AWS helpers shared by the CLI (S3 sync, endpoint lifecycle, Fargate task, pricing)."""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.exceptions import BotoCoreError, ClientError

from .config import CONFIG_STAMP, Config, endpoint_config_name

# ---------------------------------------------------------------------------- S3
# Files are uploaded in one part (up to 256 MB), so an object's ETag is the MD5 of its content and a sync can
# compare content, not just size. Larger (multipart) objects fall back to comparing size.
_SINGLE_PART = TransferConfig(multipart_threshold=256 * 1024 * 1024)


def _md5(path: Path) -> str:
    h = hashlib.md5()  # noqa: S324 — content fingerprint matching S3 ETags, not security
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _same(path: Path, obj: dict) -> bool:
    if not path.exists() or path.stat().st_size != obj["Size"]:
        return False
    etag = obj["ETag"].strip('"')
    return "-" in etag or _md5(path) == etag


def _objects(s3, bucket: str, prefix: str) -> dict[str, dict]:
    out = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix.rstrip("/") + "/"):
        for o in page.get("Contents", []):
            out[o["Key"]] = o
    return out


def s3_sync_up(local: Path, bucket: str, prefix: str, *, delete: bool = False) -> int:
    """Upload new or changed files. delete=True also removes keys under the prefix that no longer exist
    locally (files a stage replaced, e.g. extractions.partial.jsonl): only for a folder pulled from the prefix."""
    s3 = boto3.client("s3")
    existing = _objects(s3, bucket, prefix)
    n, seen = 0, set()
    for f in sorted(local.rglob("*")):
        if not f.is_file():
            continue
        key = f"{prefix}/{f.relative_to(local).as_posix()}"
        seen.add(key)
        if key in existing and _same(f, existing[key]):
            continue
        s3.upload_file(str(f), bucket, key, Config=_SINGLE_PART)
        n += 1
    gone = sorted(set(existing) - seen) if delete else []
    for i in range(0, len(gone), 1000):
        r = s3.delete_objects(
            Bucket=bucket, Delete={"Objects": [{"Key": k} for k in gone[i : i + 1000]], "Quiet": True}
        )
        if r.get("Errors"):
            raise SystemExit(f"could not delete {len(r['Errors'])} stale objects under s3://{bucket}/{prefix}/")
    return n


def s3_sync_down(bucket: str, prefix: str, local: Path, *, prune: tuple[str, ...] = ()) -> int:
    """Download new or changed files; never deletes local files, except those matching a `prune` glob
    (relative to `local`) that S3 doesn't have: leftovers a later stage removed there, e.g. the
    extractions.partial.jsonl of a killed task. Files that only exist locally (config.yaml, report.md) stay."""
    s3 = boto3.client("s3")
    n = 0
    objects = _objects(s3, bucket, prefix)
    for key, o in objects.items():
        dst = local / key[len(prefix.rstrip("/")) + 1 :]
        if _same(dst, o):
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        s3.download_file(bucket, key, str(dst))
        n += 1
    for pattern in prune if objects else ():  # an empty prefix (expired, never uploaded) says nothing
        for f in sorted(local.glob(pattern)) if local.exists() else []:
            if f.is_file() and f"{prefix.rstrip('/')}/{f.relative_to(local).as_posix()}" not in objects:
                f.unlink()
                print(f"removed {f.relative_to(local)} (no longer in s3://{bucket}/{prefix.rstrip('/')}/)")
    return n


# ---------------------------------------------------------------------------- SageMaker
_POLL_S = 30
_DELETE_WAIT_S = 1800  # deleting an endpoint normally takes a few minutes


def _schedule_name(endpoint: str) -> str:
    return f"{endpoint}-auto-delete"


def _not_found(e: ClientError) -> bool:
    """SageMaker reports a missing endpoint (or model) as ValidationException "Could not find ..."."""
    err = e.response.get("Error", {})
    return err.get("Code") == "ValidationException" and "could not find" in str(err.get("Message", "")).lower()


def _error(e: Exception) -> str:
    if isinstance(e, ClientError):
        err = e.response.get("Error", {})
        return f"{err.get('Code')}: {err.get('Message')}"
    return f"{type(e).__name__}: {e}"


def schedule_auto_delete(cfg: Config, name: str, role_arn: str | None) -> None:
    """One-off EventBridge Scheduler job that deletes the endpoint after backends.<name>.sagemaker.max_lifetime_hours.
    Raises SystemExit when it can't be scheduled; endpoint_up calls it before creating anything that bills."""
    hours = float(cfg.sagemaker(name)["max_lifetime_hours"])
    if hours <= 0:
        cancel_auto_delete(cfg, name)  # nor should an older schedule fire
        print("  WARNING: max_lifetime_hours is 0, no auto-delete; remember `endpoint down` (it bills per hour)")
        return
    if not role_arn:
        raise SystemExit(
            f"no scheduler role to auto-delete the endpoint after {hours:g} h (outputs.json has no "
            f"scheduler_role_arn): run `just deploy`, or set backends.{name}.sagemaker.max_lifetime_hours: 0"
        )
    sch = boto3.client("scheduler", region_name=cfg.region)
    at = (datetime.now(UTC) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
    args = dict(
        Name=_schedule_name(cfg.endpoint_name(name)),
        ScheduleExpression=f"at({at})",
        ScheduleExpressionTimezone="UTC",
        FlexibleTimeWindow={"Mode": "OFF"},
        ActionAfterCompletion="DELETE",
        Target={
            "Arn": "arn:aws:scheduler:::aws-sdk:sagemaker:deleteEndpoint",
            "RoleArn": role_arn,
            "Input": json.dumps({"EndpointName": cfg.endpoint_name(name)}),
        },
    )
    try:
        try:
            sch.create_schedule(**args)
        except sch.exceptions.ConflictException:
            sch.update_schedule(**args)
    except (BotoCoreError, ClientError) as e:
        raise SystemExit(f"could not schedule the endpoint's auto-delete ({_error(e)}); nothing was started") from e
    print(
        f"  auto-delete scheduled at {at} UTC ({hours:g} h); change with backends.{name}.sagemaker.max_lifetime_hours"
    )


def _cancel_schedule(cfg: Config, endpoint: str) -> None:
    try:
        boto3.client("scheduler", region_name=cfg.region).delete_schedule(Name=_schedule_name(endpoint))
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "ResourceNotFoundException":  # absent or already fired
            raise


def cancel_auto_delete(cfg: Config, name: str) -> None:
    _cancel_schedule(cfg, cfg.endpoint_name(name))


def is_endpoint_config_of(endpoint: str, config_name: str) -> bool:
    """True only for configs endpoint_up made for this endpoint: not for another backend whose endpoint
    name starts the same way (`<prefix>-ocr` vs `<prefix>-ocr-v2`)."""
    n = len(datetime(2000, 1, 1).strftime(CONFIG_STAMP))
    return re.fullmatch(re.escape(endpoint) + rf"-\d{{{n}}}", config_name) is not None


def endpoint_up(cfg: Config, backend: str, scheduler_role_arn: str | None = None) -> None:
    sm = boto3.client("sagemaker", region_name=cfg.region)
    s = cfg.sagemaker(backend)
    name = cfg.endpoint_name(backend)
    # The deletion is scheduled first (it doesn't need the endpoint to exist yet): if that fails, nothing bills.
    schedule_auto_delete(cfg, backend, scheduler_role_arn)
    conf = endpoint_config_name(name, datetime.now())
    pools = [{"InstanceType": t, "Priority": i} for i, t in enumerate(s["instance_pools"], start=1)]
    # Same Stack tag as the Terraform-managed resources, so GPU spend is attributable per stack.
    tag_map = {"ManagedBy": "ocrbench", "Stack": cfg.prefix, **(cfg["aws"].get("tags") or {})}
    tags = [{"Key": k, "Value": str(v)} for k, v in tag_map.items()]
    sm.create_endpoint_config(
        EndpointConfigName=conf,
        Tags=tags,
        ProductionVariants=[
            {
                "VariantName": "primary",
                "ModelName": cfg.model_name(backend),
                "InitialInstanceCount": 1,
                "InstancePools": pools,
                "InferenceAmiVersion": s["inference_ami_version"],
                "ContainerStartupHealthCheckTimeoutInSeconds": int(s["container_startup_timeout_s"]),
                "ModelDataDownloadTimeoutInSeconds": int(s["model_download_timeout_s"]),
            }
        ],
    )
    print(f"endpoint config {conf}: {', '.join(p['InstanceType'] for p in pools)}")
    try:
        sm.describe_endpoint(EndpointName=name)
        exists = True
    except ClientError as e:
        if not _not_found(e):
            raise
        exists = False
    if exists:
        sm.update_endpoint(EndpointName=name, EndpointConfigName=conf)
        print(f"updating {name}")
    else:
        sm.create_endpoint(EndpointName=name, EndpointConfigName=conf, Tags=tags)
        print(f"creating {name} (cold start is typically 10-20 min)")
    endpoint_wait(cfg, backend, expect_config=conf)


def endpoint_wait(cfg: Config, backend: str, expect_config: str | None = None) -> None:
    """Wait until InService, at most backends.<name>.sagemaker.wait_timeout_s. With `expect_config` (just
    created or updated to it), serving any other config means the update failed and was rolled back."""
    sm = boto3.client("sagemaker", region_name=cfg.region)
    timeout = float(cfg.sagemaker(backend)["wait_timeout_s"])
    t0, changing = time.time(), False
    while True:
        d = sm.describe_endpoint(EndpointName=cfg.endpoint_name(backend))
        st, why, live = d["EndpointStatus"], d.get("FailureReason"), d.get("EndpointConfigName")
        print(f"  {int(time.time() - t0):5d}s {st}", flush=True)
        if st in ("Failed", "OutOfService"):
            raise SystemExit(f"endpoint failed: {why}")
        if st == "RollingBack":
            raise SystemExit(f"endpoint update failed and is rolling back: {why}")
        if st != "InService":
            changing = True
        elif expect_config and live != expect_config:
            if changing or why:  # back to InService on the old config
                raise SystemExit(f"endpoint update rolled back (still serving {live}): {why or 'no reason given'}")
            # else: the update hasn't shown up in DescribeEndpoint yet
        else:
            if why:
                print(f"  WARNING: endpoint reports an earlier failure: {why}")
            print(f"  instance: {endpoint_instance_type(cfg, backend)}")
            return
        if time.time() - t0 > timeout:
            raise SystemExit(
                f"endpoint not InService after {timeout:g} s (status {st}); it may still bill: `endpoint down` "
                f"(limit: backends.{backend}.sagemaker.wait_timeout_s)"
            )
        time.sleep(_POLL_S)


def endpoint_info(cfg: Config, backend: str) -> dict:
    """Live endpoint facts: instance type actually placed (instance pools) and the resolved image digest."""
    sm = boto3.client("sagemaker", region_name=cfg.region)
    try:
        v = sm.describe_endpoint(EndpointName=cfg.endpoint_name(backend))["ProductionVariants"][0]
    except (BotoCoreError, ClientError, KeyError, IndexError):  # no endpoint, or no variant yet
        return {}
    live = [p["InstanceType"] for p in v.get("InstancePools") or [] if p.get("CurrentInstanceCount")]
    imgs = v.get("DeployedImages") or [{}]
    resolved = imgs[0].get("ResolvedImage", "")
    return {
        "instance_type": (live or [v.get("CurrentInstanceType")])[0],
        "image_digest": resolved.split("@", 1)[1] if "@" in resolved else None,
    }


def endpoint_instance_type(cfg: Config, backend: str) -> str | None:
    sm = boto3.client("sagemaker", region_name=cfg.region)
    try:
        v = sm.describe_endpoint(EndpointName=cfg.endpoint_name(backend))["ProductionVariants"][0]
    except (BotoCoreError, ClientError, KeyError, IndexError):  # no endpoint, or no variant yet
        return None
    pools = v.get("InstancePools") or []
    live = [p["InstanceType"] for p in pools if p.get("CurrentInstanceCount")]
    return (live or [v.get("CurrentInstanceType") or v.get("InstanceType")])[0]


def _status(sm, endpoint: str) -> str | None:
    """EndpointStatus, or None once the endpoint is gone."""
    try:
        return sm.describe_endpoint(EndpointName=endpoint)["EndpointStatus"]
    except ClientError as e:
        if _not_found(e):
            return None
        raise


def delete_endpoint(cfg: Config, endpoint: str) -> None:
    """Delete one endpoint of this stack and wait until it is gone; only then remove its auto-delete schedule
    and the endpoint configs endpoint_up made for it. Raises SystemExit if the endpoint can't be deleted,
    leaving the schedule in place as the safety net."""
    sm = boto3.client("sagemaker", region_name=cfg.region)
    try:
        sm.delete_endpoint(EndpointName=endpoint)
        print(f"deleting endpoint {endpoint}")
    except ClientError as e:
        st = None if _not_found(e) else _status(sm, endpoint)
        if st is None:
            print(f"endpoint {endpoint}: not found")
        elif st != "Deleting":
            raise SystemExit(f"could not delete endpoint {endpoint} ({st}): {_error(e)}") from e
    t0 = time.time()
    while _status(sm, endpoint) is not None:
        if time.time() - t0 > _DELETE_WAIT_S:
            raise SystemExit(f"endpoint {endpoint} still exists {_DELETE_WAIT_S} s after DeleteEndpoint")
        time.sleep(10)
    _cancel_schedule(cfg, endpoint)
    for page in sm.get_paginator("list_endpoint_configs").paginate(NameContains=endpoint):
        for c in page["EndpointConfigs"]:
            if is_endpoint_config_of(endpoint, c["EndpointConfigName"]):
                sm.delete_endpoint_config(EndpointConfigName=c["EndpointConfigName"])
                print(f"deleted config {c['EndpointConfigName']}")


def endpoint_down(cfg: Config, backend: str) -> None:
    delete_endpoint(cfg, cfg.endpoint_name(backend))


def existing_models(cfg: Config, backends: list[str]) -> list[str]:
    """The self-hosted backends whose SageMaker model (<prefix>-<name>-model) exists. For tf-vars on a machine
    without the last render: rendering none would make the next apply delete them. Fails (SystemExit) rather
    than guess: without credentials, or on any error other than "not found"."""
    if not backends:
        return []
    if boto3.Session().get_credentials() is None:
        raise SystemExit(
            "tf-vars: no previous terraform.tfvars.json here and no AWS credentials to check which SageMaker "
            "models exist (rendering none would delete them on apply): log in, or use `tf-vars --with-model`"
        )
    sm = boto3.client("sagemaker", region_name=cfg.region)
    out = []
    for b in backends:
        try:
            sm.describe_model(ModelName=cfg.model_name(b))
        except ClientError as e:
            if _not_found(e):
                continue
            raise SystemExit(f"tf-vars: could not check SageMaker model {cfg.model_name(b)} ({_error(e)})") from e
        except BotoCoreError as e:
            raise SystemExit(f"tf-vars: could not check SageMaker model {cfg.model_name(b)} ({_error(e)})") from e
        out.append(b)
    return out


def stack_endpoints(cfg: Config) -> list[str]:
    """Every live endpoint named <name_prefix>-*, configured or not: one whose backend left the config still
    bills. Endpoints tagged for another stack (a longer prefix that starts the same way) are left alone."""
    sm = boto3.client("sagemaker", region_name=cfg.region)
    out = []
    for page in sm.get_paginator("list_endpoints").paginate(NameContains=f"{cfg.prefix}-"):
        for e in page["Endpoints"]:
            if not e["EndpointName"].startswith(f"{cfg.prefix}-"):
                continue
            tags = {t["Key"]: t["Value"] for t in sm.list_tags(ResourceArn=e["EndpointArn"]).get("Tags", [])}
            if tags.get("Stack", cfg.prefix) == cfg.prefix:
                out.append(e["EndpointName"])
    return sorted(out)


def endpoint_down_all(cfg: Config) -> None:
    """Delete every endpoint of the stack (stack_endpoints) plus the configured ones' leftovers (schedules,
    configs). Keeps going past failures, then raises SystemExit if any failed or any endpoint is still there."""
    sagemaker = [b for b in cfg.selfhosted() if not cfg.backend(b).get("direct_url")]
    names = sorted(set(stack_endpoints(cfg)) | {cfg.endpoint_name(b) for b in sagemaker})
    failed: dict[str, str] = {}
    for n in names:
        try:
            delete_endpoint(cfg, n)
        except (SystemExit, BotoCoreError, ClientError) as e:
            failed[n] = str(e.code) if isinstance(e, SystemExit) else _error(e)
            print(f"FAILED {n}: {failed[n]}", flush=True)
    left = stack_endpoints(cfg)
    if failed or left:
        raise SystemExit(
            f"endpoint down: {len(failed)} of {len(names)} failed ({', '.join(failed) or '-'}); "
            f"still present: {', '.join(left) or 'none'}"
        )
    print(f"no endpoints left for {cfg.prefix}")


def instance_specs(region: str, instance_type: str) -> dict:
    """GPU and host of an ml.* type from EC2 DescribeInstanceTypes (same hardware without the "ml." prefix).
    {} if the API isn't reachable or doesn't know the type."""
    try:
        t = boto3.client("ec2", region_name=region).describe_instance_types(
            InstanceTypes=[instance_type.removeprefix("ml.")]
        )["InstanceTypes"][0]
    except (BotoCoreError, ClientError, KeyError, IndexError):  # unreachable, or an unknown type
        return {}
    gpus = (t.get("GpuInfo") or {}).get("Gpus") or []
    gpu = "?"
    if gpus:
        g = gpus[0]
        mem = round(g.get("MemoryInfo", {}).get("SizeInMiB", 0) / 1024)
        gpu = (
            f"{g['Count']}x " if g.get("Count", 1) > 1 else ""
        ) + f"{g.get('Manufacturer', '')} {g.get('Name', '')}, {mem} GB".strip()
    vcpu = t.get("VCpuInfo", {}).get("DefaultVCpus")
    ram = round(t.get("MemoryInfo", {}).get("SizeInMiB", 0) / 1024)
    return {"gpu": gpu, "host": f"{vcpu} vCPU, {ram} GB RAM"}


def sagemaker_hourly_usd(region: str, instance_type: str) -> float | None:
    try:
        pr = boto3.client("pricing", region_name="us-east-1")
        pages = pr.get_paginator("get_products").paginate(
            ServiceCode="AmazonSageMaker",
            Filters=[
                {"Type": "TERM_MATCH", "Field": "regionCode", "Value": region},
                {"Type": "TERM_MATCH", "Field": "instanceName", "Value": instance_type},
            ],
        )
        for page in pages:
            for p in page["PriceList"]:
                p = json.loads(p)
                if "Hosting" not in p["product"]["attributes"].get("component", ""):
                    continue
                for t in p["terms"]["OnDemand"].values():
                    for dim in t["priceDimensions"].values():
                        return float(dim["pricePerUnit"]["USD"])
    except (BotoCoreError, ClientError, KeyError, ValueError, TypeError):  # pricing is informational
        return None
    return None


# ---------------------------------------------------------------------------- Fargate
def _describe_task(ecs, cluster: str, arn: str, tries: int = 10) -> dict:
    """DescribeTasks is eventually consistent: right after RunTask it may not list the task yet."""
    for i in range(tries):
        tasks = ecs.describe_tasks(cluster=cluster, tasks=[arn]).get("tasks") or []
        if tasks:
            return tasks[0]
        if i < tries - 1:
            time.sleep(3)
    raise SystemExit(f"DescribeTasks does not know task {arn.rsplit('/', 1)[-1]}")


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
                "assignPublicIp": "ENABLED" if cfg["runner"]["assign_public_ip"] else "DISABLED",
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
        status = _describe_task(ecs, outputs["ecs_cluster"], arn)["lastStatus"]
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
    t, code = {}, None
    for _ in range(30):  # exitCode is filled in a little after STOPPED
        t = _describe_task(ecs, outputs["ecs_cluster"], arn)
        # pick ours by name: accounts may inject sidecars (e.g. a security agent) into every task
        ours = next((c for c in t["containers"] if c["name"] == "bench"), t["containers"][0])
        code = ours.get("exitCode")
        if code is not None:
            break
        time.sleep(2)
    print(f"task stopped: {t.get('stoppedReason')} (exit {code})")
    return task_id, code
