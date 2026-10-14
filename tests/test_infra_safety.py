"""Cost and safety rails around the AWS lifecycle, against stubbed clients: no network, no credentials.

Config validation, fail-closed endpoint up/down, rollback detection, weights revisions, tf-vars,
the task-definition drift check and SIGTERM handling in the Fargate task."""

from __future__ import annotations

import json
import os
import signal
import types
from datetime import datetime
from pathlib import Path

import boto3
import pytest
import yaml
from botocore.stub import ANY, Stubber

from ocrbench import aws, cli, weights
from ocrbench import config as config_mod

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "config" / "example.yaml"
T0 = datetime(2026, 1, 1)


@pytest.fixture(autouse=True)
def _creds(monkeypatch):
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(k, "testing")
    monkeypatch.setenv("AWS_CONFIG_FILE", "/nonexistent")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/nonexistent")


@pytest.fixture
def cfg():
    c = config_mod.load(EXAMPLE)
    os.environ.pop("AWS_PROFILE", None)
    return c


@pytest.fixture
def clock(monkeypatch):
    """aws.time with a fake clock: sleep() advances it instantly."""
    now = [1000.0]
    fake = types.SimpleNamespace(time=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s))
    monkeypatch.setattr(aws, "time", fake)
    return now


def stubs(monkeypatch, *services: str, region: str = "eu-west-1") -> dict[str, Stubber]:
    clients = {s: boto3.client(s, region_name=region) for s in services}
    monkeypatch.setattr(aws.boto3, "client", lambda service, **_: clients[service])
    return {s: Stubber(c) for s, c in clients.items()}


def endpoint(name: str, status: str = "InService", **kw) -> dict:
    return {
        "EndpointName": name,
        "EndpointArn": f"arn:aws:sagemaker:eu-west-1:000000000000:endpoint/{name}",
        "EndpointStatus": status,
        "CreationTime": T0,
        "LastModifiedTime": T0,
        **kw,
    }


NOT_FOUND = ("ValidationException", 'Could not find endpoint "x"')


# ------------------------------------------------------------------ config validation
def _load_with(tmp_path: Path, edit) -> config_mod.Config:
    raw = yaml.safe_load(EXAMPLE.read_text())
    edit(raw)
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(raw))
    return config_mod.load(p)


@pytest.mark.parametrize(
    ("edit", "match"),
    [
        (lambda r: r["backends"]["unlimited-ocr"]["sagemaker"].update(max_lifetime_hours="8h"), "max_lifetime_hours"),
        (lambda r: r["backends"]["unlimited-ocr"]["sagemaker"].update(max_lifetime_hours=-1), "max_lifetime_hours"),
        (lambda r: r["backends"]["unlimited-ocr"]["sagemaker"].update(wait_timeout_s=0), "wait_timeout_s"),
        (lambda r: r["runner"].update(max_minutes=0), "runner.max_minutes"),
        (lambda r: r["runner"].update(stop_grace_seconds=-5), "runner.stop_grace_seconds"),
        (lambda r: r["runner"].update(assign_public_ip="DISABLED"), "assign_public_ip"),
        (lambda r: r["bench"]["plans"]["smoke"][0].update(variant="cleen"), "variant 'cleen'"),
        (lambda r: r["backends"]["sonnet-4.5"].update(reasoning_effort="medium"), "reasoning_effort"),
    ],
)
def test_validate_rejects(tmp_path, edit, match):
    with pytest.raises(ValueError, match=match):
        _load_with(tmp_path, edit)


def test_validate_checks_the_longest_sagemaker_name(tmp_path):
    """The endpoint config <prefix>-<name>-<14 digits> is the longest name, not <prefix>-<name>-model."""

    def rename(r, prefix, name):
        r["name_prefix"] = prefix
        r["backends"][name] = r["backends"].pop("unlimited-ocr")
        r["bench"]["extraction"]["pipelines"] = ["image->agent"]
        r["bench"]["plans"] = {}

    ok_len = 63 - 15  # "-" + 14-digit stamp
    prefix = "p" * 20
    ok = "n" * (ok_len - len(prefix) - 1)
    c = _load_with(tmp_path, lambda r: rename(r, prefix, ok))
    assert len(aws.endpoint_config_name(c.endpoint_name(ok), datetime.now())) == 63
    with pytest.raises(ValueError, match="endpoint config"):
        _load_with(tmp_path, lambda r: rename(r, prefix, ok + "n"))  # model name would still fit


