# ocr-bench run `2026-10-09-repeat-plus-claude55`



## Key facts (computed, not LLM-written)

| question | clean pages | degraded pages |
|---|---|---|
| most accurate pipeline | page images → Claude Haiku 5.5 (`image->haiku-5.5`): 100.0% | page images → Claude Sonnet 5.5 (`image->sonnet-5.5`): 99.1% |
| cheapest pipeline (among priced, see note) | – | – |
| fastest pipeline | page images → Claude Haiku 5.5 (`image->haiku-5.5`): 1.5 s/doc | page images → Claude Sonnet 5.5 (`image->sonnet-5.5`): 1.7 s/doc |

**Cost note:** no cost could be computed for `image->haiku-5.5`, `image->sonnet-5.5`, `unlimited-ocr->sonnet-5.5` (no price configured, or no successful request reported its token usage), so they are left out of the cheapest-pipeline comparison. Set `price_per_mtok` for those backends to include them.

Every pipeline (field accuracy, time per document, cost per 1,000 documents):

| pipeline | clean accuracy | degraded accuracy | s/doc (clean) | $/1k docs (clean) |
|---|---|---|---|---|
| page images → Claude Haiku 5.5 (`image->haiku-5.5`) | 100.0% | 98.2% | 1.5 | – |
| page images → Claude Sonnet 5.5 (`image->sonnet-5.5`) | 99.7% | 99.1% | 2.1 | – |
| Unlimited-OCR text → Claude Sonnet 5.5 (`unlimited-ocr->sonnet-5.5`) | 95.1% | 78.7% | 8.1 | – |

| reader | variant | CER median | number recall | invented numbers (excl. runaway) | runaway pages |
|---|---|---|---|---|---|
| `haiku-5.5` | clean | 0.000 | 100.0% | 0 | 0 |
| `haiku-5.5` | degraded | 0.001 | 98.5% | 7 | 0 |
| `sonnet-5.5` | clean | 0.000 | 100.0% | 0 | 0 |
| `sonnet-5.5` | degraded | 0.001 | 99.3% | 3 | 0 |
| `unlimited-ocr` | clean | 0.006 | 99.5% | 1 | 0 |
| `unlimited-ocr` | degraded | 0.043 | 90.8% | 85 | 0 |

| reader | p50 s/page (one at a time) | max pages/min (at concurrency) |
|---|---|---|
| `unlimited-ocr` | 6.1 | 29 (16) |
| `sonnet-5.5` | 5.3 | 175 (16) |
| `haiku-5.5` | 4.3 | 136 (16) |

`unlimited-ocr` runs on its own GPU (ml.g5.xlarge), which costs $1.49/hour whether busy or idle; it handled at most ~1729 pages/hour in this run.

Pipeline costs that include `unlimited-ocr` use $0.86/1k pages: the cost at its best sustained throughput: 28.8 pages/min at concurrency 16 (GPU kept busy). One request at a time it would be $2.56/1k pages.


Fields missed most often (count over clean + degraded documents):

- page images → Claude Haiku 5.5 (`image->haiku-5.5`): `newsletter.new_fee` (3), `receipt.merchant` (1), `rental_contract.monthly_rent` (1), `rental_contract.deposit` (1)
- page images → Claude Sonnet 5.5 (`image->sonnet-5.5`): `newsletter.new_fee` (3), `rental_contract.monthly_rent` (1)
- Unlimited-OCR text → Claude Sonnet 5.5 (`unlimited-ocr->sonnet-5.5`): `holiday_survey.activities` (9), `receipt.merchant` (8), `holiday_survey.accommodation` (6), `holiday_survey.pets` (6)

## Recommendation

*LLM comment by opus-5.5 (anthropic.claude-opus-5-5) on the computed key facts. It interprets the key facts above.*

## Bottom line
Choose `image->sonnet-5.5` for production. It is the most accurate on degraded pages (99.1%) and nearly perfect on clean pages (99.7%), so it holds up best on real-world phone photos. `image->haiku-5.5` is a close alternative if clean scans dominate.

## What the facts say
- **Reading images directly beats transcribing first.** Both image pipelines stay near-perfect. `unlimited-ocr->sonnet-5.5` drops to 95.1% on clean pages and 78.7% on degraded pages, because errors in the OCR text cannot be recovered by the extractor.
- **The self-hosted reader degrades badly on photos.** `unlimited-ocr` falls to 90.8% number recall and produces 85 invented numbers on degraded pages. The Claude readers produce single-digit counts.
- **The OCR pipeline's misses are mostly on form-style fields.** `holiday_survey.activities`, `holiday_survey.accommodation` and `holiday_survey.pets` are probably checkbox or selection items. This suggests the transcript loses marks and layout. Its misses on `receipt.merchant` may come from logos or stylised headers.
- **The image pipelines share one blind spot.** Both miss `newsletter.new_fee` (3 each), which points to an ambiguous field (e.g. old vs. new fee) rather than a reading problem.
- **No runaway pages occurred for any reader**, so the failures are degradation and hallucination, not runaway output.
- **Self-hosting does not help here.** `unlimited-ocr` is slower per page (6.1 s p50) and much lower in throughput (29 vs. 175 pages/min for `sonnet-5.5`). Its GPU costs $1.49/hour even when idle.

