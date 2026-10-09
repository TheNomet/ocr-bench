# ocr-bench

ocr-bench compares a **self-hosted OCR model** (vLLM on a SageMaker GPU endpoint, by default
[baidu/Unlimited-OCR](https://huggingface.co/baidu/Unlimited-OCR)) with **hosted LLMs**
(Claude through Bedrock or any OpenAI-compatible gateway) on reading documents. It measures
speed, accuracy and cost on synthetic Norwegian documents that come with exact ground truth.

```
docs ──► stage 1: transcribe every page ──► stage 2: an agent extracts fields ──► score ──► report + static site
         (OCR model, Sonnet, Haiku, …)       from each transcript, or straight
                                             from the images (baseline)
```

The **whole stack** (bucket, image repos, IAM, SageMaker model, Fargate runner, optionally a
VPC) is deployed from one config file and removed with one command.

## Quick start

```bash
cp config/example.yaml config/mine.yaml   # edit: account, region, network, backends
export CONFIG=config/mine.yaml AWS_PROFILE=...
just tools setup        # terraform + crane into .tools/, python env, git hooks
just deploy             # infra -> images -> weights -> SageMaker model   (~20 min, mostly uploads)
just endpoint-up        # GPU endpoint                                    (~15 min cold start)
just docs               # 8 types x 6 docs, clean + degraded              (~100 pages)
just run smoke          # quick check of every backend
just run suite          # latency + batch sweep, then field extraction
just report suite && just open suite
just endpoint-down      # stop GPU billing
just destroy            # remove everything
```

Requirements: an AWS account with SageMaker GPU endpoint quota (`ml.g5`/`ml.g6` family),
`uv`, `just`, Google Chrome (to render the documents), and the AWS CLI with credentials. You
don't need Docker: images are copied and layered registry-side with `crane`.

## What you get

- `results/<tag>/report.md`: speed, transcription quality, field-extraction accuracy, and
  cost tables.
- `site/<tag>/index.html`: a static viewer. For each document it shows the page image
  (clean and degraded), the ground truth, every backend's transcript with a word diff, and
  the extracted fields marked ✓/✗.
- `results/<tag>/*.jsonl`: raw per-request records. See [docs/data-formats.md](docs/data-formats.md).

## Documentation

| | |
|---|---|
| [AGENTS.md](AGENTS.md) | Orientation for coding agents and new contributors |
| [docs/architecture.md](docs/architecture.md) | Components, data flow, and why things are built this way |
| [docs/configuration.md](docs/configuration.md) | Every config key |
| [docs/data-formats.md](docs/data-formats.md) | File contracts between stages |
| [docs/documents.md](docs/documents.md) | The synthetic document types and their fields |
| [docs/findings.md](docs/findings.md) | Results and operational lessons from past runs |

## Without AWS GPU access

Set `ocr.direct_url` to any running vLLM server (for example a local GPU box) and
`runner.mode: local`. `just run` then calls everything from your machine and nothing needs
deploying. The LLM backends work the same way.

## License

MIT. All generated documents are fictional.
