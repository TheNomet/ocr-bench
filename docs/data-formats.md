# Data formats

All stages communicate through files. This document is the contract between the
generator (`ocrbench.docgen`), the runners (`ocrbench.transcribe`, `ocrbench.extract`),
the scorer (`ocrbench.score`) and the viewer (`ocrbench.report`).

Paths are relative to a **docs directory** (default `docs-out/`) or a **run directory**
(default `results/<tag>/`). Everything is UTF-8; JSON files are pretty-printed; JSONL has
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

## 2. Transcription runs (`results/<tag>/`)

```
results/<tag>/
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
results/<tag>/
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

- `results/<tag>/scores.json`: written by `ocrbench report`. It holds the per-page
  transcription metrics, per-document field metrics, and aggregates.
- `site/<tag>/index.html`: the static viewer, also written by `ocrbench report`. It copies
  the images it shows, so the folder is self-contained.