## Choice by goal
| Goal | Choice | Why |
|---|---|---|
| Highest accuracy | `image->sonnet-5.5` | Most accurate on degraded pages, near-perfect on clean |
| Lowest latency | `image->haiku-5.5` (clean); `image->sonnet-5.5` (degraded) | Fastest on clean at 1.5 s/doc; fastest on degraded at 1.7 s/doc |
| Lowest cost | Unknown: no pipeline can be named cheapest | No price is configured for any pipeline. Set `price_per_mtok` to compare. The `unlimited-ocr` step's $0.86/1k pages excludes the Sonnet extraction cost. |
| Checking / second opinion | `image->haiku-5.5` cross-checking `image->sonnet-5.5` | Independent, accurate model; flag disagreements, especially on `newsletter.new_fee` |
| Self-host a model | Not on this evidence | Lower accuracy, more invented numbers, lower throughput. Revisit only if data-residency rules forbid hosted APIs, and verify on your own documents first. |

## Caveats
- **Synthetic sample.** The data is 48 synthetic Norwegian documents with simulated degradation. Real scans, handwriting and other languages may behave differently.
- **Small sample.** Differences between the two image pipelines amount to a handful of fields and may not be meaningful.
- **Untested combination.** Both Claude models ran at temperature 1, and `unlimited-ocr` was tested only with a Sonnet extractor.
- **Cost conclusions are impossible until prices are configured.** GPU economics also depend on keeping the instance busy: one request at a time it would be $2.56/1k pages.

## Setup

### Models

| role | name in tables | model | model id | served by | temperature | price |
|---|---|---|---|---|---|---|
| transcriber (self-hosted) | `unlimited-ocr` | baidu/Unlimited-OCR @ 07dea832 | `baidu/Unlimited-OCR` | vLLM on SageMaker real-time endpoint | 0 | $1.49/hour (on-demand) |
| transcriber, extractor | `sonnet-5.5` | Claude Sonnet 5.5 | `anthropic.claude-sonnet-5-5` | OpenAI-compatible gateway | 1 | – |
| transcriber, extractor | `haiku-5.5` | Claude Haiku 5.5 | `anthropic.claude-haiku-5-5` | OpenAI-compatible gateway | 1 | – |
| recommendation writer | `opus-5.5` | Claude Opus 5.5 | `anthropic.claude-opus-5-5` | OpenAI-compatible gateway | 1 | – |

### The extractors

Extractors: Claude Sonnet 5.5 (`sonnet-5.5`: `anthropic.claude-sonnet-5-5`, temperature 1), Claude Haiku 5.5 (`haiku-5.5`: `anthropic.claude-haiku-5-5`, temperature 1). Each pipeline names its extractor after the arrow. The extractor gets the document type's field list (name, type, one-line description), plus either the page images or the text transcript of every page, in order, and returns one JSON object with the field values copied verbatim; max 2,048 output tokens. It never sees the ground truth.

Models with temperature above 0 sample their output, so their results vary slightly between runs; differences of a point or two are within noise.

Also re-run as a reproducibility check, shown only in *Compare runs* on the main page: `haiku-4.5`, `sonnet-4.5`.

### GPU

| | |
|---|---|
| instance | `ml.g5.xlarge` (picked from pools: ml.g6.xlarge, ml.g6e.xlarge, ml.g5.xlarge, ml.g5.2xlarge) |
| GPU | NVIDIA A10G, 24 GB |
| host | 4 vCPU, 16 GB RAM |
| price | $1.49/hour (on-demand), billed while the endpoint exists, busy or idle |
| region | eu-north-1 |
| host image | `al2023-ami-sagemaker-inference-gpu-4-1` |
| container | `docker.io/vllm/vllm-openai:unlimited-ocr` (digest `sha256:f86770951a9d`) |
| serving flags | `--trust-remote-code --logits_processors vllm.model_executor.models.unlimited_ocr:NGramPerReqLogitsProcessor --no-enable-prefix-caching --mm-processor-cache-gb 0 --max-num-seqs 64 --max-model-len 32768 --gpu-memory-utilization 0.90` |
| post-processing | `strip_grounding` |

### Run

