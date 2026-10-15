# AGENTS.md: orientation for coding agents

Read this first. It tells you what the project does, where things are, and the rules that
aren't obvious from the code.

## What this is

ocr-bench is a benchmark harness. It generates synthetic documents with ground truth, sends
the page images to several **transcribers** (self-hosted vLLM models on SageMaker, and
hosted LLMs), then has an **extractor** LLM (by default **the agent**, `bench.extraction.agent`)
extract structured fields from each transcript or straight from the images. It scores
everything, has an LLM write a recommendation (`summarize`), and writes a report plus a static
HTML site. The infrastructure is temporary: deploy it, run, destroy it. Terms (stage,
extractor, agent, pipeline) are defined at the top of the [README](README.md).

## Map

```
config/example.yaml        the ONLY committed config; real configs are config/<name>.yaml (gitignored)
src/ocrbench/
  config.py                load + env expansion + derived resource names (keep in sync with infra/terraform/main.tf locals)
  cli.py                   all commands; `ocrbench --help`
  docgen/                  document generator (types/<type>.py = one module per document type)
  backends/                one per kind: vllm.py (selfhosted), bedrock.py, openai_compat.py (auth: none | bearer_env | oauth2_kms_jwt)
  postprocess.py           named output clean-up steps (strip_grounding...), listed per backend
  prompts.py               prompt + token-limit defaults; a run records what it used in run.json
  runs.py                  run folders runs/<id>/ + run.json (schema_version, backends, prompts, stage timings); upgrade() for old runs
  transcribe.py            stage 1 runner (concurrency, retries, JSONL records)
  extract.py               stage 2 runner (pipelines "<source>-><extractor>": "<transcriber>->agent", "image->agent")
  score.py                 stage 3: metrics + aggregation -> scores dict
  summarize.py             stage 4: LLM writes the recommendation (summary.md) from the key facts + setup
  view.py                  how a run names its backends, roles, prices and pipelines (report.md, site, summary prompt)
  metrics.py               every metric column: label, format, better direction, glossary
  explain.py               static texts: intro, stages, variants, document challenges
  facts.py                 computed key facts: the source of truth for every comparison
  reporting.py, report/    report.md, per-run site, experiments index
  images.py                crane-based image builds (one SageMaker image per self-hosted backend, bench runner)
  weights.py               HF snapshot -> S3, SHA-256 verified
  aws.py                   S3 sync (fetch drops local *.partial.jsonl gone from S3), SageMaker endpoints, Fargate, pricing
infra/terraform/           one root module; network/ submodule for network.mode=create
justfile                   deploy / run / report / destroy
.state/<prefix>/           LOCAL: tfvars, outputs.json, local tf state (gitignored)
scripts/rename_backends.py rename backend keys across a config and old runs
docs-out/                  LOCAL current document set (a run copies it into runs/<id>/docs)
runs/<id>/                 one folder per run, committed (except config.yaml), mirrored to s3://<bucket>/runs/<id>/
site/                      LOCAL build of the site (gitignored); published to the gh-pages branch
```

## Lifecycle (see README for commands)

1. `ocrbench tf-vars` renders `.state/<prefix>/terraform.tfvars.json` and
   `infra/terraform/backend.tf` from the config. Terraform reads nothing else.
2. Terraform creates the bucket, the ECR repos (`<prefix>-vllm`, `<prefix>-bench`), IAM, the
   Fargate task definition and optionally a VPC. Once the images and weights exist, it also
   creates one SageMaker **model** per self-hosted backend listed in `create_selfhosted_models`
   (a list of backend names; `tf-vars` keeps the previous list so a re-deploy doesn't delete models; with no
   local tfvars it keeps the models that exist in SageMaker, and refuses without AWS credentials).
3. The SageMaker **endpoints** are *not* in Terraform. `ocrbench endpoint up [--backend B]` creates one with
   `InstancePools`, a priority list of GPU types, because single GPU types often have no
   capacity. The AWS provider doesn't support instance pools.
4. `ocrbench run --plan P` creates `runs/<date>-<plan>/`: a copy of `docs-out/`, a config
   snapshot, and `run.json`. It records the GPU host that is actually serving and its price
   from the Pricing API. With `runner.mode: fargate` it uploads the folder to
   `s3://<bucket>/runs/<id>/`, launches the task (`ocrbench task-run`), which runs the
   stages and syncs back after each one, then fetches the folder.
