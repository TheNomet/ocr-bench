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
    def region(self) -> str:
        return self.raw["aws"]["region"]

    @property
    def bucket(self) -> str:
        return f"{self.prefix}-artifacts"

    @property
    def endpoint_name(self) -> str:
        return f"{self.prefix}-ocr"

    @property
    def model_name(self) -> str:
        return f"{self.prefix}-ocr-model"

    @property
    def ocr_repo(self) -> str:
        return f"{self.prefix}-vllm"

    @property
    def bench_repo(self) -> str:
        return f"{self.prefix}-bench"

    @property
    def registry(self) -> str:
        return f"{self.raw['aws']['account_id']}.dkr.ecr.{self.region}.amazonaws.com"

    @property
    def model_prefix(self) -> str:
        return "models/ocr"

    def llm(self, name: str) -> dict:
        try:
            return self.raw["llm_backends"][name]
        except KeyError:
            raise KeyError(f"llm backend {name!r} not defined under llm_backends in {self.path}") from None

    def plan(self, name: str) -> list[dict]:
        try:
            return self.raw["bench"]["plans"][name]
        except KeyError:
            raise KeyError(f"plan {name!r} not defined under bench.plans in {self.path}") from None


DEFAULTS: dict = {
    "aws": {"profile": None, "permissions_boundary_arn": None, "tags": {}},
    "terraform_state": {"bucket": None, "key": "ocr-bench/terraform.tfstate", "dynamodb_table": None},
    "network": {"mode": "create", "existing": {}, "create": {"cidr": "10.42.0.0/24"}},
    "ecs": {"cluster": "create"},
    "llm_backends": {},
    "runner": {
        "mode": "fargate",
        "cpu": 2048,
        "memory": 4096,
        "base_image": "docker.io/library/python:3.12-slim",
        "extra_ca_pem": None,
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load(path: str | Path | None = None) -> Config:
    p = Path(path or os.environ.get("OCRBENCH_CONFIG") or ROOT / "config" / "example.yaml")
    raw = _merge(DEFAULTS, _expand(yaml.safe_load(p.read_text()) or {}))
    for key in ("name_prefix", "aws", "ocr", "bench"):
        if key not in raw:
            raise ValueError(f"{p}: missing top-level key {key!r}")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,30}", raw["name_prefix"]):
        raise ValueError(f"{p}: name_prefix must be 3-31 chars of [a-z0-9-] (it names S3 buckets and ECR repos)")
    in_aws_runtime = any(os.environ.get(k) for k in ("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "AWS_EXECUTION_ENV"))
    if raw["aws"].get("profile") and not in_aws_runtime:
        os.environ.setdefault("AWS_PROFILE", raw["aws"]["profile"])
    os.environ.setdefault("AWS_REGION", raw["aws"]["region"])
    return Config(raw, p)
