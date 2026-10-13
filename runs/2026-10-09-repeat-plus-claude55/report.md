# ocr-bench run `2026-10-09-repeat-plus-claude55`



## Key facts (computed, not LLM-written)

| question | clean pages | degraded pages |
|---|---|---|
| most accurate pipeline | `image->haiku-5.5` 100.0% | `image->sonnet-5.5` 99.1% |
| cheapest pipeline (among priced, see note) | – | – |
| fastest pipeline | `image->haiku-5.5` 1.5 s/doc | `image->sonnet-5.5` 1.7 s/doc |

**Cost note:** no price is configured for `image->haiku-5.5`, `image->sonnet-5.5`, `ocr->sonnet-5.5`, so they are left out of the cheapest-pipeline comparison. Set `price_per_mtok` for those backends to include them.

Every pipeline (field accuracy, time per document, cost per 1,000 documents):

| pipeline | clean accuracy | degraded accuracy | s/doc (clean) | $/1k docs (clean) |
|---|---|---|---|---|
| `image->haiku-5.5` | 100.0% | 98.2% | 1.5 | – |
| `image->sonnet-5.5` | 99.7% | 99.1% | 2.1 | – |
| `ocr->sonnet-5.5` | 95.1% | 78.7% | 8.1 | – |

| reader | variant | CER median | number recall | invented numbers (excl. runaway) | runaway pages |
|---|---|---|---|---|---|
| `haiku-5.5` | clean | 0.000 | 100.0% | 0 | 0 |
| `haiku-5.5` | degraded | 0.001 | 98.5% | 7 | 0 |
| `ocr` | clean | 0.006 | 99.5% | 1 | 0 |
| `ocr` | degraded | 0.044 | 90.8% | 85 | 0 |
| `sonnet-5.5` | clean | 0.000 | 100.0% | 0 | 0 |
| `sonnet-5.5` | degraded | 0.001 | 99.3% | 3 | 0 |

| reader | p50 s/page (one at a time) | max pages/min (at concurrency) |
|---|---|---|
| `ocr` | 6.1 | 29 (16) |
| `haiku-5.5` | 4.3 | 136 (16) |
| `sonnet-5.5` | 5.3 | 175 (16) |

Fields missed most often (count over clean + degraded documents):

- `image->haiku-5.5`: `newsletter.new_fee` (3), `receipt.merchant` (1), `rental_contract.monthly_rent` (1), `rental_contract.deposit` (1)
- `image->sonnet-5.5`: `newsletter.new_fee` (3), `rental_contract.monthly_rent` (1)
- `ocr->sonnet-5.5`: `holiday_survey.activities` (9), `receipt.merchant` (8), `holiday_survey.accommodation` (6), `holiday_survey.pets` (6)

## Recommendation

*LLM comment by opus-5.5 (anthropic.claude-opus-5-5) on the computed key facts. It interprets the key facts above.*

## Bottom line
Choose `image->sonnet-5.5`. It is the most accurate pipeline on degraded pages (99.1%) and stays near-perfect on clean pages (99.7%), so it is the most robust choice when input quality varies. `image->haiku-5.5` is a close alternative for clean scans. Costs are unknown for all three pipelines.

## What the facts say
- **Reading images directly beats transcribing first.** Both `image->` pipelines stay near-perfect. `ocr->sonnet-5.5` drops to 95.1% on clean pages and 78.7% on degraded ones. The loss comes from the `ocr` stage, not from the extractor.
- **`ocr` breaks down on phone-photo pages.** On degraded pages its CER median rises to 0.044 and number recall falls to 90.8%. It also invents 85 numbers, against 7 for `haiku-5.5` and 3 for `sonnet-5.5`. Wrong digits that look plausible are the costliest kind of error in invoices and payslips.
- **The transcript loses layout.** `ocr->sonnet-5.5` misses mostly `holiday_survey` fields (`activities`, `accommodation`, `pets`) and `receipt.merchant`. That pattern suggests checkboxes, form structure and header or logo text are lost in plain-text transcription.
- **The image pipelines share one weak spot.** Both miss `newsletter.new_fee` (3 each). That points to an ambiguous field definition or ground-truth label rather than a reading problem.
- **No runaway pages occurred for any reader.** The failures are degraded transcription quality, not output that runs out of control.
- **The self-hosted model gives no speed advantage.** `ocr` has the slowest p50 (6.1 s/page) and by far the lowest throughput (29 pages/min, against 136 and 175).

