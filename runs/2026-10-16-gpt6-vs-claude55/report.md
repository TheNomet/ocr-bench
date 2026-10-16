# ocr-bench run `2026-10-16-gpt6-vs-claude55`



## Key facts (computed, not LLM-written)

| question | clean pages | degraded pages |
|---|---|---|
| most accurate pipeline | page images → Claude Haiku 5.5 (`image->haiku-5.5`): 100.0% | page images → Claude Sonnet 5.5 (`image->sonnet-5.5`): 99.1% |
| cheapest pipeline (among priced, see note) | – | – |
| fastest pipeline | page images → Claude Haiku 5.5 (`image->haiku-5.5`): 1.8 s/doc | page images → Claude Sonnet 5.5 (`image->sonnet-5.5`): 1.6 s/doc |

**Cost note:** no cost could be computed for `image->gpt-6-luna`, `image->gpt-6.1-sol`, `image->haiku-5.5`, `image->sonnet-5.5` (no price configured, or no successful request reported its token usage), so they are left out of the cheapest-pipeline comparison. Set `price_per_mtok` for those backends to include them.

Every pipeline (field accuracy, time per document, cost per 1,000 documents):

| pipeline | clean accuracy | degraded accuracy | s/doc (clean) | $/1k docs (clean) |
|---|---|---|---|---|
| page images → GPT-6 Luna (`image->gpt-6-luna`) | 91.8% | 87.5% | 3.3 | – |
| page images → GPT-6.1 Sol (`image->gpt-6.1-sol`) | 99.7% | 95.4% | 3.7 | – |
| page images → Claude Haiku 5.5 (`image->haiku-5.5`) | 100.0% | 98.8% | 1.8 | – |
| page images → Claude Sonnet 5.5 (`image->sonnet-5.5`) | 100.0% | 99.1% | 1.8 | – |

Fields missed most often (count over clean + degraded documents):

- page images → GPT-6 Luna (`image->gpt-6-luna`): `invoice.kid` (10), `holiday_survey.signed_date` (7), `receipt.merchant` (4), `receipt.card_last4` (4)
- page images → GPT-6.1 Sol (`image->gpt-6.1-sol`): `invoice.account_number` (4), `holiday_survey.signed_date` (2), `letter.recipient` (1), `newsletter.new_fee` (1)
- page images → Claude Haiku 5.5 (`image->haiku-5.5`): `newsletter.new_fee` (2), `holiday_survey.budget_per_person` (1), `rental_contract.monthly_rent` (1)
- page images → Claude Sonnet 5.5 (`image->sonnet-5.5`): `holiday_survey.activities` (1), `newsletter.new_fee` (1), `rental_contract.monthly_rent` (1)

## Recommendation

*LLM comment by opus-5.5 (anthropic/claude-opus-5-5) on the computed key facts. It interprets the key facts above.*

## Bottom line
Choose `image->sonnet-5.5`. It is the most accurate pipeline on degraded pages (99.1%), ties for most accurate on clean pages (100.0%), and is the fastest on degraded pages (1.6 s/doc). Real-world inputs look like phone photos, so robustness on degraded pages is the deciding factor.

## What the facts say
- All 4 pipelines read page images directly, so the differences come only from the extractor model. No OCR stage was involved.
- Clean pages do not separate the Claude models: `image->haiku-5.5` and `image->sonnet-5.5` both reach 100.0%. Degraded pages are where they diverge, and `image->gpt-6-luna` and `image->gpt-6.1-sol` lose more accuracy there than the Claude models.
- The most-missed fields differ by model:
  - `image->gpt-6-luna` misses mostly identifiers and dates, led by `invoice.kid` (10) and `holiday_survey.signed_date` (7). This points to a systematic weakness with reference numbers rather than random noise.
  - The Claude models' few misses are scattered amounts, such as `newsletter.new_fee` and `rental_contract.monthly_rent`. These look like isolated errors.
- The key facts report no runaway pages and no per-page degradation breakdown beyond the accuracy columns.
- No self-hosted models appear in the key facts, so there is no evidence on where self-hosting helps.

## Choice by goal
| goal | choice | why |
|---|---|---|
| highest accuracy | `image->sonnet-5.5` | Most accurate on degraded pages and tied for most accurate on clean pages |
| lowest latency | `image->haiku-5.5` (clean) / `image->sonnet-5.5` (degraded) | Named fastest for clean pages (1.8 s/doc) and degraded pages (1.6 s/doc) respectively |
| lowest cost | unknown | No pipeline has a configured price, so there is no cheapest pipeline. Set `price_per_mtok` before ranking. |
| checking / second opinion | `image->haiku-5.5` alongside `image->sonnet-5.5` | Close in accuracy, and their missed fields only partly overlap |
| self-host | no evidence yet | No self-hosted backend was benchmarked |

## Caveats
- The benchmark uses 48 synthetic Norwegian documents across 8 types. Real layouts, handwriting and other languages may behave differently.
- "Degraded" means a simulated phone photo. Real capture problems, such as glare, folds or multi-page scans, may be harsher.
- At the top, the miss counts are very small (1–2 per field). Differences between `image->haiku-5.5` and `image->sonnet-5.5` may not hold on a larger set.
- Cost is entirely unmeasured. A cost-driven decision could change once prices are configured.

## Setup

### Models

