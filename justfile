# ocr-bench task runner. Every recipe uses CONFIG (default config/example.yaml).
#
#   export CONFIG=config/mine.yaml
#   just tools          # terraform + crane into .tools/ (one-off)
#   just deploy         # infra, images, weights, SageMaker model (idempotent)
#   just endpoint-up    # start the GPU endpoint (~15 min; billed per hour from here). Several
#                       # self-hosted backends? `just endpoint-up <name>`
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
selfhosted := `uv run python scripts/cfg_get.py selfhosted`

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

# Including the slow headless-Chrome rendering tests
test-all:
    uv run pytest -q -m ''

# Same checks as CI (.github/workflows/ci.yml), minus the secret scan
lint:
    uv run ruff check src tests scripts && uv run ruff format --check src tests scripts
    uv run pyright
    {{tf}} fmt -check -recursive

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
    {{ob}} images all
    just weights
    just _tfinit --with-model
    {{tf}} apply -input=false -auto-approve -var-file="$PWD/{{state}}/terraform.tfvars.json"
    just _outputs
    @echo "deployed. next: just endpoint-up && just docs && just run smoke"

# Build + push both images (pinned by digest) and point the stack at them (terraform apply)
images:
    {{ob}} images all
    just _tfinit --with-model
    {{tf}} apply -input=false -auto-approve -var-file="$PWD/{{state}}/terraform.tfvars.json"
    just _outputs

# Weights of every self-hosted backend -> S3 (models/<name>/), skipped where the manifest is already there.
# Proxy blocks the HF CDN? `just weights ~/Downloads` (with several self-hosted backends: `just weights ~/Downloads <name>`)
weights from_dir="" backend="":
    #!/usr/bin/env bash
    set -euo pipefail
    bucket=$(python3 -c "import json;print(json.load(open('{{state}}/outputs.json'))['bucket']['value'])")
    for name in {{ if backend != "" { backend } else { selfhosted } }}; do
      if [ -z "{{from_dir}}" ] && aws s3 ls "s3://$bucket/models/$name.manifest.json" >/dev/null 2>&1; then
        echo "$name: weights already in s3://$bucket/models/$name/"; continue
      fi
      {{ob}} upload-weights --backend "$name" {{ if from_dir != "" { "--from-dir " + from_dir } else { "" } }}
    done

# ---- GPU endpoint ----

# The backend name is optional when the config has one self-hosted backend
endpoint-up backend="":
    {{ob}} endpoint up {{ if backend != "" { "--backend " + backend } else { "" } }}

# Without a name: every self-hosted backend
endpoint-down backend="":
    for b in {{ if backend != "" { backend } else { selfhosted } }}; do {{ob}} endpoint down --backend "$b"; done

endpoint-status:
    for b in {{selfhosted}}; do {{ob}} endpoint status --backend "$b"; done

endpoint-logs backend="":
    aws logs tail "/aws/sagemaker/Endpoints/{{prefix}}-{{ if backend != "" { backend } else { selfhosted } }}" --since 30m

# ---- benchmark ----

docs per_type="":
    {{ob}} docs {{ if per_type != "" { "--per-type " + per_type } else { "" } }}

# New run of a plan: runs/<date>-<plan>/ through all stages, then report + site
run plan:
    {{ob}} run --plan {{plan}}

# Redo stages of an existing run, e.g. `just rerun 2026-10-09-ocr-vs-claude45 summarize` or `extract,score,summarize`
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
    -just endpoint-down
    just _tfinit --with-model
    {{tf}} destroy -input=false -auto-approve -var-file="$PWD/{{state}}/terraform.tfvars.json"

# ---- public site ----

# Rebuild the site, scan it with the public guard, push it to the gh-pages branch and deploy Pages
publish:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ob}} report
    scripts/public_guard.sh dir site
    tmp=$(mktemp -d)
    cp -R site/. "$tmp/"
    touch "$tmp/.nojekyll"
    url=$(git remote get-url origin)
    name=$(git config user.name); email=$(git config user.email)
    git -C "$tmp" init -q -b gh-pages
    git -C "$tmp" -c user.name="$name" -c user.email="$email" add -A
    git -C "$tmp" -c user.name="$name" -c user.email="$email" commit -q -m "site: $(date +%Y-%m-%d) from $(git rev-parse --short HEAD)"
    git -C "$tmp" push -q --force "$url" gh-pages
    rm -rf "$tmp"
    gh workflow run pages.yml --ref main
    echo "deploying: $(gh api repos/{owner}/{repo}/pages --jq .html_url 2>/dev/null || echo 'see repo Settings > Pages')"
