# ocr-bench run `2026-10-09-suite`



## Key facts (computed, not LLM-written)

| question | clean pages | degraded pages |
|---|---|---|
| most accurate pipeline | `image->agent` 97.0% | `image->agent` 83.0% |
| cheapest pipeline | `image->agent` $7.44/1k docs | `image->agent` $6.75/1k docs |
| fastest pipeline | `image->agent` 3.4 s/doc | `image->agent` 3.6 s/doc |

| reader | variant | CER median | number recall | invented numbers (excl. runaway) | runaway pages |
|---|---|---|---|---|---|
| `haiku-4.5` | clean | 0.033 | 99.3% | 3 | 0 |
| `haiku-4.5` | degraded | 0.081 | 87.8% | 120 | 1 |
| `ocr` | clean | 0.006 | 99.5% | 1 | 0 |
| `ocr` | degraded | 0.044 | 92.6% | 82 | 0 |
| `sonnet-4.5` | clean | 0.002 | 99.8% | 2 | 0 |
| `sonnet-4.5` | degraded | 0.079 | 88.3% | 137 | 1 |

| reader | p50 s/page (one at a time) | max pages/min (at concurrency) |
|---|---|---|
| `haiku-4.5` | 8.0 | 137 (32) |
| `sonnet-4.5` | 14.1 | 43 (16) |
| `ocr` | 6.2 | 28 (32) |

OCR transcription costs $1.49/hour whether busy or idle; one GPU handled at most ~1686 pages/hour in this run.

- cheaper than `sonnet-4.5` transcription ($15.68/1k pages) above **~95 pages/hour** sustained
- cheaper than `haiku-4.5` transcription ($4.86/1k pages) above **~307 pages/hour** sustained

Fields missed most often (count over clean + degraded documents):

- `image->agent`: `newsletter.new_fee` (10), `rental_contract.property_address` (6), `holiday_survey.activities` (5), `holiday_survey.signed_date` (5)
- `ocr->agent`: `holiday_survey.activities` (9), `receipt.merchant` (7), `holiday_survey.accommodation` (5), `holiday_survey.pets` (5)

## Findings

*LLM comment by opus-5.5 (anthropic.claude-opus-5-5) on the computed key facts. It interprets the key facts above.*

## Bottom line
Choose `image->agent`. It is the most accurate, cheapest and fastest pipeline on both clean pages (97.0%, $7.44/1k docs, 3.4 s/doc) and degraded pages (83.0%, $6.75/1k docs, 3.6 s/doc). A separate OCR step adds a stage without adding measured value.

## What the facts say
- All pipelines use the same extractor, so the differences come only from the input. Letting the agent see the page images directly beats feeding it a transcript, because transcription can lose layout and visual cues.
- The `ocr->agent` misses cluster on `holiday_survey` fields (`activities`, `accommodation`, `pets`) and on `receipt.merchant`. This suggests that form marks such as checkboxes, and header or logo text, get lost in transcription.
- The `image->agent` misses (`newsletter.new_fee`, `rental_contract.property_address`) look more like interpretation problems than reading problems.
- On degraded pages, `ocr` is the strongest reader:
  - lowest CER median (0.044)
  - highest number recall (92.6%)
  - fewest invented numbers
  - no runaway pages, while `sonnet-4.5` and `haiku-4.5` each had one
- On clean pages, `sonnet-4.5` transcribes best (CER median 0.002). However, every reader is near-perfect on clean pages, so reader choice matters mainly on degraded input.
- Degraded pages are the real weakness of this benchmark. Accuracy drops for the best pipeline, and the LLM readers invent many numbers.

## Recommendation
| Goal | Choice | Why |
|---|---|---|
| Highest accuracy | `image->agent` | Most accurate on clean and degraded pages |
| Lowest latency | `image->agent` | Fastest pipeline; it has no transcription stage |
| Lowest cost | `image->agent` | Cheapest pipeline. `ocr->agent` and `sonnet-4.5->agent` have no pipeline price in the facts, so their cost is unknown, not low |
| Checking / second opinion | `ocr` transcript alongside `image->agent` | Most faithful reader of numbers on degraded pages, with no runaway pages |
| Self-host the OCR model | Only if you need transcripts anyway, at sustained volume | `ocr` costs $1.49/hour busy or idle. It beats `sonnet-4.5` transcription above ~95 pages/hour sustained and `haiku-4.5` above ~307 pages/hour. One GPU handled at most ~1686 pages/hour |