5. The stages are transcribe → extract → score → summarize. Each can be redone with
   `ocrbench run --id <id> --stages ...`. The summarize stage needs the LLM, so with a
   private gateway it runs in Fargate too.
6. `ocrbench report` re-scores locally (cheap), rewrites `report.md` and `site/<id>/`, and
   rebuilds `site/index.html` from all runs.

## Rules

- **Public repo.** Never commit account IDs, internal hostnames, client IDs, key IDs, or
  organisation names. They belong in `config/<name>.yaml` (gitignored). Contributors keep a
  local `.public-denylist` (gitignored, one regex per line); the `pre-commit`/`pre-push` hooks
  (`scripts/install_hooks.sh`) refuse any change that matches it. Don't bypass the hooks.
- **Generated documents must stay neutral and fictional** (everyday personal and household
  paperwork). Don't add forms that copy a real organisation's layout.
  `tests/test_docgen.py` checks for forbidden words.
- **One config, no hard-coded values.** If you need a new knob, add it to
  `config/example.yaml` with a comment, document it in `docs/configuration.md`, and read it
  through `Config`.
- **Resource names** come from `name_prefix` (and the backend name for self-hosted resources).
  If you change a name, change it in both `config.py` and `infra/terraform/main.tf`.
- **No backend name is special.** Code decides by `kind` (`view.is_selfhosted`), never by name.
  Keys are neutral model names ([README: Naming backends](README.md#naming-backends)).
  A run's `run.json` is the only input to scoring and reporting; if you add a setting that
  changes results, record it there and bump `runs.SCHEMA_VERSION` with an `upgrade()` step.
- **Runs are committed, their `config.yaml` is not.** Everything else in `runs/<id>/` is meant
  to be public: SageMaker endpoint names are kept out of records (`model@sagemaker`). If you add a field to `run.json` or the
  records, make sure it can't carry hosts, account ids or client ids. Images on a private registry are
  recorded as `<private-registry>/<image>[:tag|@digest]`. The guard (`scripts/public_guard.sh`)
  runs on every commit/push, on the site in `just publish`, and in CI when the `PUBLIC_DENYLIST`
  repository secret is set (it warns and skips otherwise). Its output names file:line and the denylist
  line, never the matched text.
- **The site is published from a `gh-pages` branch**, never from `main`
  ([README: Publishing](README.md#publishing)); `.github/workflows/pages.yml` only deploys that
  branch. Keep `just publish` refusing when regenerated reports leave `runs/` uncommitted.
- **Numbers are computed, the LLM only comments.** `facts.py` computes the key facts (best,
  cheapest, fastest pipeline, break-even rates...). `summarize.py` gives only those facts and a
  setup description to the recommendation model (`bench.summary.agent`, Opus 5.5 at medium reasoning
  effort in our runs), and every number in its text is checked against them; misses are
  flagged as "unverified". Keep both properties if you change it.
- **Pipelines where one model both transcribes and extracts are hidden** (`reporting.visible`):
  they only measure the model against itself. Don't add `X->X` to plans.
- **Re-summarising an old run** (`just rerun <id> summarize`) uses the *current* recommendation model (`bench.summary.agent`);
  `runs.refresh_summary_setup` patches the run's snapshot. Nothing from stages 1-3 changes.
- **Cost.** The SageMaker endpoint bills per hour while it exists. Always finish with
  `just endpoint-down` (or `just destroy`). With `network.mode: create` the NAT gateway also
  bills (~$32/month idle) until `just destroy`.

## How results are scored (don't change silently)

Change `score.py` deliberately and update the tests.

`score.py` reads only the run folder (`run.json` for prices, GPU price and post-processing), so a run
scores the same anywhere. Its choices are written into `scores.json` under `method`. It is by design not
formatting-sensitive:
- CER/WER are computed on normalised text (markdown/HTML stripped, HTML entities decoded) and
  capped at 1 per page, so one runaway page can't dominate a mean; word recall/precision ignore order;
- failed pages score CER=1/recall 0 and failed extractions 0 (see `coverage`); nothing is dropped.
  Pages a plan item selected, and documents a pipeline's source covered, that have no record
  (task killed) count as failed;
- a page with no numbers in the ground truth has number recall 1;
- the scored transcript of a page is the one from the lowest-concurrency run (latest if several);
- a self-hosted model's per-page cost uses its best sustained throughput (<=1% errors), with the
  concurrency-1 cost reported next to it; if no run qualifies, the lowest-error run is used and flagged;
- unknown cost (no price, or no token usage) is unknown, never $0;
- cheapest/fastest rank only pipelines with >=99% coverage (`method.ranking_min_coverage`);
- numbers are checked by presence and match as whole tokens;
- field values are compared after case and whitespace normalisation, ignoring currency prefixes/suffixes;
  a value for a schema field that the ground truth leaves out (optional field) counts as one wrong field;
- end-to-end latency of `X->agent` is the sum of X's per-page latencies for the document
  (per page, the record chosen as above) plus the extraction call; unknown if a page has none; its
  cost adds X's per-page costs.
- repeats (plan item `repeat: N`, `bench.extraction.plan_repeat.<plan>` -> run.json `extraction.repeat`):
  records carry `repeat` 0..N-1. The main tables score the last repeat (a success beats a failure), so they
  stay comparable with runs without repeats. Each repeat is also scored on its own into `scores.json`
  `variation` (only when something was repeated; rules in `method.variation`): transcription at the
  backend's lowest concurrency with repeats, every repeat over the same pages/documents with the usual
  failure rules, std = sample std. `T->X` repeats re-read the one scored transcript (they measure X alone).
  `facts.variation_facts` calls a gap between two means "outside the run-to-run range" only when it is
  larger than the larger of their two min–max ranges.

## Safety rails

- A GPU endpoint deletes itself after `backends.<name>.sagemaker.max_lifetime_hours`; bench tasks stop at
  `runner.max_minutes`. Images are deployed by digest (`.state/<prefix>/images.json`), the bench
  image installs from `uv.lock` with hashes, and every model file is hash-checked against Hugging Face.
  `ocrbench run` (Fargate) refuses while `images.json`'s bench digest isn't the deployed one: an
  un-applied rebuild leaves the pinned digest untagged, and ECR expires it after `retention.ecr_untagged_days`.
- Retention (S3 `runs/`, CloudWatch logs, ECR untagged images and mirrors) is `retention.*` in the config.
- Error text is redacted (`redact.py`) before it is written to records.
- `scripts/public_guard.sh` fails closed (no denylist / bad pattern = error) and also scans file
  names, commit messages and authors (`commit-msg` hook).

## Gotchas found the hard way

- **Edit with care after `ruff format`.** Several string-replace edits silently missed because the
  formatter had re-wrapped the target code. After a scripted edit, grep that it landed.
- Newer Claude models (5.5) accept only temperature 1 through the gateway (its LiteLLM layer rejects
  0 with `UnsupportedParamsError`; re-checked 2026-10-14 with
  `just probe --backend <name> --temperature 0 --temperature 1`); set `temperature: 1`
  on that backend. It is recorded in the run and shown in the report, because results then vary
  slightly between runs.

- Some LLM gateways **cache identical requests**, so a repeated benchmark returns in about
  60 ms. Set `cache_bust: true` on `openai_compatible` backends; it adds a nonce to the
  system prompt.
- SageMaker real-time `InvokeEndpoint` has a **60 s per-request limit**. At high concurrency
  a single GPU queues requests until they time out. The plans stop at concurrency 32. A slow model
  writing a long page hits it even one at a time (Dolphin-v2 on an A10G, ~66 tokens/s):
  `sagemaker.response_stream: true` streams the answer, which SageMaker allows up to 8 min.
- vLLM OCR images for recent models need a recent NVIDIA driver. The default
  `inference_ami_version` (`al2023-ami-sagemaker-inference-gpu-4-1`) ships CUDA 13
  drivers.
- The Unlimited-OCR model **needs** its logits processor and a literal `<image>` prompt
  prefix. Without them it returns nothing or loops (see its `serve_args` and `request`).
- Corporate TLS inspection: `runner.extra_ca_pem` bakes a CA into the runner image. Some
  proxies interpose a "generative AI" warning page on huggingface.co. Open the URL in a
  browser once, or use `just weights ~/Downloads`.
- Landing zones that only allow approved AMIs on EC2 will stop a GPU box running a stock
  Deep Learning AMI. That's why the model runs on SageMaker, whose hosts are managed outside
  your EC2.

## Testing

`just test` runs the unit tests plus an end-to-end local pipeline with fake backends (two
self-hosted ones, to keep names unspecial); no Chrome needed. `just test-all` adds the
Chrome rendering tests. `just lint` runs ruff, pyright and terraform fmt. CI
(`.github/workflows/ci.yml`) runs the same checks plus `terraform validate` and a gitleaks secret
scan on every push; its actions are pinned by commit SHA.
