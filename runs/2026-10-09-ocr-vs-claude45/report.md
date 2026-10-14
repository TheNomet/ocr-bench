# ocr-bench run `2026-10-09-ocr-vs-claude45`



## Key facts (computed, not LLM-written)

| question | clean pages | degraded pages |
|---|---|---|
| most accurate pipeline | page images → Claude Sonnet 4.5 (2025-09-29) (`image->agent`): 97.0% | page images → Claude Sonnet 4.5 (2025-09-29) (`image->agent`): 83.0% |
| cheapest pipeline | Unlimited-OCR text → Claude Sonnet 4.5 (2025-09-29) (`unlimited-ocr->agent`): $5.87/1k docs | Unlimited-OCR text → Claude Sonnet 4.5 (2025-09-29) (`unlimited-ocr->agent`): $5.88/1k docs |
| fastest pipeline | page images → Claude Sonnet 4.5 (2025-09-29) (`image->agent`): 3.4 s/doc | page images → Claude Sonnet 4.5 (2025-09-29) (`image->agent`): 3.6 s/doc |

Every pipeline (field accuracy, time per document, cost per 1,000 documents):

| pipeline | clean accuracy | degraded accuracy | s/doc (clean) | $/1k docs (clean) |
|---|---|---|---|---|
| page images → Claude Sonnet 4.5 (2025-09-29) (`image->agent`) | 97.0% | 83.0% | 3.4 | $7.44 |
| Unlimited-OCR text → Claude Sonnet 4.5 (2025-09-29) (`unlimited-ocr->agent`) | 95.7% | 81.5% | 9.0 | $5.87 |

| reader | variant | CER median | number recall | invented numbers (excl. runaway) | runaway pages |
|---|---|---|---|---|---|
| `haiku-4.5` | clean | 0.033 | 99.3% | 3 | 0 |
| `haiku-4.5` | degraded | 0.081 | 87.1% | 124 | 1 |
| `sonnet-4.5` | clean | 0.002 | 99.8% | 2 | 0 |
| `sonnet-4.5` | degraded | 0.079 | 87.8% | 132 | 1 |
| `unlimited-ocr` | clean | 0.006 | 99.5% | 1 | 0 |
| `unlimited-ocr` | degraded | 0.043 | 90.8% | 85 | 0 |

| reader | p50 s/page (one at a time) | max pages/min (at concurrency) |
|---|---|---|
| `unlimited-ocr` | 6.2 | 28 (32) |
| `sonnet-4.5` | 14.1 | 43 (16) |
| `haiku-4.5` | 8.0 | 137 (32) |

`unlimited-ocr` runs on its own GPU (ml.g5.xlarge), which costs $1.49/hour whether busy or idle; it handled at most ~1686 pages/hour in this run.

Pipeline costs that include `unlimited-ocr` use $0.89/1k pages: the cost at its best sustained throughput: 28.1 pages/min at concurrency 32 (GPU kept busy). One request at a time it would be $2.64/1k pages.

- `unlimited-ocr` is cheaper than `sonnet-4.5` transcription ($15.16/1k pages) above **~99 pages/hour** sustained
- `unlimited-ocr` is cheaper than `haiku-4.5` transcription ($5.29/1k pages) above **~282 pages/hour** sustained

Fields missed most often (count over clean + degraded documents):

- page images → Claude Sonnet 4.5 (2025-09-29) (`image->agent`): `newsletter.new_fee` (10), `rental_contract.property_address` (6), `holiday_survey.activities` (5), `holiday_survey.signed_date` (5)
- Unlimited-OCR text → Claude Sonnet 4.5 (2025-09-29) (`unlimited-ocr->agent`): `holiday_survey.activities` (9), `receipt.merchant` (7), `holiday_survey.accommodation` (5), `holiday_survey.pets` (5)

## Recommendation

*LLM comment by opus-5.5 (anthropic.claude-opus-5-5) on the computed key facts. It interprets the key facts above.*

## Bottom line
Choose `image->agent` for production extraction: it is the most accurate pipeline on both clean (97.0%) and degraded (83.0%) pages and also the fastest. `unlimited-ocr->agent` is the cheapest at $5.87/1k docs, but only when its GPU is kept busy, and it gives up some accuracy.