def test_defaults_come_from_config(cfg):
    assert cfg["runner"]["max_minutes"] == 240 and cfg["runner"]["assign_public_ip"] is False
    assert cfg.sagemaker("unlimited-ocr")["max_lifetime_hours"] == 8
    assert cfg.sagemaker("unlimited-ocr")["wait_timeout_s"] == config_mod.SAGEMAKER_DEFAULTS["wait_timeout_s"]


# ------------------------------------------------------------------ endpoint up: fail closed
def test_endpoint_up_without_role_creates_nothing(cfg, monkeypatch):
    s = stubs(monkeypatch, "sagemaker", "scheduler")
    with s["sagemaker"], s["scheduler"], pytest.raises(SystemExit, match="no scheduler role"):
        aws.endpoint_up(cfg, "unlimited-ocr", None)  # any AWS call would hit an empty Stubber and fail


def test_endpoint_up_schedules_before_anything_bills(cfg, monkeypatch):
    s = stubs(monkeypatch, "sagemaker", "scheduler")
    s["scheduler"].add_client_error("create_schedule", "AccessDeniedException", "no", 403)
    with s["sagemaker"], s["scheduler"], pytest.raises(SystemExit, match="could not schedule"):
        aws.endpoint_up(cfg, "unlimited-ocr", "arn:aws:iam::000000000000:role/sched")
    s["scheduler"].assert_no_pending_responses()


def test_cli_endpoint_up_refuses_without_outputs(cfg, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "state_dir", lambda _c: tmp_path)  # no outputs.json
    monkeypatch.setattr(aws, "endpoint_up", lambda *a: pytest.fail("must not start an endpoint"))
    with pytest.raises(SystemExit, match="outputs.json"):
        cli.cmd_endpoint(cfg, types.SimpleNamespace(action="up", backend=None, all=False))


# ------------------------------------------------------------------ endpoint wait
def test_wait_detects_rollback_to_the_old_config(cfg, monkeypatch, clock):
    s = stubs(monkeypatch, "sagemaker")["sagemaker"]
    name = cfg.endpoint_name("unlimited-ocr")
    s.add_response("describe_endpoint", endpoint(name, "Updating", EndpointConfigName="old"))
    s.add_response("describe_endpoint", endpoint(name, EndpointConfigName="old", FailureReason="no capacity"))
    with s, pytest.raises(SystemExit, match="rolled back.*no capacity"):
        aws.endpoint_wait(cfg, "unlimited-ocr", expect_config="new")


def test_wait_fails_on_rolling_back(cfg, monkeypatch, clock):
    s = stubs(monkeypatch, "sagemaker")["sagemaker"]
    s.add_response("describe_endpoint", endpoint(cfg.endpoint_name("unlimited-ocr"), "RollingBack"))
    with s, pytest.raises(SystemExit, match="rolling back"):
        aws.endpoint_wait(cfg, "unlimited-ocr")


def test_wait_accepts_the_new_config(cfg, monkeypatch, clock):
    s = stubs(monkeypatch, "sagemaker")["sagemaker"]
    name = cfg.endpoint_name("unlimited-ocr")
    s.add_response("describe_endpoint", endpoint(name, EndpointConfigName="old"))  # update not visible yet
    s.add_response("describe_endpoint", endpoint(name, "Updating", EndpointConfigName="old"))
    s.add_response("describe_endpoint", endpoint(name, EndpointConfigName="new"))
    s.add_response("describe_endpoint", endpoint(name, EndpointConfigName="new"))  # endpoint_instance_type
    with s:
        aws.endpoint_wait(cfg, "unlimited-ocr", expect_config="new")
        s.assert_no_pending_responses()


def test_wait_times_out(cfg, monkeypatch, clock):
    cfg.raw["backends"]["unlimited-ocr"]["sagemaker"]["wait_timeout_s"] = 60
    s = stubs(monkeypatch, "sagemaker")["sagemaker"]
    for _ in range(4):
        s.add_response("describe_endpoint", endpoint(cfg.endpoint_name("unlimited-ocr"), "Creating"))
    with s, pytest.raises(SystemExit, match="not InService after 60 s"):
        aws.endpoint_wait(cfg, "unlimited-ocr")


# ------------------------------------------------------------------ endpoint down
def test_failed_delete_keeps_the_auto_delete_schedule(cfg, monkeypatch, clock):
    s = stubs(monkeypatch, "sagemaker", "scheduler")
    sm = s["sagemaker"]
    sm.add_client_error("delete_endpoint", "AccessDeniedException", "not allowed", 403)
    sm.add_response("describe_endpoint", endpoint(cfg.endpoint_name("unlimited-ocr")))
    with sm, s["scheduler"], pytest.raises(SystemExit, match="could not delete endpoint"):
        aws.endpoint_down(cfg, "unlimited-ocr")  # no delete_schedule stubbed: calling it would fail the test


