# ocr-bench

ocr-bench compares a **self-hosted OCR model** (vLLM on a SageMaker GPU endpoint, by default
[baidu/Unlimited-OCR](https://huggingface.co/baidu/Unlimited-OCR)) with **hosted LLMs**
(Claude through Bedrock or any OpenAI-compatible gateway) on reading documents. It measures
speed, accuracy and cost on synthetic Norwegian documents that come with exact ground truth.

```
docs ─► 1 transcribe every page ─► 2 an agent extracts fields ─► 3 score ─► 4 an LLM writes the findings
        (OCR model, Sonnet, Haiku…)     from each transcript, or straight
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
just run smoke          # quick check of every backend      -> runs/<date>-smoke/
just run suite          # latency + batch sweep, extraction, findings -> runs/<date>-suite/
just open               # experiments index -> one run -> one document
just endpoint-down      # stop GPU billing
just destroy            # remove everything
```

Requirements: an AWS account with SageMaker GPU endpoint quota (`ml.g5`/`ml.g6` family),
`uv`, `just`, Google Chrome (to render the documents), and the AWS CLI with credentials. You
don't need Docker: images are copied and layered registry-side with `crane`.

## What you get

Every run is one self-contained folder, `runs/<date>-<plan>/`. It holds the setup
(`run.json`: models, GPU host and price, documents, plan, git commit), a copy of the
documents, every raw output, `scores.json`, the LLM-written `summary.md`, and `report.md`.
`report.md` puts it all together: findings, setup, a legend of document types and pipelines,
metric definitions and result tables. Runs stay local (and in your S3 bucket); they are
gitignored because they contain deployment-specific names.

`site/index.html` lists every experiment with its models, GPU and cost, and headline
results. From a run page you drill down to each document: page image (clean and degraded),
ground truth, every transcript with a word diff, and the extracted fields marked ✓/✗.

Redo any stage of an old run without starting over, e.g. `just rerun <id> summarize` or
`just rerun <id> extract,score,summarize`.

## Documentation

| | |
|---|---|
| [AGENTS.md](AGENTS.md) | Orientation for coding agents and new contributors |
| [docs/architecture.md](docs/architecture.md) | Components, data flow, and why things are built this way |
| [docs/configuration.md](docs/configuration.md) | Every config key |
| [docs/data-formats.md](docs/data-formats.md) | File contracts between stages |
| [docs/documents.md](docs/documents.md) | The synthetic document types and their fields |

## Without AWS GPU access

Set `ocr.direct_url` to any running vLLM server (for example a local GPU box) and
`runner.mode: local`. `just run` then calls everything from your machine and nothing needs
deploying. The LLM backends work the same way.

## License

MIT. All generated documents are fictional.
