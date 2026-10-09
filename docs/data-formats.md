# Data formats

All stages communicate through files. This document is the contract between the
generator (`ocrbench.docgen`), the runners (`ocrbench.transcribe`, `ocrbench.extract`),
the scorer (`ocrbench.score`) and the viewer (`ocrbench.report`).

Paths are relative to a **docs directory** (default `docs-out/`) or a **run directory**
(default `runs/<id>/`). Everything is UTF-8; JSON files are pretty-printed; JSONL has
one object per line.

## 1. Generated documents (`docs-out/`)

```
docs-out/
  manifest.json
  schemas.json
  <doc_id>/
    doc.pdf                 # the rendered source document (1..n pages)
    doc.html                # the HTML it was printed from
    p1.png                  # page 1, clean render
    p1.degraded.jpg         # page 1, simulated phone photo / bad scan
    p1.gt.txt               # ground-truth text of page 1, reading order, one block per line
    p1.gt.json              # {"numbers": [...]}: numeric strings that appear on page 1
    p2.png ...              # further pages for multi-page documents
    fields.json             # ground-truth structured fields for the whole document
```

`doc_id` = `<type>-<index:02d>`, e.g. `invoice-03`.

### manifest.json

```json
[
  {
    "id": "invoice-03",
    "type": "invoice",
    "title": "Faktura nr. 48213",
    "language": "nb",
    "pages": [
      {"n": 1, "clean": "invoice-03/p1.png", "degraded": "invoice-03/p1.degraded.jpg",
       "gt": "invoice-03/p1.gt.txt", "gt_meta": "invoice-03/p1.gt.json",
       "width": 1190, "height": 1684}
    ],
    "fields": "invoice-03/fields.json",
    "pdf": "invoice-03/doc.pdf"
  }
]
```

### fields.json

```json
{"type": "invoice", "fields": {"invoice_number": "48213", "total_due": "15 937,50", "due_date": "14.06.2026"}}
```

Field values are **strings exactly as printed on the document**. List-valued fields (such as
checked boxes) are JSON arrays of strings. Missing or not-applicable fields are omitted.

### schemas.json

These are the per-type extraction schemas given to the extraction agent:

```json
{"invoice": {"description": "Invoice from a company to a private customer",
             "fields": {"invoice_number": {"type": "string", "description": "Invoice number"},
                        "selected_options": {"type": "list", "description": "..."}}}}
```

## 2. Run folder (`runs/<id>/`)

`<id>` is `<YYYY-MM-DD>-<plan>`, with `-2`, `-3`, … appended for repeats on the same day.

```
runs/<id>/
  run.json                  setup + stage timings (below)
  config.yaml               snapshot of the config used
  docs/                     copy of the document set (section 1 layout)
  runs.jsonl transcripts.jsonl extractions.jsonl outputs/     (sections 2-3)
  scores.json               stage 3 (section 4)
  summary.md                stage 4: LLM-written findings; first line is an HTML comment naming the model
  report.md                 findings + setup + legend + result tables
```

### run.json

```json
{"id": "2026-10-09-suite", "plan": "suite", "plan_items": [{"backend": "ocr", "concurrency": 1}],
 "created": "...", "git_commit": "90b8e41", "region": "eu-west-1",
 "runner": {"mode": "fargate", "cpu": 2048, "memory": 4096},
 "documents": {"count": 48, "pages": 54, "page_images": 108, "seed": 7, "per_type": 6,
               "types": {"invoice": {"docs": 6, "pages": 6, "description": "...", "fields": ["..."]}}},
 "ocr": {"hf_repo": "...", "hf_revision": "...", "instance_type": "ml.g5.xlarge", "gpu": "NVIDIA A10G, 24 GB",
         "host": "4 vCPU, 16 GB RAM", "price_per_hour_usd": 1.49, "image_digest": "sha256:...", "...": "..."},
 "llm_backends": {"bedrock-sonnet": {"provider": "bedrock", "model": "...", "via": "AWS Bedrock",
                                     "price_per_mtok": {"input": 3.0, "output": 15.0}}},
 "transcribers": ["ocr", "bedrock-sonnet"], "extraction": {"agent": "...", "pipelines": ["..."]},
 "summary": {"agent": "..."},
 "stages": {"transcribe": {"finished": "...", "seconds": 3600.0}}}
```

`llm_backends` holds model identity and price only: never hosts, auth or credentials.

## 2a. Transcription records (inside the run folder)

```
runs/<id>/
  runs.jsonl                # one line per run (backend x concurrency)
  transcripts.jsonl         # one line per request
  outputs/<backend>/<doc_id>.p<n>.<variant>.r<run_id>.md   # raw model output
```

`variant` is either `clean` or `degraded`.

### runs.jsonl

```json
{"run_id": "20261008-141236", "backend": "ocr", "model": "baidu/Unlimited-OCR@sagemaker:x",
 "concurrency": 16, "pages": 96, "wall_s": 120.4, "started": 1791462025.1}
```

### transcripts.jsonl

```json
{"run_id": "...", "backend": "ocr", "model": "...", "doc_id": "invoice-03", "page": 1,
 "variant": "clean", "concurrency": 16, "started": 1791462025.2, "latency_s": 3.1, "ok": true,
 "prompt_tokens": 1192, "completion_tokens": 490, "finish_reason": "stop", "retries": 0,
 "error": null, "output_file": "outputs/ocr/invoice-03.p1.clean.r20261008-141236.md"}
```

## 3. Extraction runs (same run directory)

```
runs/<id>/
  extractions.jsonl
  outputs/extract/<pipeline_slug>/<doc_id>.<variant>.json   # parsed fields returned by the agent
```

`pipeline` is `"<source>->agent"`, where source is a transcriber backend name, or `"image"`
for direct extraction from the page images. In file names, `->` becomes `__`.

### extractions.jsonl

```json
{"pipeline": "ocr->agent", "source": "ocr", "agent": "bedrock-sonnet", "agent_model": "...",
 "doc_id": "invoice-03", "variant": "degraded", "latency_s": 4.2, "ok": true,
 "prompt_tokens": 2100, "completion_tokens": 160, "error": null,
 "output_file": "outputs/extract/ocr__agent/invoice-03.degraded.json"}
```

When the source is a transcriber, the agent reads that transcriber's **first successful**
transcript of every page of the document, in page order. The source's transcription latency
is **not** included in `latency_s`; the report adds the two together for end-to-end figures.

## 4. Scores and site

- `runs/<id>/scores.json`: written by the score stage and by `ocrbench report`. It holds the per-page
  transcription metrics, per-document field metrics, and aggregates.
- `site/index.html`: the experiments index. `site/<id>/index.html` is the run page and
  `site/<id>/docs/<doc_id>.html` the document pages. Written by `ocrbench report`, which
  copies the images, so each `site/<id>/` folder is self-contained.