def test_down_all_finds_orphans_keeps_going_and_fails(cfg, monkeypatch, clock, capsys):
    s = stubs(monkeypatch, "sagemaker", "scheduler")
    sm, sch = s["sagemaker"], s["scheduler"]
    p = cfg.prefix
    orphan, ours, other = endpoint(f"{p}-old"), endpoint(f"{p}-unlimited-ocr"), endpoint(f"{p}-x-y")
    listing = {"Endpoints": [orphan, ours, other]}

    def tags(e, stack):
        sm.add_response(
            "list_tags",
            {"Tags": [{"Key": "Stack", "Value": stack}] if stack else []},
            {"ResourceArn": e["EndpointArn"]},
        )

    sm.add_response("list_endpoints", listing, {"NameContains": f"{p}-"})
    tags(orphan, p)
    tags(ours, None)  # untagged: still ours by name
    tags(other, f"{p}-x")  # another stack whose prefix starts like ours
    # orphan: delete fails -> reported, the loop goes on
    sm.add_client_error(
        "delete_endpoint", "AccessDeniedException", "no", 403, expected_params={"EndpointName": f"{p}-old"}
    )
    sm.add_response("describe_endpoint", orphan)
    # configured one: deleted, then its schedule and configs
    sm.add_response("delete_endpoint", {}, {"EndpointName": f"{p}-unlimited-ocr"})
    sm.add_client_error("describe_endpoint", *NOT_FOUND)
    sch.add_response("delete_schedule", {}, {"Name": f"{p}-unlimited-ocr-auto-delete"})
    sm.add_response("list_endpoint_configs", {"EndpointConfigs": []}, {"NameContains": f"{p}-unlimited-ocr"})
    # final check: the orphan is still there
    sm.add_response("list_endpoints", {"Endpoints": [orphan]}, {"NameContains": f"{p}-"})
    tags(orphan, p)
    with sm, sch, pytest.raises(SystemExit, match=rf"1 of 2 failed \({p}-old\); still present: {p}-old"):
        aws.endpoint_down_all(cfg)
    sm.assert_no_pending_responses()
    sch.assert_no_pending_responses()
    assert f"FAILED {p}-old" in capsys.readouterr().out


# ------------------------------------------------------------------ Fargate + pricing
def test_task_sync_removes_files_a_stage_replaced(monkeypatch, tmp_path):
    """extract renames extractions.partial.jsonl at the end of a pass: S3 must not keep the stale copy."""
    (tmp_path / "extractions.jsonl").write_text("{}\n")
    s3 = boto3.client("s3", region_name="eu-west-1")
    monkeypatch.setattr(aws.boto3, "client", lambda *_a, **_k: s3)
    monkeypatch.setattr(s3, "upload_file", lambda *a, **k: None)
    listing = {
        "Contents": [
            {"Key": "runs/r/extractions.partial.jsonl", "Size": 3, "ETag": '"x"'},
        ]
    }
    with Stubber(s3) as st:
        st.add_response("list_objects_v2", listing, {"Bucket": "b", "Prefix": "runs/r/"})
        st.add_response(
            "delete_objects",
            {},
            {"Bucket": "b", "Delete": {"Objects": [{"Key": "runs/r/extractions.partial.jsonl"}], "Quiet": True}},
        )
        assert aws.s3_sync_up(tmp_path, "b", "runs/r", delete=True) == 1
        st.add_response("list_objects_v2", listing, {"Bucket": "b", "Prefix": "runs/r/"})
        assert aws.s3_sync_up(tmp_path, "b", "runs/r") == 1  # default: never deletes
        st.assert_no_pending_responses()


def test_describe_task_retries_until_listed(monkeypatch, clock):
    ecs = stubs(monkeypatch, "ecs")["ecs"]
    ecs.add_response("describe_tasks", {"tasks": []})
    ecs.add_response("describe_tasks", {"tasks": [{"taskArn": "arn:t/1", "lastStatus": "PENDING"}]})
    with ecs:
        assert aws._describe_task(ecs.client, "c", "arn:t/1")["lastStatus"] == "PENDING"