## Recommendation
| Goal | Choice | Why |
|---|---|---|
| Highest accuracy | `image->sonnet-5.5` | Best on degraded pages (99.1%) and 99.7% on clean pages. `image->haiku-5.5` leads on clean pages only (100.0%). |
| Lowest latency | `image->haiku-5.5` (clean); `image->sonnet-5.5` (degraded) | Fastest per the key facts: 1.5 s/doc clean, 1.7 s/doc degraded. |
| Lowest cost | Unknown; cannot rank | No pipeline has a price configured. Set `price_per_mtok` before deciding on cost. |
| Checking / second opinion | Run `image->haiku-5.5` alongside `image->sonnet-5.5` | Both are highly accurate, and disagreements flag fields to review. Do not use `ocr->sonnet-5.5` here; its invented numbers would create noise. |
| Self-host OCR | Only if images cannot leave your infrastructure, and only for clean pages | It is less accurate, slower and has lower throughput. Its cost is also unconfirmed. |

## Caveats
- The documents are synthetic: 48 Norwegian documents across 8 types. Real scans, handwriting and other languages may behave differently.
- "Degraded" means simulated phone photos. Real-world damage such as glare, folds and skew may be harsher.
- With small samples, a single missed field shifts accuracy. The near-100% scores among the `image->` pipelines are effectively tied.
- Everything ran at temperature 1 with a single checkpoint of `ocr` (07dea832) on one instance type. Results may vary between runs and model versions.

## Setup

### Models

| role | name in tables | model | model id | served by | temperature | price |
|---|---|---|---|---|---|---|
| transcriber (self-hosted) | `ocr` | baidu/Unlimited-OCR @ 07dea832 | `baidu/Unlimited-OCR` | vLLM on SageMaker real-time endpoint | 0 | $1.49/hour (on-demand) |
| transcriber, extraction agent (default) | `sonnet-5.5` | Claude Sonnet 5.5 | `anthropic.claude-sonnet-5-5` | OpenAI-compatible gateway | 1 | – |
| transcriber, extractor | `haiku-5.5` | Claude Haiku 5.5 | `anthropic.claude-haiku-5-5` | OpenAI-compatible gateway | 1 | – |
| summary writer | `opus-5.5` | Claude Opus 5.5 | `anthropic.claude-opus-5-5` | OpenAI-compatible gateway | 1 | – |

### The extraction agent

The **agent** (default extractor) is Claude Sonnet 5.5 (`anthropic.claude-sonnet-5-5`), temperature 1. It gets the document type's field list (name, type, one-line description), plus either the page images (`image->agent`) or the text transcript of every page, in order (`<transcriber>->agent`), and returns one JSON object with the field values copied verbatim; max 2,048 output tokens. It never sees the ground truth. Pipelines that name another extractor (`haiku-5.5`) use that model with the same input.

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

### Run

| | |
|---|---|
| plan | `repeat-plus-claude55`: Re-check of the OCR model on the same GPU, and the new Claude Sonnet 5.5 and Haiku 5.5. The Claude 4.5 models were also re-run as a reproducibility check; that comparison is in Compare runs. (7 transcription runs) |
| documents | 48 documents, 54 pages, 108 page images (clean + degraded), seed 7 |
| runner | fargate (2048 CPU units / 4096 MB) |
| code | git `5cdc563` |
| started | 2026-10-09T14:05:02 |

Transcription runs in this plan:

- `ocr` at concurrency 1
- `ocr` at concurrency 4
- `ocr` at concurrency 16
- `sonnet-5.5` at concurrency 1
- `haiku-5.5` at concurrency 1
- `sonnet-5.5` at concurrency 16
- `haiku-5.5` at concurrency 16

## How to read this report

### Stages

- **1. Transcribe**: Every page image (clean and degraded) is sent to each transcriber, at several concurrency levels. This measures how well and how fast each backend reads.
- **2. Extract**: The extraction agent pulls each document type's fields (totals, dates, names, checked boxes…) out of each pipeline's input. Scored field by field against the ground truth.
- **3. Score**: Transcripts and fields are compared with the ground truth; latency and cost are aggregated.
- **4. Recommend**: An LLM reads the computed key facts and a short description of the setup, and writes a recommendation. It may not compute numbers; every number it writes is checked against the facts, and any it cannot find there is flagged as unverified.