## Caveats
- The data is 48 synthetic Norwegian documents across 8 types, and the "degraded" pages are simulated phone photos. Real scans, handwriting or other languages may change the ranking.
- The key facts do not include accuracy, cost or latency for `ocr->agent` or `sonnet-4.5->agent`, so we cannot say how far behind those pipelines are.
- Results come from one run with fixed model versions (Claude Sonnet 4.5 2025-09-29, `ocr` @ 07dea832) on ml.g5.xlarge. Changes in price, model or hardware could move the break-even points.
- The most-missed field counts are small, so treat the failure patterns as hints rather than established causes.

## Setup

### Models

| role | name in tables | model | model id | served by | temperature | price |
|---|---|---|---|---|---|---|
| transcriber (self-hosted) | `ocr` | baidu/Unlimited-OCR @ 07dea832 | `baidu/Unlimited-OCR` | vLLM on SageMaker real-time endpoint | 0 | $1.49/hour (on-demand) |
| transcriber, extraction agent (default) | `sonnet-4.5` | Claude Sonnet 4.5 (2025-09-29) | `anthropic.claude-sonnet-4-5-20250929-v1:0` | OpenAI-compatible gateway | 0 | $3.0 in / $15.0 out per 1M tokens |
| transcriber | `haiku-4.5` | Claude Haiku 4.5 (2025-10-01) | `anthropic.claude-haiku-4-5-20251001-v1:0` | OpenAI-compatible gateway | 0 | $1.0 in / $5.0 out per 1M tokens |
| summary writer | `opus-5.5` | Claude Opus 5.5 | `anthropic.claude-opus-5-5` | OpenAI-compatible gateway | 1 | – |

### The extraction agent

The **agent** (default extractor) is Claude Sonnet 4.5 (2025-09-29) (`anthropic.claude-sonnet-4-5-20250929-v1:0`), temperature 0. It gets the document type's field list (name, type, one-line description), plus either the page images (`image->agent`) or the text transcript of every page, in order (`<transcriber>->agent`), and returns one JSON object with the field values copied verbatim; max 2,048 output tokens. It never sees the ground truth. It is the same model in every pipeline.

Models with temperature above 0 sample their output, so their results vary slightly between runs; differences of a point or two are within noise.

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

### Run

| | |
|---|---|
| plan | `suite`: First benchmark: self-hosted Unlimited-OCR (GPU) vs Claude Sonnet 4.5 and Haiku 4.5, single requests and batch load (concurrency 1-32). (9 transcription runs) |
| documents | 48 documents, 54 pages, 108 page images (clean + degraded), seed 7 |
| runner | fargate (2048 CPU units / 4096 MB) |
| code | git `f3e57e7 (migrated from results/suite)` |
| started | 2026-10-09T08:58:00 |

Transcription runs in this plan:

- `ocr` at concurrency 1
- `sonnet-4.5` at concurrency 1
- `haiku-4.5` at concurrency 1
- `ocr` at concurrency 4
- `ocr` at concurrency 16
- `ocr` at concurrency 32
- `sonnet-4.5` at concurrency 16
- `haiku-4.5` at concurrency 16
- `haiku-4.5` at concurrency 32

## How to read this report

### Stages

- **1. Transcribe**: Every page image (clean and degraded) is sent to each transcriber, at several concurrency levels. This measures how well and how fast each backend reads.
- **2. Extract**: The extraction agent pulls each document type's fields (totals, dates, names, checked boxes…) out of each pipeline's input. Scored field by field against the ground truth.
- **3. Score**: Transcripts and fields are compared with the ground truth; latency and cost are aggregated.
- **4. Summarise**: An LLM reads the setup and all result tables and writes the findings below. It is told to use only the numbers provided; check them against the tables.

### Pipelines (the arrows)

Pipeline names read `<what produces the input> -> agent`. The left side is what the extraction agent gets to read: `image` = the page images themselves, anything else = the text transcript produced by that backend in stage 1. The agent is the same model in every pipeline, so the pipelines differ only in the input it sees.