| | |
|---|---|
| plan | `repeat-plus-claude55`: Re-check of the OCR model on the same GPU, and the new Claude Sonnet 5.5 and Haiku 5.5. The Claude 4.5 models were also re-run as a reproducibility check; that comparison is in Compare runs. (7 transcription runs) |
| documents | 48 documents, 54 pages, 108 page images (clean + degraded), seed 7 |
| runner | fargate (2048 CPU units / 4096 MB) |
| code | git `5cdc563` |
| started | 2026-10-09T14:05:02 |

Transcription runs in this plan:

- `unlimited-ocr` at concurrency 1
- `unlimited-ocr` at concurrency 4
- `unlimited-ocr` at concurrency 16
- `sonnet-5.5` at concurrency 1
- `haiku-5.5` at concurrency 1
- `sonnet-5.5` at concurrency 16
- `haiku-5.5` at concurrency 16

## How to read this report

### Stages

- **1. Transcribe**: Every page image (clean and degraded) is sent to each transcriber, at several concurrency levels. This measures how well and how fast each backend reads.
- **2. Extract**: An extractor pulls each document type's fields (totals, dates, names, checked boxes…) out of each pipeline's input: the run's extraction agent, or the model named after the arrow. Scored field by field against the ground truth.
- **3. Score**: Transcripts and fields are compared with the ground truth; latency and cost are aggregated.
- **4. Recommend**: An LLM reads the computed key facts and a short description of the setup, and writes a recommendation. It may not compute numbers. The numbers it writes in digits are checked against the facts, and any not found there are flagged as unverified; the check is automatic and can miss a claim, so the tables remain the reference.

### Pipelines (the arrows)

Pipeline names read `<input> -> <extractor>`. The left side is what the extracting model gets to read: `image` = the page images themselves, anything else = the text transcript produced by that backend in stage 1. The right side is the model that extracts the fields: `agent` = the run's default extraction agent, otherwise the named backend.

| pipeline | what the extractor reads |
|---|---|
| `unlimited-ocr->sonnet-5.5` | Two steps: the self-hosted model `unlimited-ocr` (baidu/Unlimited-OCR @ 07dea832) transcribes every page to text, then the extractor reads that text (not the images) and returns the fields. Extractor: `sonnet-5.5` (Claude Sonnet 5.5). |
| `image->sonnet-5.5` | One step: the extractor reads the page images directly and returns the fields. This is the baseline: what you get without any separate OCR step. Extractor: `sonnet-5.5` (Claude Sonnet 5.5). |
| `image->haiku-5.5` | One step: the extractor reads the page images directly and returns the fields. This is the baseline: what you get without any separate OCR step. Extractor: `haiku-5.5` (Claude Haiku 5.5). |

### Page variants

- **clean**: straight render of the document, as a perfect scan would look
- **degraded**: simulated phone photo: tilted, downscaled, blurred, noisy, warm tint, low-quality JPEG

### Document types

| type | what it is | docs | pages | hard part | fields extracted |
|---|---|---|---|---|---|
| `letter` | Personal letter written by a private person | 6 | 6 | prose paragraphs with amounts and dates inside sentences | sender_name, date, recipient, amounts, phone |
| `receipt` | Shop receipt (kvittering) from a store | 6 | 6 | narrow monospace column, label and amount on opposite sides | merchant, date, time, total, vat, card_last4, item_count |
| `bank_statement` | Monthly bank account statement for a private customer | 6 | 6 | dense 15-26 row table | account_number, period_start, period_end, opening_balance, closing_balance, transaction_count |
| `holiday_survey` | Filled-in holiday preferences questionnaire (ferieundersøkelse) | 6 | 6 | boxed key/value form filled in handwriting fonts, checkboxes | name, destination, date_from, date_to, travellers, budget_per_person, activities, accommodation, pets, signed_date |
| `invoice` | Invoice from a company to a private customer | 6 | 6 | line-item table plus totals block | invoice_number, invoice_date, due_date, kid, account_number, subtotal, vat, total_due |
| `payslip` | Monthly payslip from an employer to an employee | 6 | 6 | earnings/deductions table, several totals | employer, employee, period, gross_pay, tax_withheld, net_pay, payment_date, account_number |
| `rental_contract` | Residential tenancy agreement between a landlord and a tenant (2 pages) | 6 | 12 | two pages of dense numbered clauses | landlord, tenant, property_address, start_date, monthly_rent, deposit, notice_period |
| `newsletter` | Newsletter from a housing co-op board to residents | 6 | 6 | two-column layout with an inline table | issue_date, meeting_date, new_fee, dugnad_date |

All documents are synthetic Norwegian (bokmål) everyday paperwork with fictional names and numbers.

### Metrics

