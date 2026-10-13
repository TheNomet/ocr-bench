# Configuration reference

There is one YAML file per deployment. `config/example.yaml` is the annotated template.
Real files (`config/<name>.yaml`) are gitignored. Choose one with `CONFIG=...` (just) or
`--config` / `$OCRBENCH_CONFIG` (CLI). Any string can use `${VAR}` or `${VAR:-default}`.

Terms:

- **stages**: `transcribe`, `extract`, `score`, `summarize`. `summarize` writes the
  recommendation (`summary.md` / `summary.json`) with the backend in `bench.summary.agent`.
- **extractor**: any model that extracts the fields in stage 2.
- **the agent**: the default extractor, `bench.extraction.agent`.
- **pipeline** `<source>-><extractor>`: the source is a transcriber backend or `image` (page
  images go straight to the extractor); the extractor is `agent` or a backend name.

| Key | Meaning |
|---|---|
| `name_prefix` | Prefix for every resource: bucket `<p>-artifacts`, ECR `<p>-vllm` / `<p>-bench`, task family `<p>-bench`, and per self-hosted backend endpoint `<p>-<name>` and model `<p>-<name>-model`. 3–31 chars of `[a-z0-9-]` |
| `aws.profile` | Profile for the CLI and Terraform (ignored inside the Fargate task) |
| `aws.account_id` | Terraform refuses to touch any other account |
| `aws.region` | Region for every resource |
| `aws.permissions_boundary_arn` | Optional boundary on every IAM role (needed by some landing zones) |
| `aws.tags` | Tags on every resource |
| `terraform_state.bucket/key/dynamodb_table` | S3 remote state; `bucket: null` keeps state in `.state/<prefix>/terraform.tfstate` |
| `network.mode` | `existing` or `create`. `create` provisions a NAT gateway, which bills while the stack exists (~$32/month idle, plus data processing) until `just destroy` |
| `network.existing.vpc_id` or `vpc_name_tag` | VPC lookup |
| `network.existing.subnet_ids` or `subnet_name_tag_glob` | Private subnets for the runner. They need routes to S3, ECR, CloudWatch Logs, SageMaker runtime and your LLM endpoints |
| `network.create.cidr` | /24 for the dedicated VPC (2 private subnets, 1 public subnet with the NAT gateway, S3 gateway endpoint) |
| `ecs.cluster` | `create`, or the name of an existing cluster |
| `backends.<name>` | Every model the benchmark uses, keyed by the name that appears in records, tables and pipelines (see [Backends](#backends)). Use neutral model names (`sonnet-4.5`, `unlimited-ocr`) |
| `backends.<name>.kind` | `selfhosted`, `bedrock` or `openai_compatible` |
| `backends.<name>.label` | Optional display name; default derived from the model id |
| `backends.<name>.postprocess` | Steps applied to the backend's output before scoring and extraction: `strip_grounding` (DeepSeek-OCR `<\|det\|>`/`<\|ref\|>` tokens), `strip_think`, `unwrap_code_fence`. Recorded in the run; the raw output is kept |
| `backends.<name>.temperature` | LLMs: overrides the request temperature (default 0). Some models accept only 1; `null` omits it. Recorded in the run and shown in the report |
| `backends.<name>.reasoning_effort` | LLMs: `low` / `medium` / `high` for models with extended thinking (sent as `reasoning_effort`) |
| `backends.<name>.price_per_mtok` | LLMs: `{input, output}` USD per million tokens, for cost figures. Without it the backend's cost is unknown and it is left out of "cheapest" |
| `bench.docs.per_type/seed/scale` | Document generation |
| `bench.extraction.agent` | Backend that extracts the fields in stage 2 (the `agent` in `<input>->agent`) |
| `bench.extraction.pipelines` | `"<transcriber>->agent"` and/or `"image->agent"`; `"<input>-><backend>"` uses that backend as the extractor. A plan skips pipelines whose transcriber it doesn't run |
| `bench.extraction.plan_pipelines.<plan>` | Pipelines for that plan instead of `pipelines` |
| `bench.extraction.concurrency` | Parallel extraction requests (default 8) |
| `bench.summary.agent` | Backend that writes the recommendation in stage 4 (default: the extraction agent). A strong reasoning model works best, e.g. Opus 5.5 with `reasoning_effort: medium` |
| `bench.prompts.transcribe` | `system`, `user`, `max_tokens` for LLM transcription. A self-hosted backend uses its own `request` instead |
| `bench.prompts.extract` | `system`, `rules` (appended after the generated field list), `max_tokens` |
| `bench.prompts.summarize` | `system`, `max_tokens`, `temperature` for the recommendation. The section structure and the number rules are fixed in code, because the number check depends on them |
| `bench.plan_report.<plan>.hide_backends` | Backends re-run only as a reproducibility check: hidden on the run page and in its recommendation, kept in the data and in *Compare runs* |
| `bench.plan_descriptions.<plan>` | One line shown on every run of that plan (run ids are `<date>-<plan>`) |
| `bench.plans.<name>` | List of `{backend, concurrency, variant?, limit?, repeat?, warmup?}` runs (see [Plan items](#plan-items)) |
| `bench.max_retries` | Retries on 429/5xx and network errors, with exponential backoff |
| `runner.mode` | `fargate` (in the VPC) or `local` |
| `runner.cpu/memory` | Fargate task size |
| `runner.max_minutes` | Hard time limit per bench task (default 240) |
| `runner.base_image` | Python 3.12 base for the bench image |
| `runner.extra_ca_pem` | PEM baked into the bench image and trusted for outbound TLS |

## Plan items

Each item of `bench.plans.<name>` is one transcription run of one backend.

| Field | Meaning |
|---|---|
| `backend` | Backend key |
| `concurrency` | Requests in flight at once |
| `variant` | `clean`, `degraded` or `both` (default) page images |
| `limit` | Only this many page images (default 0 = all). The pages are interleaved by document type, so a small limit still covers several types |
| `repeat` | Send every page this many times (default 1), e.g. to measure run-to-run variation |
| `warmup` | Send one untimed request first (default `true`): token fetch, CUDA graphs, connection pools. Not recorded or scored |

Prompt defaults live in `src/ocrbench/prompts.py`. Whatever a run used is recorded in its
`run.json` (`prompts`), and resumed or re-scored runs read it from there.

## Backends

```yaml
backends:
  unlimited-ocr:                # self-hosted: vLLM on its own SageMaker endpoint
    kind: selfhosted
    hf_repo: baidu/Unlimited-OCR
    hf_revision: <full 40-char commit hash>
    hf_skip_prefixes: []        # files not uploaded
    source_image: docker.io/vllm/vllm-openai@sha256:...
    served_model_name: baidu/Unlimited-OCR
    serve_args: [--trust-remote-code, ...]
    request: {prompt: "<image>document parsing.", max_tokens: 8192, temperature: 0.0, extra_body: {}}
    postprocess: [strip_grounding]
    sagemaker:
      instance_pools: [ml.g6.xlarge, ml.g5.xlarge]
      inference_ami_version: al2023-ami-sagemaker-inference-gpu-4-1
      network_isolation: true
      max_lifetime_hours: 8
      price_per_hour_usd: null  # default: AWS Pricing API, for the instance actually placed
    direct_url: null            # or a running vLLM server; then no AWS resources are created for it

  sonnet-4.5:                   # Bedrock
    kind: bedrock
    model_id: <model or inference-profile id>
    region: eu-west-1
    price_per_mtok: {input: 3.0, output: 15.0}

  gw-sonnet:                    # any OpenAI-compatible /v1/chat/completions
    kind: openai_compatible
    base_url: https://gateway.example.internal
    model: <model id the gateway expects>
    cache_bust: true            # gateway caches identical requests
    ca_bundle: null
    headers: {}
    extra_body: {}
    auth:
      type: oauth2_kms_jwt      # or: none | bearer_env (env: VAR_NAME)
      token_url: https://idp.example.com/oauth2/token
      audience: null            # defaults to token_url
      client_id: <client id>
      kms_key_id: <key id or ARN>   # ECC_NIST_P256, SIGN_VERIFY
      kms_region: eu-west-1
      scope: <scope>
```

### Self-hosted backends

Any number of them; each one gets its own image tag (`<p>-vllm:<name>-sagemaker`), weights
prefix (`s3://<p>-artifacts/models/<name>/`), SageMaker model and endpoint. The name must be
`[a-z0-9-]`, because it names those resources.

| Key | Meaning |
|---|---|
| `hf_repo`, `hf_revision` | Model snapshot, pinned to a full commit hash. Every file is hash-checked against Hugging Face on upload |
| `hf_skip_prefixes` | Files in the snapshot that are not uploaded (path prefixes) |
| `served_model_name` | Model name vLLM serves under (`--served-model-name`); recorded as the backend's model |
| `source_image` | vLLM image that can serve the model. Prefer `name@sha256:...`; with a tag the build prints the digest to pin. It is mirrored by digest, and the deployed model always references the built image by digest |
| `image_platform` | Platform crane resolves and mirrors from `source_image` (default `linux/amd64`, what SageMaker GPU hosts run) |
| `serve_args` | Arguments to `vllm serve` (model path, host and port are added). Baked into `/opt/serve.sh` by `ocrbench images <name>` |
| `request` | Request body: `prompt`, `max_tokens`, `temperature`, `extra_body` (merged as-is, e.g. `vllm_xargs`) |
| `sagemaker.instance_pools` | GPU types in priority order |
| `sagemaker.inference_ami_version` | Host image, which sets the NVIDIA driver |
| `sagemaker.container_startup_timeout_s` | How long SageMaker waits for the container's health check (vLLM loading the model) before failing the endpoint (default 1800) |
| `sagemaker.model_download_timeout_s` | How long SageMaker allows for copying the weights from S3 to the host (default 1800) |
| `sagemaker.network_isolation` | Run the container without network access (default `true`); the weights come from S3 |
| `sagemaker.max_lifetime_hours` | The endpoint deletes itself after this long (EventBridge Scheduler, created by `endpoint up`); 0 = never |
| `sagemaker.price_per_hour_usd` | Optional. When unset, the run looks up the deployed instance's price in the AWS Pricing API |
| `direct_url` | Use a running vLLM server instead of SageMaker |

Commands that act on one self-hosted backend take `--backend <name>` (`ocrbench endpoint up`,
`upload-weights`; `just endpoint-up <name>`); it can be left out when the config has only one.

### Older configs

Configs with a top-level `ocr:` block and `llm_backends:` still load: `ocr:` becomes a
self-hosted backend named `ocr` and each `llm_backends` entry keeps its key, with `provider`
read as `kind`. Runs made with such a config call that backend `ocr` too. To give it a model
name, first move the config to the `backends:` shape (as in `config/example.yaml`), then rename
the key in the config and in old runs at once:

```bash
uv run python scripts/rename_backends.py --config config/<name>.yaml --runs runs/* \
    --map ocr=unlimited-ocr [--map old2=new2] [--redact-endpoint]
```

`--redact-endpoint` also turns `model@sagemaker:<endpoint-name>` into `model@sagemaker` in
records. `--config` is optional (runs only); `--help` lists the options.

### Permissions

`tf-vars` grants the runner role `kms:Sign` on every `oauth2_kms_jwt` key,
`bedrock:InvokeModel`/`Converse` on every Bedrock model in the config, and
`InvokeEndpoint` on every self-hosted backend's endpoint.
`bearer_env` tokens are read from the environment of whoever runs the request. In Fargate
mode that is the task, which has no such variable, so use `runner.mode: local` for those.