| pipeline | what the agent reads |
|---|---|
| `ocr->agent` | Two steps: the self-hosted OCR model (baidu/Unlimited-OCR) transcribes every page to text, then the extraction agent reads that text (not the images) and returns the fields. Extractor: `sonnet-4.5` (Claude Sonnet 4.5 (2025-09-29)). |
| `sonnet-4.5->agent` | Two steps: `sonnet-4.5` transcribes every page to text, then the extraction agent reads that text and returns the fields. Extractor: `sonnet-4.5` (Claude Sonnet 4.5 (2025-09-29)). |
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

- **CER**: character error rate after removing Markdown/HTML formatting; 0 = perfect. Sensitive to reading order.
- **CER median**: the typical page; the mean can be dominated by a single runaway page
- **runaway**: pages where the output was more than 3x the page length (a repetition loop)
- **word recall / precision**: share of the page's words found / share of output words that are on the page; ignores order
- **number recall**: share of the amounts, dates, account numbers etc. on the page that appear in the output
- **invented numbers**: numbers of 3+ characters in the output that are not on the page
- **field accuracy**: share of extracted fields equal to the ground truth (case, spaces and a kr/NOK prefix ignored)
- **end-to-end**: per document: transcription time of all its pages (concurrency-1 runs) plus the agent call
- **$/1k**: LLMs: list price x tokens used. OCR model: GPU hourly price / measured throughput, i.e. with the GPU kept busy

## Results

### Speed and cost (stage 1)

| backend | conc | pages | ok | pages/min | p50 s | p95 s | $ / 1k pages |
|---|---|---|---|---|---|---|---|
| ocr | 1 | 108 | 108 | 9.4 | 6.23 | 8.94 | 2.64 |
| sonnet-4.5 | 1 | 108 | 108 | 3.7 | 14.09 | 22.86 | 15.68 |
| haiku-4.5 | 1 | 108 | 108 | 7.2 | 7.95 | 12.73 | 4.86 |
| ocr | 4 | 108 | 108 | 23.1 | 10.74 | 14.15 | 1.08 |
| ocr | 16 | 108 | 107 | 23.3 | 33.43 | 41.44 | 1.07 |
| ocr | 32 | 108 | 108 | 28.1 | 65.09 | 76.86 | 0.89 |
| sonnet-4.5 | 16 | 108 | 108 | 43.3 | 14.29 | 22.73 | 14.65 |
| haiku-4.5 | 16 | 108 | 108 | 92.5 | 7.18 | 12.18 | 5.51 |
| haiku-4.5 | 32 | 108 | 108 | 136.7 | 7.02 | 12.16 | 5.51 |

### Transcription quality (stage 1)

| backend | variant | pages | CER mean | CER median | runaway | word recall | number recall | missing nums | invented nums |
|---|---|---|---|---|---|---|---|---|---|
| haiku-4.5 | clean | 54 | 0.038 | 0.033 | 0 | 97.5% | 99.3% | 3 | 3 |
| haiku-4.5 | degraded | 54 | 0.290 | 0.081 | 1 | 88.0% | 87.8% | 122 | 120 |
| ocr | clean | 54 | 0.013 | 0.006 | 0 | 97.2% | 99.5% | 2 | 1 |
| ocr | degraded | 54 | 0.077 | 0.044 | 0 | 85.5% | 92.6% | 109 | 82 |
| sonnet-4.5 | clean | 54 | 0.028 | 0.002 | 0 | 99.6% | 99.8% | 1 | 2 |
| sonnet-4.5 | degraded | 54 | 0.496 | 0.079 | 1 | 91.9% | 88.3% | 126 | 413 |

### Field extraction (stage 2)

| pipeline | variant | docs | field accuracy | agent s | end-to-end s | $ / 1k docs |
|---|---|---|---|---|---|---|
| image->agent | clean | 48 | 97.0% | 3.42 | 3.42 | 7.44 |
| image->agent | degraded | 48 | 83.0% | 3.57 | 3.57 | 6.75 |
| ocr->agent | clean | 48 | 95.7% | 2.17 | 8.97 | 7.85 |
| ocr->agent | degraded | 48 | 81.5% | 2.18 | 9.68 | 7.85 |

Most-missed fields (count over clean + degraded):

- **image->agent**: `newsletter.new_fee` (10), `rental_contract.property_address` (6), `holiday_survey.activities` (5), `holiday_survey.signed_date` (5), `rental_contract.tenant` (5)
- **ocr->agent**: `holiday_survey.activities` (9), `receipt.merchant` (7), `holiday_survey.accommodation` (5), `holiday_survey.pets` (5), `holiday_survey.destination` (4)
