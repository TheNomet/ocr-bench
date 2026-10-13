"""Endpoint lifecycle against stubbed AWS clients: no network, no credentials."""

from datetime import datetime
from pathlib import Path

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from ocrbench import aws
from ocrbench import config as config_mod

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(k, "testing")
    c = config_mod.load(ROOT / "config" / "example.yaml")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    return c


def _clients(monkeypatch, region: str) -> dict:
    made = {s: boto3.client(s, region_name=region) for s in ("sagemaker", "scheduler")}
    monkeypatch.setattr(aws.boto3, "client", lambda service, **_: made[service])
    return made


def _conf(name: str) -> dict:
    return {"EndpointConfigName": name, "EndpointConfigArn": f"arn:x:{name}", "CreationTime": datetime(2026, 1, 1)}


def test_config_name_matches_only_its_own_endpoint():
    name = "pre-ocr"
    own = aws.endpoint_config_name(name, datetime(2026, 10, 13, 9, 5, 7))
    assert own == "pre-ocr-20261013090507" and aws.is_endpoint_config_of(name, own)
    for other in ("pre-ocr-v2-20261013090507", "pre-ocr-v2", "xpre-ocr-20261013090507", "pre-ocr-2026"):
        assert not aws.is_endpoint_config_of(name, other), other


def test_endpoint_down_keeps_other_backends_configs(cfg, monkeypatch, capsys):
    c = _clients(monkeypatch, cfg.region)
    name = cfg.endpoint_name("unlimited-ocr")
    ours = aws.endpoint_config_name(name, datetime(2026, 10, 13, 9, 0, 0))
    sm, sch = Stubber(c["sagemaker"]), Stubber(c["scheduler"])
    sm.add_response("delete_endpoint", {}, {"EndpointName": name})
    sm.add_client_error("describe_endpoint", "ValidationException", "Could not find endpoint")
    sch.add_client_error("delete_schedule", "ResourceNotFoundException", "gone")  # already fired: fine
    sm.add_response(
        "list_endpoint_configs",
        {"EndpointConfigs": [_conf(ours), _conf(f"{name}-v2-20261013090000"), _conf(f"{name}-v2")]},
        {"NameContains": name},
    )
    sm.add_response("delete_endpoint_config", {}, {"EndpointConfigName": ours})  # only this one
    with sm, sch:
        aws.endpoint_down(cfg, "unlimited-ocr")
        sm.assert_no_pending_responses()
        sch.assert_no_pending_responses()
    assert f"deleted config {ours}" in capsys.readouterr().out


def test_cancel_auto_delete_only_ignores_not_found(cfg, monkeypatch):
    c = _clients(monkeypatch, cfg.region)
    with Stubber(c["scheduler"]) as sch:
        sch.add_client_error("delete_schedule", "ResourceNotFoundException", "gone")
        aws.cancel_auto_delete(cfg, "unlimited-ocr")
        sch.add_client_error("delete_schedule", "AccessDeniedException", "no")
        with pytest.raises(ClientError):
            aws.cancel_auto_delete(cfg, "unlimited-ocr")


def test_endpoint_info_is_empty_without_an_endpoint(cfg, monkeypatch):
    c = _clients(monkeypatch, cfg.region)
    with Stubber(c["sagemaker"]) as sm:
        sm.add_client_error("describe_endpoint", "ValidationException", "Could not find endpoint")
        assert aws.endpoint_info(cfg, "unlimited-ocr") == {}
        sm.add_client_error("describe_endpoint", "ValidationException", "Could not find endpoint")
        assert aws.endpoint_instance_type(cfg, "unlimited-ocr") is None
