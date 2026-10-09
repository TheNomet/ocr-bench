# AGENTS.md: orientation for coding agents

Read this first. It tells you what the project does, where things are, and the rules that
aren't obvious from the code.

## What this is

ocr-bench is a benchmark harness. It generates synthetic documents with ground truth, sends
the page images to several **transcribers** (a self-hosted vLLM OCR model on SageMaker, and
hosted LLMs), then has an **agent** LLM extract structured fields from each transcript. It
scores everything and writes a report plus a static HTML site. The infrastructure is
temporary: deploy it, run, destroy it.

## Map

```
config/example.yaml        the ONLY committed config; real configs are config/<name>.yaml (gitignored)
src/ocrbench/
  config.py                load + env expansion + derived resource names (keep in sync with infra/terraform/main.tf locals)
  cli.py                   all commands; `ocrbench --help`
  docgen/                  document generator (types/<type>.py = one module per document type)
  backends/                ocr (vllm_ocr.py), bedrock.py, openai_compat.py (auth: none | bearer_env | oauth2_kms_jwt)
  transcribe.py            stage 1 runner (concurrency, retries, JSONL records)
  extract.py               stage 2 runner (pipelines "<transcriber>->agent" and "image->agent")
  score.py                 metrics + aggregation -> scores dict
  reporting.py, report/    report.md + static site
  images.py                crane-based image builds (OCR SageMaker wrapper, bench runner)
  weights.py               HF snapshot -> S3, SHA-256 verified
  aws.py                   S3 sync, SageMaker endpoint lifecycle, Fargate run_task, pricing
infra/terraform/           one root module; network/ submodule for network.mode=create
justfile                   deploy / run / report / destroy
.state/<prefix>/           LOCAL: tfvars, outputs.json, local tf state (gitignored)
docs-out/ results/ site/   LOCAL generated data (gitignored)
```

## Lifecycle (see README for commands)

1. `ocrbench tf-vars` renders `.state/<prefix>/terraform.tfvars.json` and
   `infra/terraform/backend.tf` from the config. Terraform reads nothing else.
2. Terraform creates the bucket, the ECR repos (`<prefix>-vllm`, `<prefix>-bench`), IAM, the
   Fargate task definition and optionally a VPC. Once the image and weights exist, it also
   creates the SageMaker **model** (`create_ocr_model`).
3. The SageMaker **endpoint** is *not* in Terraform. `ocrbench endpoint up` creates it with
   `InstancePools`, a priority list of GPU types, because single GPU types often have no
   capacity. The AWS provider doesn't support instance pools.
4. `ocrbench run` with `runner.mode: fargate` uploads `docs-out/` and the config to S3, then
   launches the Fargate task, which runs `ocrbench task-run`. It streams the logs and syncs
   `results/<tag>/` back.
5. `ocrbench report` scores the run and writes `results/<tag>/report.md` and
   `site/<tag>/index.html`. If `results/<tag>/findings.md` exists (hand- or agent-written),
   it is placed at the top of the report.

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
- **Resource names** come from `name_prefix`. If you change a name, change it in both
  `config.py` and `infra/terraform/main.tf` `locals`.
- **Cost.** The SageMaker endpoint bills per hour while it exists. Always finish with
  `just endpoint-down` (or `just destroy`).
- **Scoring is by design not formatting-sensitive.** CER/WER are computed on normalised text
  (markdown/HTML stripped). Word recall/precision ignore order. Numbers are checked by
  presence. Field values are compared after case, whitespace and currency-prefix
  normalisation. Change `score.py` deliberately and update the tests.

## Gotchas found the hard way

- Some LLM gateways **cache identical requests**, so a repeated benchmark returns in about
  60 ms. Set `cache_bust: true` on `openai_compatible` backends; it adds a nonce to the
  system prompt.
- SageMaker real-time `InvokeEndpoint` has a **60 s per-request limit**. At high concurrency
  a single GPU queues requests until they time out. The plans stop at concurrency 32.
- vLLM OCR images for recent models need a recent NVIDIA driver. The default
  `inference_ami_version` (`al2023-ami-sagemaker-inference-gpu-4-1`) ships CUDA 13
  drivers.
- The Unlimited-OCR model **needs** its logits processor and a literal `<image>` prompt
  prefix. Without them it returns nothing or loops (see `ocr.serve_args` and `ocr.request`).
- Corporate TLS inspection: `runner.extra_ca_pem` bakes a CA into the runner image. Some
  proxies interpose a "generative AI" warning page on huggingface.co. Open the URL in a
  browser once, or use `just weights ~/Downloads`.
- Landing zones that only allow approved AMIs on EC2 will stop a GPU box running a stock
  Deep Learning AMI. That's why the model runs on SageMaker, whose hosts are managed outside
  your EC2.

## Testing

`just test` runs unit tests plus an end-to-end local pipeline with a fake backend (needs
Chrome; that test skips otherwise). `just lint` runs ruff and terraform fmt.
