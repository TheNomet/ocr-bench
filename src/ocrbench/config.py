"""Load config/<name>.yaml: env-var expansion, defaults, derived names.

The same file drives the CLI and Terraform (`ocrbench tf-vars` renders the Terraform
variables from it), so there is exactly one place to change an account, region or endpoint.
"""

from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
ROOT = Path(__file__).resolve().parents[2]


def _expand(v: Any) -> Any:
    if isinstance(v, str):

        def sub(m: re.Match) -> str:
            val = os.environ.get(m.group(1))
            if val is None:
                if m.group(2) is None:
                    raise KeyError(f"config references ${{{m.group(1)}}} but it is not set")
                return m.group(2)
            return val

        return _ENV.sub(sub, v)
    if isinstance(v, dict):
        return {k: _expand(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_expand(x) for x in v]
    return v


# Page image variants a plan item (or `ocrbench transcribe --variant`) can ask for.
VARIANTS = ("clean", "degraded", "both")

# backends.<name>.sagemaker defaults (endpoint up/wait, tf-vars).
SAGEMAKER_DEFAULTS: dict = {
    "max_lifetime_hours": 8,
    "container_startup_timeout_s": 1800,
    "model_download_timeout_s": 1800,
    "wait_timeout_s": 5400,
    "network_isolation": True,
}

# Endpoint configs are named <endpoint>-<timestamp> (aws.endpoint_up); the longest SageMaker name we create.
CONFIG_STAMP = "%Y%m%d%H%M%S"


def endpoint_config_name(endpoint: str, at: datetime) -> str:
    return f"{endpoint}-{at:{CONFIG_STAMP}}"


@dataclass
class Config:
    raw: dict
    path: Path

    def __getitem__(self, key: str) -> Any:
        return self.raw[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)

    # ---- derived names (keep in sync with the locals in infra/terraform/main.tf) ----
    @property
    def prefix(self) -> str:
        return self.raw["name_prefix"]

    @property
    def state_dir(self) -> Path:
        """Local, gitignored deployment state: .state/<prefix>/ (tfvars, outputs, pinned image refs)."""
        d = ROOT / ".state" / self.prefix
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def region(self) -> str:
        return self.raw["aws"]["region"]

    @property
    def bucket(self) -> str:
        return f"{self.prefix}-artifacts"

    @property
    def selfhosted_repo(self) -> str:
        """One ECR repo for every self-hosted image; tags are per backend (<name>-sagemaker)."""
        return f"{self.prefix}-vllm"

    @property
    def bench_repo(self) -> str:
        return f"{self.prefix}-bench"

    @property
    def registry(self) -> str:
        return f"{self.raw['aws']['account_id']}.dkr.ecr.{self.region}.amazonaws.com"

    # ---- per self-hosted backend ----
    def endpoint_name(self, name: str) -> str:
        return f"{self.prefix}-{name}"

    def model_name(self, name: str) -> str:
        return f"{self.prefix}-{name}-model"

    def model_prefix(self, name: str) -> str:
        return f"models/{name}"

    def sagemaker(self, name: str) -> dict:
        """backends.<name>.sagemaker with SAGEMAKER_DEFAULTS filled in."""
        return {**SAGEMAKER_DEFAULTS, **(self.backend(name).get("sagemaker") or {})}

    # ---- backends ----
    @property
    def backends(self) -> dict[str, dict]:
        return self.raw["backends"]

    def backend(self, name: str) -> dict:
        try:
            return self.raw["backends"][name]
        except KeyError:
            raise KeyError(f"backend {name!r} not defined under backends in {self.path}") from None

    def kind(self, name: str) -> str:
        return self.backend(name)["kind"]

    def selfhosted(self) -> list[str]:
        return [k for k, v in self.raw["backends"].items() if v["kind"] == "selfhosted"]

    def one_selfhosted(self, name: str | None = None) -> str:
        """`name` if given (and self-hosted), else the only self-hosted backend in the config."""
        sh = self.selfhosted()
        if name:
            if name not in sh:
                raise SystemExit(f"{name!r} is not a self-hosted backend (self-hosted: {', '.join(sh) or 'none'})")
            return name
        if len(sh) != 1:
            raise SystemExit(
                f"several self-hosted backends ({', '.join(sh)}): name one"
                if sh
                else "no self-hosted backend in the config"
            )
        return sh[0]

    # ---- plans ----
    def plan(self, name: str) -> list[dict]:
        """The plan's transcription items. Raises KeyError naming the defined plans if there is no such plan."""
        plans = self.raw["bench"].get("plans") or {}
        if name not in plans:
            raise KeyError(
                f"plan {name!r} not defined under bench.plans in {self.path} (defined: {', '.join(plans) or 'none'})"
            )
        return plans[name]

    def plan_description(self, name: str) -> str:
        return (self.raw["bench"].get("plan_descriptions") or {}).get(name) or ""

    def plan_report(self, name: str) -> dict:
        """How run pages of this plan are shown (bench.plan_report.<plan>), e.g. {"hide_backends": [...]}."""
        return (self.raw["bench"].get("plan_report") or {}).get(name) or {}

    @property
    def summary_agent(self) -> str:
        """Backend that writes the recommendation: bench.summary.agent, else the extraction agent."""
        return (self.raw["bench"].get("summary") or {}).get("agent") or self.raw["bench"]["extraction"]["agent"]


DEFAULTS: dict = {
    "aws": {"profile": None, "permissions_boundary_arn": None, "tags": {}},
    "terraform_state": {"bucket": None, "key": "ocr-bench/terraform.tfstate", "dynamodb_table": None},
    "network": {"mode": "create", "existing": {}, "create": {"cidr": "10.42.0.0/24"}},
    "ecs": {"cluster": "create"},
    "backends": {},
    "runner": {
        "mode": "fargate",
        "cpu": 2048,
        "memory": 4096,
        "max_minutes": 240,
        "stop_grace_seconds": 120,
        "assign_public_ip": False,
        "base_image": "docker.io/library/python:3.12-slim",
        "extra_ca_pem": None,
    },
    "retention": {"runs_days": 60, "log_days": 14, "ecr_untagged_days": 14, "ecr_keep_mirrors": 2},
    "bench": {
        "max_retries": 4,
        "extraction": {"concurrency": 8},
        "docs": {"per_type": 6, "seed": 7, "scale": 2.0},
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


KINDS = ("selfhosted", "bedrock", "openai_compatible")
_SELFHOSTED_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,30}")
_SAGEMAKER_NAME_MAX = 63
# retention.log_days: the values CloudWatch Logs accepts (0, "never expire", is left out on purpose)
LOG_RETENTION_DAYS = (
    1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653,
)  # fmt: skip


def _is_num(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def upgrade(raw: dict) -> dict:
    """Configs written before `backends:` existed: `ocr:` (one self-hosted model, recorded as "ocr") and
    `llm_backends:` (kind = provider). Returns the same config in the current shape."""
    if "ocr" not in raw and "llm_backends" not in raw:
        return raw
    raw = copy.deepcopy(raw)
    backends: dict = {}
    if "ocr" in raw:
        o = raw.pop("ocr")
        o.pop("name", None)  # never used: results always said "ocr"
        strip = o.pop("strip_grounding_tokens", True)
        backends["ocr"] = {"kind": "selfhosted", "postprocess": ["strip_grounding"] if strip else [], **o}
    for k, v in (raw.pop("llm_backends", None) or {}).items():
        backends[k] = {"kind": v.get("provider"), **{x: y for x, y in v.items() if x != "provider"}}
    raw["backends"] = {**backends, **(raw.get("backends") or {})}
    (raw.get("bench") or {}).pop("transcribers", None)
    return raw


def validate(raw: dict, p: Path) -> None:
    from .postprocess import REGISTRY

    if not raw["backends"]:
        raise ValueError(f"{p}: no backends defined")
    for k, v in raw["backends"].items():
        if v.get("kind") not in KINDS:
            raise ValueError(f"{p}: backends.{k}.kind must be one of {', '.join(KINDS)}, got {v.get('kind')!r}")
        for name in v.get("postprocess") or []:
            if name not in REGISTRY:
                raise ValueError(f"{p}: backends.{k}.postprocess: unknown step {name!r} ({', '.join(REGISTRY)})")
        if v["kind"] == "bedrock" and v.get("reasoning_effort"):
            raise ValueError(
                f"{p}: backends.{k}.reasoning_effort is not supported for kind bedrock (only openai_compatible)"
            )
        if v["kind"] == "selfhosted":
            if not _SELFHOSTED_NAME.fullmatch(k):
                raise ValueError(
                    f"{p}: self-hosted backend name {k!r} must be 1-31 chars of [a-z0-9-] (it names AWS resources)"
                )
            # the longest SageMaker name per backend is its endpoint config: <prefix>-<name>-<14-digit stamp>
            longest = endpoint_config_name(f"{raw['name_prefix']}-{k}", datetime(2000, 1, 1))
            if len(longest) > _SAGEMAKER_NAME_MAX:
                raise ValueError(
                    f"{p}: name_prefix + backend name {k!r} too long for SageMaker: endpoint config "
                    f"{longest!r} is {len(longest)} chars (max {_SAGEMAKER_NAME_MAX})"
                )
            sm = {**SAGEMAKER_DEFAULTS, **(v.get("sagemaker") or {})}
            hours = sm["max_lifetime_hours"]
            if not _is_num(hours) or hours < 0:
                raise ValueError(f"{p}: backends.{k}.sagemaker.max_lifetime_hours must be a number >= 0, got {hours!r}")
            for key in ("container_startup_timeout_s", "model_download_timeout_s", "wait_timeout_s"):
                if not _is_num(sm[key]) or sm[key] <= 0:
                    raise ValueError(f"{p}: backends.{k}.sagemaker.{key} must be a number > 0, got {sm[key]!r}")
    run = raw["runner"]
    for key in ("cpu", "memory", "max_minutes", "stop_grace_seconds"):
        # max_minutes feeds GNU `timeout`, where 0 means no limit at all
        if not _is_int(run.get(key)) or run[key] <= 0:
            raise ValueError(f"{p}: runner.{key} must be an integer > 0, got {run.get(key)!r}")
    if not isinstance(run.get("assign_public_ip"), bool):
        raise ValueError(f"{p}: runner.assign_public_ip must be true or false, got {run.get('assign_public_ip')!r}")
    ret = raw.get("retention") or {}
    for key, low in (("runs_days", 0), ("ecr_untagged_days", 1), ("ecr_keep_mirrors", 1)):
        if not _is_int(ret.get(key)) or ret[key] < low:
            raise ValueError(f"{p}: retention.{key} must be an integer >= {low}, got {ret.get(key)!r}")
    if not _is_int(ret.get("log_days")) or ret["log_days"] not in LOG_RETENTION_DAYS:
        raise ValueError(
            f"{p}: retention.log_days must be one of CloudWatch's {', '.join(map(str, LOG_RETENTION_DAYS))}, "
            f"got {ret.get('log_days')!r}"
        )
    ex = raw["bench"].get("extraction") or {}
    for role, key in (
        ("bench.extraction.agent", ex.get("agent")),
        ("bench.summary.agent", (raw["bench"].get("summary") or {}).get("agent")),
    ):
        if key and key not in raw["backends"]:
            raise ValueError(f"{p}: {role} {key!r} is not a backend")
    for plan, items in (raw["bench"].get("plans") or {}).items():
        for it in items:
            if it["backend"] not in raw["backends"]:
                raise ValueError(f"{p}: bench.plans.{plan}: backend {it['backend']!r} is not defined under backends")
            if it.get("variant", "both") not in VARIANTS:
                raise ValueError(
                    f"{p}: bench.plans.{plan}: variant {it['variant']!r} must be one of {', '.join(VARIANTS)}"
                )


def load(path: str | Path | None = None) -> Config:
    p = Path(path or os.environ.get("OCRBENCH_CONFIG") or os.environ.get("CONFIG") or ROOT / "config" / "example.yaml")
    user = upgrade(_expand(yaml.safe_load(p.read_text()) or {}))
    for key in ("name_prefix", "aws", "bench"):  # checked before defaults fill in parts of them
        if key not in user:
            raise ValueError(f"{p}: missing top-level key {key!r}")
    raw = _merge(DEFAULTS, user)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,30}", raw["name_prefix"]):
        raise ValueError(f"{p}: name_prefix must be 3-31 chars of [a-z0-9-] (it names S3 buckets and ECR repos)")
    validate(raw, p)
    in_aws_runtime = any(os.environ.get(k) for k in ("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "AWS_EXECUTION_ENV"))
    if raw["aws"].get("profile") and not in_aws_runtime:
        os.environ.setdefault("AWS_PROFILE", raw["aws"]["profile"])
    os.environ.setdefault("AWS_REGION", raw["aws"]["region"])
    return Config(raw, p)
