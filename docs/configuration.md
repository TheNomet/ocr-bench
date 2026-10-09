# Configuration reference

There is one YAML file per deployment. `config/example.yaml` is the annotated template.
Real files (`config/<name>.yaml`) are gitignored. Choose one with `CONFIG=...` (just) or
`--config` / `$OCRBENCH_CONFIG` (CLI). Any string can use `${VAR}` or `${VAR:-default}`.

| Key | Meaning |
|---|---|
| `name_prefix` | Prefix for every resource: bucket `<p>-artifacts`, ECR `<p>-vllm` / `<p>-bench`, endpoint `<p>-ocr`, model `<p>-ocr-model`, task family `<p>-bench`. 3–31 chars of `[a-z0-9-]` |
| `aws.profile` | Profile for the CLI and Terraform (ignored inside the Fargate task) |
| `aws.account_id` | Terraform refuses to touch any other account |
| `aws.region` | Region for every resource |
| `aws.permissions_boundary_arn` | Optional boundary on every IAM role (needed by some landing zones) |
| `aws.tags` | Tags on every resource |
| `terraform_state.bucket/key/dynamodb_table` | S3 remote state; `bucket: null` keeps state in `.state/<prefix>/terraform.tfstate` |
| `network.mode` | `existing` or `create` |
| `network.existing.vpc_id` or `vpc_name_tag` | VPC lookup |
| `network.existing.subnet_ids` or `subnet_name_tag_glob` | Private subnets for the runner. They need routes to S3, ECR, CloudWatch Logs, SageMaker runtime and your LLM endpoints |
| `network.create.cidr` | /24 for the dedicated VPC (2 private subnets, NAT, S3 endpoint) |
| `ecs.cluster` | `create`, or the name of an existing cluster |
| `ocr.hf_repo`, `hf_revision` | Model snapshot, pinned. `hf_skip_prefixes` lists files not uploaded |
| `ocr.source_image` | vLLM image that can serve the model |
| `ocr.serve_args` | Arguments to `vllm serve` (model path, host and port are added). Baked into `/opt/serve.sh` by `ocrbench images ocr` |
| `ocr.request` | Request body: `prompt`, `max_tokens`, `temperature`, `extra_body` (merged as-is, e.g. `vllm_xargs`) |
| `ocr.strip_grounding_tokens` | Remove `<\|det\|>` boxes and unwrap `<\|ref\|>` before scoring and extraction |
| `ocr.sagemaker.instance_pools` | GPU types in priority order |
| `ocr.sagemaker.inference_ami_version` | Host image, which sets the NVIDIA driver |
| `ocr.sagemaker.price_per_hour_usd` | Optional. When unset, `report` looks up the deployed instance's price in the AWS Pricing API |
| `ocr.direct_url` | Use a running vLLM server instead of SageMaker |
| `llm_backends.<key>` | Named backends (see below) |
| `bench.docs.per_type/seed/scale` | Document generation |
| `bench.transcribers` | Informational list of stage-1 backends |
| `bench.extraction.agent` | `llm_backends` key used for stage 2 |
| `bench.extraction.pipelines` | `"<transcriber>->agent"` and/or `"image->agent"` |
| `bench.summary.agent` | `llm_backends` key that writes the findings in stage 4 (default: the extraction agent) |
| `bench.plans.<name>` | List of `{backend, concurrency, variant?, limit?, repeat?, warmup?}` runs |
| `bench.max_retries` | Retries on 429/5xx with exponential backoff |
| `runner.mode` | `fargate` (in the VPC) or `local` |
| `runner.cpu/memory` | Fargate task size |
| `runner.base_image` | Python 3.12 base for the bench image |
| `runner.extra_ca_pem` | PEM baked into the bench image and trusted for outbound TLS |

## LLM backends

```yaml
llm_backends:
  sonnet:                       # Bedrock
    provider: bedrock
    model_id: <model or inference-profile id>
    region: eu-west-1
    price_per_mtok: {input: 3.0, output: 15.0}

  gw-sonnet:                    # any OpenAI-compatible /v1/chat/completions
    provider: openai_compatible
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

`tf-vars` grants the runner role `kms:Sign` on every `oauth2_kms_jwt` key, and
`bedrock:InvokeModel`/`Converse` on every Bedrock model in the config.
`bearer_env` tokens are read from the environment of whoever runs the request. In Fargate
mode that is the task, which has no such variable, so use `runner.mode: local` for those.