def test_pricing_reads_every_page(monkeypatch):
    pr = stubs(monkeypatch, "pricing", region="us-east-1")["pricing"]

    def product(component: str, usd: str) -> str:
        dim = {"d": {"pricePerUnit": {"USD": usd}}}
        return json.dumps(
            {
                "product": {"attributes": {"component": component}},
                "terms": {"OnDemand": {"t": {"priceDimensions": dim}}},
            }
        )

    pr.add_response(
        "get_products", {"PriceList": [product("Training", "9.0")], "NextToken": "n", "FormatVersion": "aws_v1"}
    )
    second = {"ServiceCode": "AmazonSageMaker", "Filters": ANY, "NextToken": "n"}
    pr.add_response("get_products", {"PriceList": [product("Hosting", "1.5")], "FormatVersion": "aws_v1"}, second)
    with pr:
        assert aws.sagemaker_hourly_usd("eu-west-1", "ml.g6.xlarge") == 1.5


# ------------------------------------------------------------------ weights
def test_manifest_is_current_only_for_the_same_revision():
    rev, other = "a" * 40, "b" * 40
    m = {"repo": "org/m", "revision": rev, "files": []}
    assert weights.is_current(m, "org/m", rev, ())
    assert not weights.is_current(m, "org/m", other, ())
    assert not weights.is_current(None, "org/m", rev, ())
    assert not weights.is_current({**m, "skip_prefixes": ["x/"]}, "org/m", rev, ())


def test_reupload_removes_files_of_the_old_snapshot(monkeypatch):
    s3 = boto3.client("s3", region_name="eu-west-1")
    keep = {"models/m/config.json", "models/m/model.safetensors"}
    objs = [{"Key": k, "Size": 1} for k in sorted(keep | {"models/m/old-shard.bin"})]
    with Stubber(s3) as st:
        st.add_response("list_objects_v2", {"Contents": objs}, {"Bucket": "b", "Prefix": "models/m/"})
        st.add_response(
            "delete_objects",
            {"Deleted": [{"Key": "models/m/old-shard.bin"}]},
            {"Bucket": "b", "Delete": {"Objects": [{"Key": "models/m/old-shard.bin"}], "Quiet": True}},
        )
        assert weights._remove_stale(s3, "b", "models/m", keep) == 1
        st.assert_no_pending_responses()


# ------------------------------------------------------------------ tf-vars + task definition drift
def test_tf_vars_keep_the_models_of_the_last_render(cfg, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "state_dir", lambda _c: tmp_path)
    names = ["a", "b"]
    assert cli._models_to_create(cfg, names, with_model=True) == names
    assert cli._models_to_create(cfg, names, with_model=False) == []  # first deploy: no tfvars yet
    (tmp_path / "terraform.tfvars.json").write_text(json.dumps({"create_selfhosted_models": ["a"]}))
    assert cli._models_to_create(cfg, names, with_model=False) == ["a"]  # b was just added: waits
    (tmp_path / "terraform.tfvars.json").write_text(
        json.dumps({"create_selfhosted_models": True, "selfhosted": {"a": {}, "b": {}}})  # older render
    )
    assert cli._models_to_create(cfg, names, with_model=False) == names


def test_run_refuses_when_the_task_definition_is_stale(cfg, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "state_dir", lambda _c: tmp_path)
    with pytest.raises(SystemExit, match="is missing"):
        cli._check_task_definition(cfg)
    deployed = {var: cfg["runner"][key] for var, key in cli._TASK_DEFINITION_VARS.items()}
    (tmp_path / "terraform.tfvars.json").write_text(json.dumps(deployed))
    cli._check_task_definition(cfg)
    cfg.raw["runner"]["max_minutes"] = 30
    with pytest.raises(SystemExit, match="runner.max_minutes: 30 in the config, 240 deployed"):
        cli._check_task_definition(cfg)


# ------------------------------------------------------------------ SIGTERM in the task
def test_sigterm_still_syncs_the_partial_run(cfg, monkeypatch):
    synced: list[str] = []
    monkeypatch.setattr(aws, "s3_sync_down", lambda *a: 0)
    monkeypatch.setattr(aws, "s3_sync_up", lambda *a, **k: synced.append("up") or 0)

    def stages(*_a, **_k):
        os.kill(os.getpid(), signal.SIGTERM)  # what `timeout` sends at runner.max_minutes
        pytest.fail("SIGTERM should have stopped the stages")

    monkeypatch.setattr(cli, "execute_stages", stages)
    prev = signal.getsignal(signal.SIGTERM)
    try:
        with pytest.raises(SystemExit) as e:
            cli.cmd_task_run(cfg, types.SimpleNamespace(run="r1", stages="score"))
    finally:
        signal.signal(signal.SIGTERM, prev)
    assert e.value.code == 128 + signal.SIGTERM and synced == ["up"]