## What the facts say
- Both pipelines use the same extractor (Claude Sonnet 4.5), so the accuracy gap comes from the input. Sonnet 4.5 reading the page images beats it reading the `unlimited-ocr` transcript, even though `unlimited-ocr` is a strong reader.
- As a reader, `unlimited-ocr` does best on degraded pages. It has the lowest CER median (0.043), the highest number recall (90.8%), the fewest invented numbers (85) and 0 runaway pages. `haiku-4.5` and `sonnet-4.5` each had 1 runaway page. Better transcription still did not produce better extraction.
- The fields `unlimited-ocr->agent` misses most (`holiday_survey.activities`, `holiday_survey.accommodation`, `holiday_survey.pets`, `receipt.merchant`) look like layout or visual content. Checkboxes and logos tend to get lost when a page is flattened to text. `image->agent` misses different fields (`newsletter.new_fee`, `rental_contract.property_address`), so the 2 pipelines fail in different places.
- Degraded pages hurt every pipeline and every reader, and invented numbers rise sharply for all readers. Photo quality is the biggest risk to accuracy.
- The self-hosted model's cost advantage depends on load. The GPU costs $1.49/hour busy or idle. Its rate is $0.89/1k pages at full throughput but $2.64/1k pages one request at a time. It also adds latency: `unlimited-ocr->agent` takes 9.0 s/doc against 3.4 s/doc for `image->agent`.

## Choice by goal
| goal | choice | why |
|---|---|---|
| highest accuracy | `image->agent` | Most accurate on clean and degraded pages |
| lowest latency | `image->agent` | Fastest pipeline (3.4 s/doc clean, 3.6 s/doc degraded) |
| lowest cost | `unlimited-ocr->agent` | Cheapest ($5.87/1k docs clean), assuming the GPU stays busy |
| checking/second opinion | `unlimited-ocr->agent` | Different miss pattern and fewest invented numbers in its transcripts, so it is useful for cross-checking amounts |
| when to self-host | `unlimited-ocr` | Only with high sustained volume: it beats `haiku-4.5` transcription above ~282 pages/hour and `sonnet-4.5` transcription above ~99 pages/hour. It is capped at ~1686 pages/hour on this instance. |

## Caveats
- The data is 48 synthetic Norwegian documents across 8 types. Real documents, other languages and other layouts may behave differently.
- The degraded pages are simulated phone photos. Real capture problems such as glare, folds or handwriting may hit the 2 inputs differently.
- Costs assume the stated prices and, for `unlimited-ocr`, a fully used ml.g5.xlarge. Idle time or a different instance changes the cost ranking.
- `sonnet-4.5->agent` was left out of the key facts because one model both transcribed and extracted. The comparison therefore covers only 2 pipelines with 1 extractor.

## Setup

### Models

| role | name in tables | model | model id | served by | temperature | price |
|---|---|---|---|---|---|---|
| transcriber (self-hosted) | `unlimited-ocr` | baidu/Unlimited-OCR @ 07dea832 | `baidu/Unlimited-OCR` | vLLM on SageMaker real-time endpoint | 0 | $1.49/hour (on-demand) |
| transcriber, extraction agent (default) | `sonnet-4.5` | Claude Sonnet 4.5 (2025-09-29) | `anthropic.claude-sonnet-4-5-20250929-v1:0` | OpenAI-compatible gateway | 0 | $3.0 in / $15.0 out per 1M tokens |
| transcriber | `haiku-4.5` | Claude Haiku 4.5 (2025-10-01) | `anthropic.claude-haiku-4-5-20251001-v1:0` | OpenAI-compatible gateway | 0 | $1.0 in / $5.0 out per 1M tokens |
| recommendation writer | `opus-5.5` | Claude Opus 5.5 | `anthropic.claude-opus-5-5` | OpenAI-compatible gateway | 1 | – |

### The extraction agent

The **agent** (default extractor) is Claude Sonnet 4.5 (2025-09-29) (`anthropic.claude-sonnet-4-5-20250929-v1:0`), temperature 0. It gets the document type's field list (name, type, one-line description), plus either the page images (`image->agent`) or the text transcript of every page, in order (`<transcriber>->agent`), and returns one JSON object with the field values copied verbatim; max 2,048 output tokens. It never sees the ground truth. It is the same model in every pipeline.

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
| plan | `ocr-vs-claude45`: First benchmark: self-hosted Unlimited-OCR (GPU) vs Claude Sonnet 4.5 and Haiku 4.5, single requests and batch load (concurrency 1-32). (9 transcription runs) |
| documents | 48 documents, 54 pages, 108 page images (clean + degraded), seed 7 |
| runner | fargate (2048 CPU units / 4096 MB) |
| code | git `f3e57e7 (migrated from results/suite)` |
| started | 2026-10-09T08:58:00 |

Transcription runs in this plan:

