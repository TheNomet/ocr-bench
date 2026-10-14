"""aws.schedule_auto_delete: the one-off EventBridge Scheduler job that deletes a GPU endpoint."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import boto3
import pytest
from botocore.stub import ANY, Stubber

from ocrbench import aws
from ocrbench.config import SAGEMAKER_DEFAULTS

ROLE = "arn:aws:iam::000000000000:role/sched"


class FakeCfg:
    """Just what schedule_auto_delete reads from a Config."""

    region = "eu-west-1"

    def __init__(self, sagemaker: dict | None = None):
        self._spec = {"kind": "selfhosted", "sagemaker": sagemaker or {}}

    def backend(self, name: str) -> dict:
        return self._spec

    def sagemaker(self, name: str) -> dict:
        return {**SAGEMAKER_DEFAULTS, **self._spec["sagemaker"]}

    def endpoint_name(self, name: str) -> str:
        return f"bench-{name}"


@pytest.fixture
def scheduler(monkeypatch):
    """Every boto3.client("scheduler") is one offline client behind a Stubber; .made counts factory calls."""
    for k in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_CONFIG_FILE", "/nonexistent")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/nonexistent")
    client = boto3.session.Session().client("scheduler", region_name="eu-west-1")
    made: list[str] = []

    def factory(service, *a, **kw):
        assert service == "scheduler", service
        made.append(kw.get("region_name"))
        return client

    monkeypatch.setattr(boto3, "client", factory)
    with Stubber(client) as stub:
        stub.made = made  # pyright: ignore[reportAttributeAccessIssue]
        yield stub
        stub.assert_no_pending_responses()


def expected() -> dict:
    return {
        "Name": "bench-ocr-auto-delete",
        "ScheduleExpression": ANY,
        "ScheduleExpressionTimezone": "UTC",
        "FlexibleTimeWindow": {"Mode": "OFF"},
        "ActionAfterCompletion": "DELETE",
        "Target": {
            "Arn": "arn:aws:scheduler:::aws-sdk:sagemaker:deleteEndpoint",
            "RoleArn": ROLE,
            "Input": json.dumps({"EndpointName": "bench-ocr"}),
        },
    }


def capture(stub, operation: str) -> list[dict]:
    """Records the parameters of every call to `operation` (e.g. CreateSchedule); the Stubber only matches them."""
    seen: list[dict] = []
    stub.client.meta.events.register_first(
        f"provide-client-params.scheduler.{operation}", lambda params, **_: seen.append(dict(params))
    )
    return seen


def scheduled_at(params: dict) -> datetime:
    expr = params["ScheduleExpression"]
    assert expr.startswith("at(") and expr.endswith(")"), expr
    return datetime.strptime(expr[3:-1], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)


def test_creates_schedule(scheduler, capsys):
    seen = capture(scheduler, "CreateSchedule")
    scheduler.add_response(
        "create_schedule", {"ScheduleArn": "arn:aws:scheduler:eu-west-1:000000000000:schedule/x"}, expected()
    )
    before = datetime.now(UTC).replace(microsecond=0)
    aws.schedule_auto_delete(FakeCfg({"max_lifetime_hours": 2}), "ocr", ROLE)  # pyright: ignore[reportArgumentType]
    after = datetime.now(UTC)
    assert scheduler.made == ["eu-west-1"]
    at = scheduled_at(seen[0])
    assert before + timedelta(hours=2) <= at <= after + timedelta(hours=2)
    assert "auto-delete scheduled" in capsys.readouterr().out


def test_default_lifetime_is_8_hours(scheduler):
    seen = capture(scheduler, "CreateSchedule")
    scheduler.add_response("create_schedule", {"ScheduleArn": "arn:x"}, expected())
    aws.schedule_auto_delete(FakeCfg(), "ocr", ROLE)  # pyright: ignore[reportArgumentType]
    delta = scheduled_at(seen[0]) - datetime.now(UTC)
    assert timedelta(hours=7, minutes=59) < delta <= timedelta(hours=8)


def test_conflict_updates_existing_schedule(scheduler, capsys):
    scheduler.add_client_error("create_schedule", service_error_code="ConflictException", http_status_code=409)
    scheduler.add_response("update_schedule", {"ScheduleArn": "arn:x"}, expected())
    aws.schedule_auto_delete(FakeCfg({"max_lifetime_hours": 1}), "ocr", ROLE)  # pyright: ignore[reportArgumentType]
    out = capsys.readouterr().out
    assert "auto-delete scheduled" in out and "WARNING" not in out


@pytest.mark.parametrize("hours", [0, 0.0])
def test_zero_lifetime_cancels_any_older_schedule(scheduler, capsys, hours):
    scheduler.add_client_error("delete_schedule", service_error_code="ResourceNotFoundException")
    aws.schedule_auto_delete(FakeCfg({"max_lifetime_hours": hours}), "ocr", ROLE)  # pyright: ignore[reportArgumentType]
    assert "no auto-delete" in capsys.readouterr().out


def test_refuses_without_role(scheduler):
    """Fail closed: no role (e.g. no outputs.json) means no endpoint, not an endpoint without its safety net."""
    with pytest.raises(SystemExit, match="no scheduler role"):
        aws.schedule_auto_delete(FakeCfg({"max_lifetime_hours": 4}), "ocr", None)  # pyright: ignore[reportArgumentType]
    assert scheduler.made == []


def test_other_errors_refuse(scheduler):
    scheduler.add_client_error("create_schedule", service_error_code="AccessDeniedException", http_status_code=403)
    with pytest.raises(SystemExit, match="could not schedule.*AccessDeniedException"):
        aws.schedule_auto_delete(FakeCfg({"max_lifetime_hours": 1}), "ocr", ROLE)  # pyright: ignore[reportArgumentType]
