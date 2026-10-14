"""run.json: schema upgrades, what gets recorded, re-summarising, run order."""

import json

import yaml

from ocrbench import config as config_mod
from ocrbench import prompts, runs
from ocrbench.config import ROOT

V1 = {
    "id": "r",
    "schema_version": 1,
    "ocr": {"hf_repo": "a/B", "served_model_name": "a/B", "prompt": "<image>p", "price_per_hour_usd": 1.5},
    "llm_backends": {"s": {"provider": "bedrock", "model": "m", "price_per_mtok": None}},
}


def test_upgrade_is_idempotent_and_stamps_the_version():
    m = runs.upgrade(V1)
    assert m["schema_version"] == runs.SCHEMA_VERSION and set(m["backends"]) == {"ocr", "s"}
    assert runs.upgrade(m) == m  # twice: backends survive
    assert V1["schema_version"] == 1 and "ocr" in V1  # input untouched
    m["prompts"]["transcribe"]["max_tokens"] = -1
    assert prompts.DEFAULTS["transcribe"]["max_tokens"] != -1  # a copy, not the shared defaults


def test_future_schema_bump_keeps_v2_runs(monkeypatch):
    monkeypatch.setattr(runs, "SCHEMA_VERSION", 3)
    v2 = {"schema_version": 2, "id": "r", "backends": {"x": {"kind": "bedrock"}}, "prompts": {"p": 1}}
    m = runs.upgrade(v2)
    assert m["schema_version"] == 3 and m["backends"] == v2["backends"] and m["prompts"] == {"p": 1}


def test_published_ids_carry_no_account():
    acct = "123456789012"
    prof = f"arn:aws:bedrock:eu-west-1:{acct}:application-inference-profile/abcd1234efgh"
    out = runs.public_model_id(prof)
    assert acct not in out and "abcd1234efgh" not in out and out.startswith("application-inference-profile/")
    assert out != runs.public_model_id(prof.replace("abcd", "zzzz"))  # still distinct per resource
    assert runs.public_model_id(f"arn:aws:bedrock:eu-west-1:{acct}:inference-profile/eu.anthropic.x-v1:0") == (
        "eu.anthropic.x-v1:0"
    )
    assert runs.public_model_id("arn:aws:bedrock:us-east-1::foundation-model/anthropic.y") == "anthropic.y"
    assert runs.public_model_id("eu.anthropic.z") == "eu.anthropic.z"
    s = runs.backend_summary({"kind": "bedrock", "model_id": prof})
    assert acct not in json.dumps(s)

    ecr = f"{acct}.dkr.ecr.eu-west-1.amazonaws.com/mirror/vllm-openai:v1"
    assert runs.public_image(ecr) == "<private-registry>/vllm-openai:v1"
    assert runs.public_image("registry.corp.example:5000/vllm:v1") == "<private-registry>/vllm:v1"
    digest = "sha256:" + "a" * 64
    org = f"registry.corp.example/some-org/team/vllm-openai@{digest}"
    assert runs.public_image(org) == f"<private-registry>/vllm-openai@{digest}"  # no org/team path
    assert runs.public_image("localhost:5000/org/x:1") == "<private-registry>/x:1"
    for public in (
        "docker.io/vllm/vllm-openai:v0.11",
        "vllm/vllm-openai:v0.11",
        "ghcr.io/a/b:1",
        "public.ecr.aws/x/y",
        f"nvcr.io/nvidia/sub/img@{digest}",
    ):
        assert runs.public_image(public) == public


def _cfg(tmp_path, monkeypatch):
    cfg = config_mod.load(ROOT / "config" / "example.yaml")
    cfg.raw["backends"]["unused"] = {"kind": "bedrock", "model_id": "m-unused"}
    monkeypatch.setattr(runs, "RUNS", tmp_path / "runs")
    return cfg