- `unlimited-ocr` at concurrency 1
- `sonnet-4.5` at concurrency 1
- `haiku-4.5` at concurrency 1
- `unlimited-ocr` at concurrency 4
- `unlimited-ocr` at concurrency 16
- `unlimited-ocr` at concurrency 32
- `sonnet-4.5` at concurrency 16
- `haiku-4.5` at concurrency 16
- `haiku-4.5` at concurrency 32

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
| `unlimited-ocr->agent` | Two steps: the self-hosted model `unlimited-ocr` (baidu/Unlimited-OCR @ 07dea832) transcribes every page to text, then the extractor reads that text (not the images) and returns the fields. Extractor: `sonnet-4.5` (Claude Sonnet 4.5 (2025-09-29)). |
| `image->agent` | One step: the extraction agent reads the page images directly and returns the fields. This is the baseline: what you get without any separate OCR step. Extractor: `sonnet-4.5` (Claude Sonnet 4.5 (2025-09-29)). |

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
- **coverage**: share of pages or documents that got an answer. Failures, and selected pages or documents never attempted, count against the backend: CER 1 and no numbers found for a page, every field wrong for a document. Cheapest and fastest pipeline only compare pipelines with at least 99% coverage
- **field accuracy**: share of extracted fields equal to the ground truth (case, spaces and a kr/NOK prefix ignored). A value given for an optional field the document doesn't have counts as one wrong field
- **end-to-end**: per document: transcription time of all its pages (each page's lowest-concurrency transcript) plus the extraction call
- **$/1k**: LLMs: list price x tokens used. Self-hosted models: GPU hourly price / best sustained throughput (fastest run with at most 1% errors, GPU kept busy; if none, the run with the fewest errors, flagged); the cost one request at a time is shown next to it. Unknown token usage = no cost, never $0

## Results

### Speed and cost (stage 1)

| Backend | Conc. | Pages | OK | Pages/min | p50 (s) | p95 (s) | $/1k pages |
|---|---|---|---|---|---|---|---|
| unlimited-ocr | 1 | 108 | 108 | 9.4 | 6.23 | 8.94 | $2.64 |
| sonnet-4.5 | 1 | 108 | 108 | 3.7 | 14.09 | 22.86 | $15.68 |
| haiku-4.5 | 1 | 108 | 108 | 7.2 | 7.95 | 12.73 | $4.86 |
| unlimited-ocr | 4 | 108 | 108 | 23.1 | 10.74 | 14.15 | $1.08 |
| unlimited-ocr | 16 | 108 | 107 | 23.3 | 33.43 | 41.44 | $1.07 |
| unlimited-ocr | 32 | 108 | 108 | 28.1 | 65.09 | 76.86 | $0.89 |
| sonnet-4.5 | 16 | 108 | 108 | 43.3 | 14.29 | 22.73 | $14.65 |
| haiku-4.5 | 16 | 108 | 108 | 92.5 | 7.18 | 12.18 | $5.51 |
| haiku-4.5 | 32 | 108 | 108 | 136.7 | 7.02 | 12.16 | $5.51 |

### Transcription quality (stage 1)

| Backend | Variant | Pages | Coverage | CER mean | CER median | Runaway | Word recall | Number recall | Numbers missing | Invented numbers |
|---|---|---|---|---|---|---|---|---|---|---|
| haiku-4.5 | clean | 54 | 100.0% | 0.038 | 0.033 | 0 | 97.5% | 99.3% | 3 | 3 |
| haiku-4.5 | degraded | 54 | 100.0% | 0.110 | 0.081 | 1 | 88.0% | 87.1% | 130 | 124 |
| sonnet-4.5 | clean | 54 | 100.0% | 0.028 | 0.002 | 0 | 99.6% | 99.8% | 1 | 2 |
| sonnet-4.5 | degraded | 54 | 100.0% | 0.160 | 0.079 | 1 | 91.9% | 87.8% | 132 | 410 |
| unlimited-ocr | clean | 54 | 100.0% | 0.012 | 0.006 | 0 | 97.3% | 99.5% | 2 | 1 |
| unlimited-ocr | degraded | 54 | 100.0% | 0.075 | 0.043 | 0 | 85.6% | 90.8% | 135 | 85 |

### Field extraction (stage 2)

| Pipeline id | Variant | Docs | Coverage | Field accuracy | Extractor latency (s) | End-to-end (s) | $/1k docs |
|---|---|---|---|---|---|---|---|
| image->agent | clean | 48 | 100.0% | 97.0% | 3.42 | 3.42 | $7.44 |
| image->agent | degraded | 48 | 100.0% | 83.0% | 3.57 | 3.57 | $6.75 |
| unlimited-ocr->agent | clean | 48 | 100.0% | 95.7% | 2.17 | 8.97 | $5.87 |
| unlimited-ocr->agent | degraded | 48 | 100.0% | 81.5% | 2.18 | 9.68 | $5.88 |

Most-missed fields (count over clean + degraded):

- **image->agent**: `newsletter.new_fee` (10), `rental_contract.property_address` (6), `holiday_survey.activities` (5), `holiday_survey.signed_date` (5), `rental_contract.tenant` (5)
- **unlimited-ocr->agent**: `holiday_survey.activities` (9), `receipt.merchant` (7), `holiday_survey.accommodation` (5), `holiday_survey.pets` (5), `holiday_survey.destination` (4)
