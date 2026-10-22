# ocr-bench

ocr-bench compares **self-hosted OCR models** (vLLM on SageMaker GPU endpoints, by default
[baidu/Unlimited-OCR](https://huggingface.co/baidu/Unlimited-OCR)) with **hosted LLMs**
(Claude through Bedrock or any OpenAI-compatible gateway) on reading documents. It measures
speed, accuracy and cost on synthetic Norwegian documents that come with exact ground truth.

```
docs ─► 1 transcribe every page ─► 2 an LLM extracts fields ─► 3 score ─► 4 an LLM writes the recommendation
        (OCR models, Sonnet, Haiku…)    from each transcript, or straight
                                        from the images (baseline)
```

The **whole stack** (bucket, image repos, IAM, SageMaker model, Fargate runner, optionally a
VPC) is deployed from one config file and removed with one command.

**Terms** used in the config, reports and code:

- **stages**: `transcribe`, `extract`, `score`, `summarize`. `summarize` writes the
  recommendation (`summary.md` / `summary.json`) with the backend in `bench.summary.agent`.
- **extractor**: any model that extracts the fields in stage 2.
- **the agent**: the default extractor, `bench.extraction.agent`.
- **pipeline** `<source>-><extractor>`: the source is a transcriber backend (its transcript is
  the input) or `image` (the page images go straight to the extractor); the extractor is `agent`
  or a backend name. Examples: `unlimited-ocr->agent`, `image->haiku-4.5`.

## Quick start

### Look at the results locally (no AWS)

```bash
CONFIG=config/example.yaml just report && just open
```

Needs only `uv` and `just`; nothing is deployed or called. `just report` re-scores the committed
runs and regenerates their `runs/*/report.md` and `runs/*/scores.json` (unchanged unless the
scoring or report code changed), then builds `site/`. `just open` opens `site/index.html`.

### Full stack

```bash
cp config/example.yaml config/mine.yaml   # edit: account, region, network, backends
export CONFIG=config/mine.yaml AWS_PROFILE=...
just tools setup        # terraform + crane into .tools/, python env, git hooks
just deploy             # infra -> images -> weights -> SageMaker model   (~20 min, mostly uploads)
just endpoint-up        # GPU endpoint                                    (~15 min cold start)
just docs               # 8 types x 6 docs: 48 docs, 54 pages, clean + degraded = 108 page images
just run smoke          # quick check of every backend      -> runs/<date>-smoke/
just run suite          # latency + batch sweep, extraction, recommendation -> runs/<date>-suite/
                        # (name plans after what they test: runs are called <date>-<plan>)
just open               # experiments index -> one run -> one document
just endpoint-down      # stop GPU billing
just destroy            # remove everything
```

Other recipes: `just runs` (list runs), `just fetch <id>` (pull a Fargate run from S3),
`just report [<id>]` (re-score, rebuild reports and site), `just endpoint-status`, `just endpoint-logs [<name>]`, `just probe`
(which model ids a gateway accepts; `--temperature 0 --temperature 1` also tries those temperatures), `just lint`;
`just --list` shows all of them.

### Without a GPU: Bedrock only, nothing to deploy

```bash
cp config/example.yaml config/mine.yaml   # set aws.account_id/region; set runner.mode: local
export CONFIG=config/mine.yaml AWS_PROFILE=...
just setup              # python env, git hooks
just docs               # needs Google Chrome
just run bedrock-only   # Sonnet 4.5 + Haiku 4.5 transcribe; extraction from text and from images
just open
```

This runs everything from your machine and creates no AWS resources. Your credentials need
`bedrock:InvokeModel` on the two Claude 4.5 models in the region (enable model access in the
Bedrock console first). The example uses `eu.` inference profiles for `eu-west-1`; use `us.`,
`apac.` or `global.` to match your region. Pipelines that read the self-hosted model's
transcript are skipped, because this plan doesn't run it.

### Requirements for the full stack

An AWS account with SageMaker GPU endpoint quota (`ml.g5`/`ml.g6` family),
`uv`, `just`, Google Chrome (to render the documents), and the AWS CLI with credentials. You
don't need Docker: images are copied and layered registry-side with `crane`.

Cost while idle: `just endpoint-down` stops the GPU, but with `network.mode: create` the
stack's NAT gateway bills (~$32/month plus data processing) until `just destroy`.

## What you get

Every run is one self-contained folder, `runs/<date>-<plan>/`, committed to this repo. It holds the setup
(`run.json`: models, GPU host and price, documents, plan, git commit), a copy of the
documents, every raw output, `scores.json`, the LLM-written `summary.md`, and `report.md`.
`report.md` puts it all together: key facts, recommendation, setup, a legend of document types and pipelines,
metric definitions and result tables. The one exception is each run's `config.yaml`
snapshot: it holds deployment details (account, endpoints, client ids), so it stays local
and is gitignored.

**Published results:** <https://thenomet.github.io/ocr-bench/> (updated with `just publish`).

`site/index.html` explains the benchmark for a newcomer, compares runs, and lists every experiment with its models, GPU and cost, and headline
results. From a run page you drill down to each document: page image (clean and degraded),
ground truth, every transcript with a word diff, and the extracted fields marked ✓/✗.

Redo any stage of an old run without starting over, e.g. `just rerun <id> summarize` or
`just rerun <id> extract,score,summarize`. A price that wasn't known when a run was made can be
added later: set `price_per_mtok` in the config, then `just fill-prices [<id>]` (only fills prices the
run left empty, for the same model) and `just report`. How pages, fields, latency and cost are scored:
[AGENTS.md](AGENTS.md#how-results-are-scored-dont-change-silently).

## Documentation

| | |
|---|---|
| [AGENTS.md](AGENTS.md) | Orientation for coding agents and new contributors |
| [docs/architecture.md](docs/architecture.md) | Components, data flow, and why things are built this way |
| [docs/configuration.md](docs/configuration.md) | Every config key |
| [docs/data-formats.md](docs/data-formats.md) | File contracts between stages |
| [docs/documents.md](docs/documents.md) | The synthetic document types and their fields |

## Naming backends

Backend keys (`backends.<name>`) appear in every record, report and the public site, so name
them after the model, not the provider or your organisation: `unlimited-ocr`, `sonnet-4.5`,
`opus-5.5`, as in `config/example.yaml`. You can add several self-hosted models; each gets its
own endpoint. If you renamed keys after a run, `scripts/rename_backends.py` rewrites old runs
consistently (see [Older configs](docs/configuration.md#older-configs)). The rules behind this
are in [AGENTS.md](AGENTS.md#rules).

## Publishing

`just publish` regenerates every run's `report.md` and `scores.json` and the site, and refuses
to go on if that leaves uncommitted changes in `runs/` (commit them first, so the published
site matches the repo). It then scans every file with the public guard, force-pushes the site to
the `gh-pages` branch and starts the `pages` workflow. Pages must use "GitHub Actions" as its
source.

## Your own vLLM server

Set `direct_url` on a self-hosted backend to any running vLLM server (for example a local GPU box) and
`runner.mode: local`. `just run` then calls everything from your machine and nothing needs
deploying. The LLM backends work the same way.

## License

MIT. All generated documents are fictional.
