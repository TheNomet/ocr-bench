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
cfg := "uv run python scripts/cfg_get.py"

# Nothing is read from the config when the justfile loads, so `just lint`/`just test` work without one.
# Recipes that run terraform or the aws CLI start with this prelude: it sets $prefix, $state (.state/<prefix>)
# and $vars (the tfvars file), and exports AWS_REGION and AWS_PROFILE from the config (Python sets them only
# for itself). An AWS_PROFILE already in the environment wins.
_env := 'prefix=$(' + cfg + ' prefix); region=$(' + cfg + ' region); profile=$(' + cfg + ' profile); export AWS_REGION="$region"; if [ -z "${AWS_PROFILE:-}" ] && [ -n "$profile" ]; then export AWS_PROFILE="$profile"; fi; state=".state/$prefix"; vars="$PWD/$state/terraform.tfvars.json"'

# Shell expression: the space-separated SageMaker-served self-hosted backends of the config
_backends := '$(' + cfg + ' selfhosted)'

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

# Tests with a coverage report (htmlcov/)
cov:
    uv run pytest -q --cov=ocrbench --cov-report=term-missing:skip-covered --cov-report=html

# Same checks as CI (.github/workflows/ci.yml), minus the secret scan and workflow lint
lint:
    uv run ruff check src tests scripts && uv run ruff format --check src tests scripts
    uv run pyright
    {{tf}} fmt -check -recursive

# ---- infrastructure ----

# Terraform passthrough with the vars from the last `deploy` (does not re-render them). `just tf plan`
[positional-arguments]
tf *args:
    #!/usr/bin/env bash
    set -euo pipefail
    {{_env}}
    case "${1:-}" in
      plan|apply|destroy|import|console|refresh) sub=$1; shift; {{tf}} "$sub" -var-file="$vars" "$@" ;;
      *) {{tf}} "$@" ;;
    esac

_tfinit with_model="":
    #!/usr/bin/env bash
    set -euo pipefail
    {{_env}}
    {{ob}} tf-vars {{with_model}}
    {{tf}} init -reconfigure -input=false >/dev/null

# Render vars, init, apply and record the outputs (.state/<prefix>/outputs.json)
_apply with_model="":
    #!/usr/bin/env bash
    set -euo pipefail
    just _tfinit {{with_model}}
    {{_env}}
    {{tf}} apply -input=false -auto-approve -var-file="$vars"
    {{tf}} output -json > "$state/outputs.json"

# Full deploy: infra -> images -> weights -> SageMaker model. Safe to re-run.
deploy:
    just _apply
    {{ob}} images all
    just weights
    just _apply --with-model
    @echo "deployed. next: just endpoint-up && just docs && just run smoke"

# Build + push both images (pinned by digest) and point the stack at them (terraform apply)
images:
    {{ob}} images all
    just _apply --with-model

# Weights of every self-hosted backend -> S3 (models/<name>/), skipped where S3 already has the configured
# hf_revision; a new revision replaces the old files.
# Proxy blocks the HF CDN? `just weights ~/Downloads` (with several self-hosted backends: `just weights ~/Downloads <name>`)
weights from_dir="" backend="":
    #!/usr/bin/env bash
    set -euo pipefail
    for name in {{ if backend != "" { backend } else { _backends } }}; do
      {{ob}} upload-weights --backend "$name" {{ if from_dir != "" { "--from-dir " + from_dir } else { "--if-changed" } }}
    done

# ---- GPU endpoint ----

# The backend name is optional when the config has one self-hosted backend
endpoint-up backend="":
    {{ob}} endpoint up {{ if backend != "" { "--backend " + backend } else { "" } }}

# Without a name: every endpoint of the stack (<name_prefix>-*), also those of backends no longer in the
# config. Keeps going past a failure and exits non-zero if any endpoint is left.
endpoint-down backend="":
    {{ob}} endpoint down {{ if backend != "" { "--backend " + backend } else { "--all" } }}

endpoint-status:
    for b in {{_backends}}; do {{ob}} endpoint status --backend "$b"; done

# Last 30 min of the endpoint's CloudWatch logs; the name is required with several self-hosted backends
endpoint-logs backend="":
    #!/usr/bin/env bash
    set -euo pipefail
    {{_env}}
    names=({{ if backend != "" { backend } else { _backends } }})
    if [ "${#names[@]}" -ne 1 ]; then
      echo "self-hosted backends: ${names[*]:-none}; pick one: just endpoint-logs <name>" >&2; exit 1
    fi
    aws logs tail "/aws/sagemaker/Endpoints/$prefix-${names[0]}" --since 30m

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
    #!/usr/bin/env bash
    set -euo pipefail
    # refuses (non-zero) while any endpoint of the stack is left: the scheduler role must outlive it
    just endpoint-down
    just _tfinit --with-model
    {{_env}}
    {{tf}} destroy -input=false -auto-approve -var-file="$vars"

# ---- public site ----

# Rebuild the site, scan it with the public guard, push it to the gh-pages branch and deploy Pages.
# Refuses when rebuilding changed runs/ (or runs/ has uncommitted work): the site must match the repo.
publish:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ob}} report
    if ! git diff --quiet HEAD -- runs/ || [ -n "$(git ls-files --others --exclude-standard -- runs/)" ]; then
      git status --short -- runs/ >&2
      echo "runs/ changed: commit first" >&2; exit 1
    fi
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
