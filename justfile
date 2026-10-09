# ocr-bench task runner. Every recipe uses CONFIG (default config/example.yaml).
#
#   export CONFIG=config/mine.yaml
#   just tools          # terraform + crane into .tools/ (one-off)
#   just deploy         # infra, images, weights, SageMaker model (idempotent)
#   just endpoint-up    # start the GPU endpoint (~15 min; billed per hour from here)
#   just docs           # generate the synthetic documents
#   just run smoke      # new run folder runs/<date>-smoke/: transcribe, extract, score, summarize
#   just open           # experiments index -> drill down per run and per document
#   just endpoint-down  # stop GPU billing
#   just destroy        # remove everything

set shell := ["bash", "-euo", "pipefail", "-c"]

export OCRBENCH_CONFIG := env("CONFIG", "config/example.yaml")
export PATH := justfile_directory() + "/.tools:" + env("PATH")

ob := "uv run ocrbench"
tf := "terraform -chdir=infra/terraform"
prefix := `uv run python scripts/cfg_get.py prefix`
# Terraform/aws CLI need the profile + region from the config too (Python sets them only for itself)
export AWS_PROFILE := env("AWS_PROFILE", `uv run python scripts/cfg_get.py profile`)
export AWS_REGION := `uv run python scripts/cfg_get.py region`
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

# Terraform passthrough with the vars from the last `deploy` (does not re-render them). `just tf plan`
tf *args:
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

# New run of a plan: runs/<date>-<plan>/ through all stages, then report + site
run plan:
    {{ob}} run --plan {{plan}}

# Redo stages of an existing run, e.g. `just rerun 2026-10-09-suite summarize` or `extract,score,summarize`
rerun id stages:
    {{ob}} run --id {{id}} --stages {{stages}}

runs:
    {{ob}} list

fetch id:
    {{ob}} fetch --run {{id}}

# Re-score + rebuild report.md and the site (one run, or all) and the experiments index
report id="":
    {{ob}} report {{ if id != "" { "--run " + id } else { "" } }}

# Open the experiments index (or one run)
open id="":
    open "site/{{ if id != "" { id + "/" } else { "" } }}index.html" 2>/dev/null || xdg-open "site/{{ if id != "" { id + "/" } else { "" } }}index.html"

# Which model ids does the gateway accept? Runs inside the VPC. e.g. `just probe --model some-id --filter haiku`
probe *args:
    {{ob}} probe-task {{args}}

# ---- teardown ----

[confirm("Destroy the whole ocr-bench stack for this config (endpoint, bucket incl. weights/results, ECR, IAM, network)? [y/N]")]
destroy:
    -{{ob}} endpoint down
    just _tfinit --with-model
    {{tf}} destroy -input=false -auto-approve -var-file="$PWD/{{state}}/terraform.tfvars.json"