def test_only_used_backends_are_recorded(fake_docs, tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    meta = runs.load(runs.create(cfg, "smoke", fake_docs))
    # smoke transcribes with unlimited-ocr and sonnet-4.5; sonnet-4.5 also extracts and writes the summary
    assert set(meta["backends"]) == {"unlimited-ocr", "sonnet-4.5"}


def test_refresh_summary_without_snapshot_and_without_touching_stage_backends(fake_docs, tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    d = runs.create(cfg, "smoke", fake_docs)
    before = runs.load(d)["backends"]["sonnet-4.5"]
    # the writer is a backend stages 1-3 used, now configured differently
    cfg.raw["backends"]["sonnet-4.5"] = {**cfg.backend("sonnet-4.5"), "temperature": 1, "price_per_mtok": None}
    (d / "config.yaml").unlink()  # fresh clone: the snapshot is gitignored
    runs.refresh_summary_setup(cfg, d)
    meta = runs.load(d)
    assert meta["backends"]["sonnet-4.5"] == before  # stage 1-3 prices and settings unchanged
    assert meta["summary"]["agent"] == "sonnet-4.5" and meta["summary"]["backend"]["temperature"] == 1
    assert not (d / "config.yaml").exists()


def test_refresh_summary_snapshot_keeps_stage_backend(fake_docs, tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    d = runs.create(cfg, "smoke", fake_docs)
    snap0 = yaml.safe_load((d / "config.yaml").read_text())
    cfg.raw["backends"]["sonnet-4.5"] = {**cfg.backend("sonnet-4.5"), "temperature": 1}
    runs.refresh_summary_setup(cfg, d)
    snap = yaml.safe_load((d / "config.yaml").read_text())
    assert snap["backends"]["sonnet-4.5"] == snap0["backends"]["sonnet-4.5"]
    alias = snap["bench"]["summary"]["agent"]
    assert alias != "sonnet-4.5" and snap["backends"][alias]["temperature"] == 1
    assert runs.load(d)["summary"]["config_key"] == alias

    # a writer not used in stages 1-3 is recorded under its own key, as before
    cfg.raw["bench"]["summary"] = {"agent": "unused"}
    runs.refresh_summary_setup(cfg, d)
    meta = runs.load(d)
    assert meta["summary"] == {"agent": "unused", "temperature": 0.2}
    assert meta["backends"]["unused"]["model"] == "m-unused"
    assert yaml.safe_load((d / "config.yaml").read_text())["bench"]["summary"]["agent"] == "unused"


def test_summary_records_the_temperature_it_is_sent_with(fake_docs, tmp_path, monkeypatch):
    """Without a backend override the request uses the summarize prompt's temperature, not the 0 that
    backend_summary records for stages 1-2."""
    cfg = _cfg(tmp_path, monkeypatch)
    assert "temperature" not in cfg.backend("sonnet-4.5")
    d = runs.create(cfg, "smoke", fake_docs)
    meta = runs.load(d)
    assert meta["summary"]["temperature"] == prompts.DEFAULTS["summarize"]["temperature"] == 0.2
    assert meta["backends"]["sonnet-4.5"]["temperature"] == 0.0  # what stages 1-2 sent
    cfg.raw["bench"]["prompts"] = {"summarize": {"temperature": 0.5}}
    runs.refresh_summary_setup(cfg, d)
    assert runs.load(d)["summary"]["temperature"] == 0.5
    cfg.raw["backends"]["sonnet-4.5"]["temperature"] = 1  # override wins
    runs.refresh_summary_setup(cfg, d)
    assert runs.load(d)["summary"]["temperature"] == 1
    cfg.raw["backends"]["sonnet-4.5"]["temperature"] = None  # not sent: the model's default
    runs.refresh_summary_setup(cfg, d)
    assert runs.load(d)["summary"]["temperature"] is None
    sh = cfg.backend("unlimited-ocr")
    assert runs.summary_temperature(sh, {"temperature": 0.2}) == sh["request"]["temperature"]


def test_all_runs_newest_first_by_created(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "RUNS", tmp_path)
    for rid, created in (("2026-10-09-p-9", "2026-10-09T10:00:00"), ("2026-10-09-p-10", "2026-10-09T11:00:00")):
        (tmp_path / rid).mkdir()
        runs.save(tmp_path / rid, {"schema_version": runs.SCHEMA_VERSION, "id": rid, "created": created})
    assert [m["id"] for m in runs.all_runs()] == ["2026-10-09-p-10", "2026-10-09-p-9"]
