# ocr-bench task runner. Every recipe uses CONFIG (default config/example.yaml).
#
#   export CONFIG=config/mine.yaml
#   just tools          # terraform + crane into .tools/ (one-off)
#   just deploy         # infra, images, weights, SageMaker model (idempotent)
#   just endpoint-up    # start the GPU endpoint (~15 min; billed per hour from here)
#   just docs           # generate the synthetic documents
#   just run smoke      # stage 1 + 2 for plan "smoke" (Fargate or local per runner.mode)
#   just report smoke   # score -> results/smoke/report.md + site/smoke/index.html
#   just endpoint-down  # stop GPU billing
#   just destroy        # remove everything

set shell := ["bash", "-euo", "pipefail", "-c"]

export OCRBENCH_CONFIG := env("CONFIG", "config/example.yaml")
export PATH := justfile_directory() + "/.tools:" + env("PATH")

ob := "uv run ocrbench"
tf := "terraform -chdir=infra/terraform"
prefix := `uv run python -c "from ocrbench.config import load; print(load().prefix)" 2>/dev/null || echo unknown`
state := ".state/" + prefix

default:
    @just --list --unsorted

# ---- setup ----

# Download terraform + crane into .tools/ (macOS/Linux, arm64/amd64)
tools:
    scripts/get_tools.sh

# Install the Python package + dev tools and the public-repo git hooks
setup:
    uv sync --extra dev
    scripts/install_hooks.sh

test:
    uv run pytest -q

lint:
    uv run ruff check src tests && uv run ruff format --check src tests && {{tf}} fmt -check -recursive

# ---- infrastructure ----

# Terraform plan/apply with the current config. `just tf plan`, `just tf apply`.
tf *args: _tfinit
    {{tf}} {{args}} -var-file="$PWD/{{state}}/terraform.tfvars.json"

_tfinit with_model="":
    {{ob}} tf-vars {{with_model}}
    {{tf}} init -reconfigure -input=false >/dev/null

_outputs:
    {{tf}} output -json > {{state}}/outputs.json

# Full deploy: infra -> images -> weights -> SageMaker model. Safe to re-run.
deploy:
    just _tfinit
    {{tf}} apply -input=false -auto-approve -var-file="$PWD/{{state}}/terraform.tfvars.json"
    just _outputs
    just images
    just weights
    just _tfinit --with-model
    {{tf}} apply -input=false -auto-approve -var-file="$PWD/{{state}}/terraform.tfvars.json"
    just _outputs
    @echo "deployed. next: just endpoint-up && just docs && just run smoke"

# Build + push both images (ocr mirror is skipped if already present: `just images-force` to redo)
images:
    #!/usr/bin/env bash
    set -euo pipefail
    repo=$(python3 -c "import json;print(json.load(open('{{state}}/outputs.json'))['vllm_repository_url']['value'])")
    if crane digest "$repo:mirror" >/dev/null 2>&1; then {{ob}} images ocr --skip-mirror; else {{ob}} images ocr; fi
    {{ob}} images bench

images-force:
    {{ob}} images all

# Weights -> S3, skipped if the manifest is already there. Proxy blocks the HF CDN? `just weights ~/Downloads`
weights from_dir="":
    #!/usr/bin/env bash
    set -euo pipefail
    bucket=$(python3 -c "import json;print(json.load(open('{{state}}/outputs.json'))['bucket']['value'])")
    if [ -z "{{from_dir}}" ] && aws s3 ls "s3://$bucket/models/ocr.manifest.json" >/dev/null 2>&1; then
      echo "weights already in s3://$bucket/models/ocr/"; exit 0
    fi
    {{ob}} upload-weights {{ if from_dir != "" { "--from-dir " + from_dir } else { "" } }}

# ---- GPU endpoint ----

endpoint-up:
    {{ob}} endpoint up

endpoint-down:
    {{ob}} endpoint down

endpoint-status:
    {{ob}} endpoint status

endpoint-logs:
    aws logs tail "/aws/sagemaker/Endpoints/{{prefix}}-ocr" --since 30m

# ---- benchmark ----

docs per_type="":
    {{ob}} docs {{ if per_type != "" { "--per-type " + per_type } else { "" } }}

# Run a plan from bench.plans (stage 1) and the extraction pipelines (stage 2)
run plan tag=plan:
    {{ob}} run --plan {{plan}} --tag {{tag}}

fetch tag:
    {{ob}} fetch --tag {{tag}}

report tag:
    {{ob}} report --tag {{tag}}

# Open the static viewer
open tag:
    open "site/{{tag}}/index.html" 2>/dev/null || xdg-open "site/{{tag}}/index.html"

# ---- teardown ----

[confirm("Destroy the whole ocr-bench stack for this config (endpoint, bucket incl. weights/results, ECR, IAM, network)? [y/N]")]
destroy:
    -{{ob}} endpoint down
    just _tfinit --with-model
    {{tf}} destroy -input=false -auto-approve -var-file="$PWD/{{state}}/terraform.tfvars.json"