| role | name in tables | model | model id | served by | temperature | price |
|---|---|---|---|---|---|---|
| extraction agent (default) | `sonnet-4.5` | Claude Sonnet 4.5 (2025-09-29) | `anthropic.claude-sonnet-4-5-20250929-v1:0` | OpenAI-compatible gateway | 0 | $3.0 in / $15.0 out per 1M tokens |
| extractor | `gpt-6.1-sol` | GPT-6.1 Sol | `openai/gpt-6.1-sol` | OpenAI-compatible gateway | default | – |
| extractor | `gpt-6-luna` | GPT-6 Luna | `openai/gpt-6-luna` | OpenAI-compatible gateway | default | – |
| extractor | `sonnet-5.5` | Claude Sonnet 5.5 | `anthropic/claude-sonnet-5-5` | OpenAI-compatible gateway | 0 | – |
| extractor | `haiku-5.5` | Claude Haiku 5.5 | `anthropic/claude-haiku-5-5` | OpenAI-compatible gateway | 0 | – |
| recommendation writer | `opus-5.5` | Claude Opus 5.5 | `anthropic/claude-opus-5-5` | OpenAI-compatible gateway | 1 | – |

### The extraction agent

The **agent** (default extractor) is Claude Sonnet 4.5 (2025-09-29) (`anthropic.claude-sonnet-4-5-20250929-v1:0`), temperature 0. It gets the document type's field list (name, type, one-line description), plus either the page images (`image->agent`) or the text transcript of every page, in order (`<transcriber>->agent`), and returns one JSON object with the field values copied verbatim; max 2,048 output tokens. It never sees the ground truth. Pipelines that name another extractor (`gpt-6.1-sol`, `gpt-6-luna`, `sonnet-5.5`, `haiku-5.5`) use that model with the same input.

### GPU

No self-hosted model in this run, so no GPU was used.

### Run

| | |
|---|---|
| plan | `gpt6-vs-claude55`: OpenAI GPT-6.1 Sol and GPT-6 Luna against Claude Sonnet 5.5 and Haiku 5.5, each reading the page images and extracting the fields in one call (RAI gateway; Claude at temperature 0). (0 transcription runs) |
| documents | 48 documents, 54 pages, 108 page images (clean + degraded), seed 7 |
| runner | local (2048 CPU units / 4096 MB) |
| code | git `e264b1d+dirty` |
| started | 2026-10-16T22:54:29 |

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
| `image->gpt-6.1-sol` | One step: the extractor reads the page images directly and returns the fields. This is the baseline: what you get without any separate OCR step. Extractor: `gpt-6.1-sol` (GPT-6.1 Sol). |
| `image->gpt-6-luna` | One step: the extractor reads the page images directly and returns the fields. This is the baseline: what you get without any separate OCR step. Extractor: `gpt-6-luna` (GPT-6 Luna). |
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
- **token limit**: pages where the model used its whole output-token budget, so the text is cut off or empty (an empty one is a failed page, CER 1)
- **word recall / precision**: share of the page's words found / share of output words that are on the page; ignores order
- **number recall**: share of the amounts, dates, account numbers etc. on the page that appear in the output
- **invented numbers**: numbers of 3+ characters in the output that are not on the page
- **coverage**: share of pages or documents that got an answer. Failures, and selected pages or documents never attempted, count against the backend: CER 1 and no numbers found for a page, every field wrong for a document. Cheapest and fastest pipeline only compare pipelines with at least 99% coverage
- **field accuracy**: share of extracted fields equal to the ground truth (case, spaces and a kr/NOK prefix ignored). A value given for an optional field the document doesn't have counts as one wrong field
- **end-to-end**: per document: transcription time of all its pages (each page's lowest-concurrency transcript) plus the extraction call
- **$/1k**: LLMs: list price x tokens used. Self-hosted models: GPU hourly price / best sustained throughput (fastest run with at most 1% errors, GPU kept busy; if none, the run with the fewest errors, flagged); the cost one request at a time is shown next to it. Unknown token usage = no cost, never $0

## Results

### Field extraction (stage 2)

| Pipeline id | Variant | Docs | Coverage | Field accuracy | Extractor latency (s) | End-to-end (s) | $/1k docs |
|---|---|---|---|---|---|---|---|
| image->gpt-6-luna | clean | 48 | 100.0% | 91.8% | 3.28 | 3.28 | – |
| image->gpt-6-luna | degraded | 48 | 100.0% | 87.5% | 4.67 | 4.67 | – |
| image->gpt-6.1-sol | clean | 48 | 100.0% | 99.7% | 3.70 | 3.70 | – |
| image->gpt-6.1-sol | degraded | 48 | 100.0% | 95.4% | 6.32 | 6.32 | – |
| image->haiku-5.5 | clean | 48 | 100.0% | 100.0% | 1.78 | 1.78 | – |
| image->haiku-5.5 | degraded | 48 | 100.0% | 98.8% | 1.93 | 1.93 | – |
| image->sonnet-5.5 | clean | 48 | 100.0% | 100.0% | 1.81 | 1.81 | – |
| image->sonnet-5.5 | degraded | 48 | 100.0% | 99.1% | 1.63 | 1.63 | – |

Most-missed fields (count over clean + degraded):

- **image->gpt-6-luna**: `invoice.kid` (10), `holiday_survey.signed_date` (7), `receipt.merchant` (4), `receipt.card_last4` (4), `rental_contract.property_address` (4)
- **image->gpt-6.1-sol**: `invoice.account_number` (4), `holiday_survey.signed_date` (2), `letter.recipient` (1), `newsletter.new_fee` (1), `payslip.employer` (1)
- **image->haiku-5.5**: `newsletter.new_fee` (2), `holiday_survey.budget_per_person` (1), `rental_contract.monthly_rent` (1)
- **image->sonnet-5.5**: `holiday_survey.activities` (1), `newsletter.new_fee` (1), `rental_contract.monthly_rent` (1)