### Pipelines (the arrows)

Pipeline names read `<input> -> <extractor>`. The left side is what the extracting model gets to read: `image` = the page images themselves, anything else = the text transcript produced by that backend in stage 1. The right side is the model that extracts the fields: `agent` = the run's default extraction agent, otherwise the named backend.

| pipeline | what the agent reads |
|---|---|
| `ocr->sonnet-5.5` | Two steps: the self-hosted OCR model (baidu/Unlimited-OCR) transcribes every page to text, then the extraction agent reads that text (not the images) and returns the fields. Extractor: `sonnet-5.5` (Claude Sonnet 5.5). |
| `image->sonnet-5.5` | One step: the extraction agent reads the page images directly and returns the fields. This is the baseline: what you get without any separate OCR step. Extractor: `sonnet-5.5` (Claude Sonnet 5.5). |
| `image->haiku-5.5` | One step: the extraction agent reads the page images directly and returns the fields. This is the baseline: what you get without any separate OCR step. Extractor: `haiku-5.5` (Claude Haiku 5.5). |

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
| ocr | 1 | 108 | 108 | 9.7 | 6.05 | 8.69 | 2.56 |
| ocr | 4 | 108 | 108 | 23.9 | 10.18 | 13.56 | 1.04 |
| ocr | 16 | 108 | 108 | 28.8 | 32.45 | 40.33 | 0.86 |
| sonnet-5.5 | 1 | 108 | 108 | 11.6 | 5.27 | 7.33 | – |
| haiku-5.5 | 1 | 108 | 108 | 12.3 | 4.27 | 7.88 | – |
| sonnet-5.5 | 16 | 108 | 108 | 174.7 | 4.98 | 7.34 | – |
| haiku-5.5 | 16 | 108 | 108 | 136.3 | 4.08 | 8.58 | – |

### Transcription quality (stage 1)

| backend | variant | pages | CER mean | CER median | runaway | word recall | number recall | missing nums | invented nums |
|---|---|---|---|---|---|---|---|---|---|
| haiku-5.5 | clean | 54 | 0.014 | 0.000 | 0 | 100.0% | 100.0% | 0 | 0 |
| haiku-5.5 | degraded | 54 | 0.018 | 0.001 | 0 | 99.4% | 98.5% | 10 | 7 |
| ocr | clean | 54 | 0.013 | 0.006 | 0 | 97.2% | 99.5% | 2 | 1 |
| ocr | degraded | 54 | 0.077 | 0.044 | 0 | 85.5% | 90.8% | 135 | 85 |
| sonnet-5.5 | clean | 54 | 0.019 | 0.000 | 0 | 100.0% | 100.0% | 0 | 0 |
| sonnet-5.5 | degraded | 54 | 0.019 | 0.001 | 0 | 99.4% | 99.3% | 4 | 3 |

### Field extraction (stage 2)

| pipeline | variant | docs | field accuracy | agent s | end-to-end s | $ / 1k docs |
|---|---|---|---|---|---|---|
| image->haiku-5.5 | clean | 48 | 100.0% | 1.50 | 1.50 | – |
| image->haiku-5.5 | degraded | 48 | 98.2% | 1.88 | 1.88 | – |
| image->sonnet-5.5 | clean | 48 | 99.7% | 2.07 | 2.07 | – |
| image->sonnet-5.5 | degraded | 48 | 99.1% | 1.73 | 1.73 | – |
| ocr->sonnet-5.5 | clean | 48 | 95.1% | 1.45 | 8.06 | – |
| ocr->sonnet-5.5 | degraded | 48 | 78.7% | 2.12 | 9.40 | – |

Most-missed fields (count over clean + degraded):

- **image->haiku-5.5**: `newsletter.new_fee` (3), `receipt.merchant` (1), `rental_contract.monthly_rent` (1), `rental_contract.deposit` (1)
- **image->sonnet-5.5**: `newsletter.new_fee` (3), `rental_contract.monthly_rent` (1)
- **ocr->sonnet-5.5**: `holiday_survey.activities` (9), `receipt.merchant` (8), `holiday_survey.accommodation` (6), `holiday_survey.pets` (6), `holiday_survey.destination` (4)
