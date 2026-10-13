"""Load config/<name>.yaml: env-var expansion, defaults, derived names.

The same file drives the CLI and Terraform (`ocrbench tf-vars` renders the Terraform
variables from it), so there is exactly one place to change an account, region or endpoint.
"""

from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass
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


@dataclass
class Config:
    raw: dict
    path: Path

    def __getitem__(self, key: str) -> Any:
        return self.raw[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)

    # ---- derived names (keep in sync with infra/terraform/locals.tf) ----
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
        "base_image": "docker.io/library/python:3.12-slim",
        "extra_ca_pem": None,
    },
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
        if v["kind"] == "selfhosted":
            if not _SELFHOSTED_NAME.fullmatch(k):
                raise ValueError(
                    f"{p}: self-hosted backend name {k!r} must be 1-31 chars of [a-z0-9-] (it names AWS resources)"
                )
            if len(f"{raw['name_prefix']}-{k}-model") > 63:
                raise ValueError(f"{p}: name_prefix + backend name {k!r} too long for SageMaker (63 chars)")
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