- **CER**: character error rate after removing Markdown/HTML formatting; 0 = perfect, capped at 1 per page (a failed or runaway page). Sensitive to reading order.
- **CER median**: the typical page; the mean also counts each failed or runaway page as 1
- **runaway**: pages where the output was more than 3x the page length (a repetition loop)
- **word recall / precision**: share of the page's words found / share of output words that are on the page; ignores order
- **number recall**: share of the amounts, dates, account numbers etc. on the page that appear in the output
- **invented numbers**: numbers of 3+ characters in the output that are not on the page
- **coverage**: share of pages or documents that got an answer. Failures count against the backend: CER 1 and no numbers found for a page, every field wrong for a document. Cheapest and fastest pipeline only compare pipelines with at least 99% coverage
- **field accuracy**: share of extracted fields equal to the ground truth (case, spaces and a kr/NOK prefix ignored)
- **end-to-end**: per document: transcription time of all its pages (each page's lowest-concurrency transcript) plus the extraction call
- **$/1k**: LLMs: list price x tokens used. Self-hosted models: GPU hourly price / best sustained throughput (fastest run with at most 1% errors, GPU kept busy; if none, the run with the fewest errors, flagged); the cost one request at a time is shown next to it. Unknown token usage = no cost, never $0

## Results

### Speed and cost (stage 1)

| Backend | Conc. | Pages | OK | Pages/min | p50 (s) | p95 (s) | $/1k pages |
|---|---|---|---|---|---|---|---|
| unlimited-ocr | 1 | 108 | 108 | 9.7 | 6.05 | 8.69 | $2.56 |
| unlimited-ocr | 4 | 108 | 108 | 23.9 | 10.18 | 13.56 | $1.04 |
| unlimited-ocr | 16 | 108 | 108 | 28.8 | 32.45 | 40.33 | $0.86 |
| sonnet-5.5 | 1 | 108 | 108 | 11.6 | 5.27 | 7.33 | – |
| haiku-5.5 | 1 | 108 | 108 | 12.3 | 4.27 | 7.88 | – |
| sonnet-5.5 | 16 | 108 | 108 | 174.7 | 4.98 | 7.34 | – |
| haiku-5.5 | 16 | 108 | 108 | 136.3 | 4.08 | 8.58 | – |

### Transcription quality (stage 1)

| Backend | Variant | Pages | Coverage | CER mean | CER median | Runaway | Word recall | Number recall | Numbers missing | Invented numbers |
|---|---|---|---|---|---|---|---|---|---|---|
| haiku-5.5 | clean | 54 | 100.0% | 0.014 | 0.000 | 0 | 100.0% | 100.0% | 0 | 0 |
| haiku-5.5 | degraded | 54 | 100.0% | 0.018 | 0.001 | 0 | 99.4% | 98.5% | 10 | 7 |
| sonnet-5.5 | clean | 54 | 100.0% | 0.019 | 0.000 | 0 | 100.0% | 100.0% | 0 | 0 |
| sonnet-5.5 | degraded | 54 | 100.0% | 0.019 | 0.001 | 0 | 99.4% | 99.3% | 4 | 3 |
| unlimited-ocr | clean | 54 | 100.0% | 0.012 | 0.006 | 0 | 97.3% | 99.5% | 2 | 1 |
| unlimited-ocr | degraded | 54 | 100.0% | 0.075 | 0.043 | 0 | 85.6% | 90.8% | 135 | 85 |

### Field extraction (stage 2)

| Pipeline id | Variant | Docs | Coverage | Field accuracy | Extractor latency (s) | End-to-end (s) | $/1k docs |
|---|---|---|---|---|---|---|---|
| image->haiku-5.5 | clean | 48 | 100.0% | 100.0% | 1.50 | 1.50 | – |
| image->haiku-5.5 | degraded | 48 | 100.0% | 98.2% | 1.88 | 1.88 | – |
| image->sonnet-5.5 | clean | 48 | 100.0% | 99.7% | 2.07 | 2.07 | – |
| image->sonnet-5.5 | degraded | 48 | 100.0% | 99.1% | 1.73 | 1.73 | – |
| unlimited-ocr->sonnet-5.5 | clean | 48 | 100.0% | 95.1% | 1.45 | 8.06 | – |
| unlimited-ocr->sonnet-5.5 | degraded | 48 | 100.0% | 78.7% | 2.12 | 9.40 | – |

Most-missed fields (count over clean + degraded):

- **image->haiku-5.5**: `newsletter.new_fee` (3), `receipt.merchant` (1), `rental_contract.monthly_rent` (1), `rental_contract.deposit` (1)
- **image->sonnet-5.5**: `newsletter.new_fee` (3), `rental_contract.monthly_rent` (1)
- **unlimited-ocr->sonnet-5.5**: `holiday_survey.activities` (9), `receipt.merchant` (8), `holiday_survey.accommodation` (6), `holiday_survey.pets` (6), `holiday_survey.destination` (4)
