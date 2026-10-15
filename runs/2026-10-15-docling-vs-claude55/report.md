# ocr-bench run `2026-10-15-docling-vs-claude55`



## Key facts (computed, not LLM-written)

| question | clean pages | degraded pages |
|---|---|---|
| most accurate pipeline | page images → Claude Sonnet 5.5 (`image->sonnet-5.5`): 100.0% | page images → Claude Sonnet 5.5 (`image->sonnet-5.5`): 99.7% |
| cheapest pipeline (among priced, see note) | – | – |
| fastest pipeline | page images → Claude Sonnet 5.5 (`image->sonnet-5.5`): 1.7 s/doc | page images → Claude Sonnet 5.5 (`image->sonnet-5.5`): 1.4 s/doc |

**Cost note:** no cost could be computed for `granite-docling->sonnet-5.5`, `image->sonnet-5.5` (no price configured, or no successful request reported its token usage), so they are left out of the cheapest-pipeline comparison. Set `price_per_mtok` for those backends to include them.

Every pipeline (field accuracy, time per document, cost per 1,000 documents):

| pipeline | clean accuracy | degraded accuracy | s/doc (clean) | $/1k docs (clean) |
|---|---|---|---|---|
| granite-docling-258M text → Claude Sonnet 5.5 (`granite-docling->sonnet-5.5`) | 78.7% | 52.6% | 5.8 | – |
| page images → Claude Sonnet 5.5 (`image->sonnet-5.5`) | 100.0% | 99.7% | 1.7 | – |

| reader | variant | CER median | number recall | invented numbers (excl. runaway) | runaway pages | pages at token limit |
|---|---|---|---|---|---|---|
| `granite-docling` | clean | 0.079 | 92.8% | 3 | 0 | 2 |
| `granite-docling` | degraded | 0.283 | 60.9% | 639 | 3 | 8 |

| reader | p50 s/page (one at a time) | max pages/min (at concurrency) |
|---|---|---|
| `granite-docling` | 2.7 | 63 (16) |

`granite-docling` runs on its own GPU (ml.g6.xlarge), which costs $1.20/hour whether busy or idle; it handled at most ~3782 pages/hour in this run.

Pipeline costs that include `granite-docling` use $0.32/1k pages: the cost at its best sustained throughput: 63.0 pages/min at concurrency 16 (GPU kept busy). One request at a time it would be $1.42/1k pages.


Fields missed most often (count over clean + degraded documents):

- granite-docling-258M text → Claude Sonnet 5.5 (`granite-docling->sonnet-5.5`): `holiday_survey.activities` (12), `holiday_survey.accommodation` (12), `payslip.gross_pay` (10), `receipt.total` (9)
- page images → Claude Sonnet 5.5 (`image->sonnet-5.5`): `rental_contract.monthly_rent` (1)

## Recommendation

*LLM comment by opus-5.5 (anthropic.claude-opus-5-5) on the computed key facts. It interprets the key facts above.*

## Bottom line
Choose `image->sonnet-5.5`. It is the most accurate pipeline on both clean (100.0%) and degraded (99.7%) pages, and also the fastest. The `granite-docling` transcription step adds errors that the extractor cannot recover from, especially on phone-photo pages.

## What the facts say
- **The transcript limits accuracy.** Both pipelines use the same extractor (Claude Sonnet 5.5), so the accuracy gap comes from the input. `granite-docling->sonnet-5.5` reaches 78.7% clean and 52.6% degraded, and the extractor can only work with what the transcript contains.
- **Degradation breaks `granite-docling`.** On degraded pages its CER median rises from 0.079 to 0.283 and number recall falls from 92.8% to 60.9%. Invented numbers jump from 3 to 639, and 3 pages ran away. Reading the images directly barely changes between clean and degraded pages.
- **The most-missed fields point to lost structure and lost numbers.**
  - `holiday_survey.activities` and `holiday_survey.accommodation` are the top misses for `granite-docling->sonnet-5.5`. This suggests survey layouts such as checkboxes or tables do not survive transcription.
  - `payslip.gross_pay` and `receipt.total` are also often missed, which matches the drop in number recall.
- **Token-limit pages signal looping or overlong output.** `granite-docling` hit the token limit on 2 clean pages and 8 degraded pages. That output becomes noisy text for the extractor.
- **`image->sonnet-5.5` has only 1 recurring miss.** It missed `rental_contract.monthly_rent` (1), which is too few to show a pattern.
- **Self-hosting does not help here.** `granite-docling` is slower (5.8 s/doc versus 1.7 s/doc clean) and less accurate. Its GPU costs $1.20/hour whether busy or idle.

## Choice by goal
| goal | choice | why |
|---|---|---|
| highest accuracy | `image->sonnet-5.5` | Most accurate on clean and degraded pages |
| lowest latency | `image->sonnet-5.5` | Fastest pipeline: 1.7 s/doc clean, 1.4 s/doc degraded |
| lowest cost | unknown | No price is configured for either pipeline, so neither can be called cheapest. `granite-docling` transcription alone is $0.32/1k pages at full load, but the extractor cost is still missing. Set `price_per_mtok` to compare. |
| checking / second opinion | Re-run `image->sonnet-5.5` on low-confidence fields | `granite-docling->sonnet-5.5` is too unreliable to serve as a check, especially on degraded pages |
| when to self-host | Not on this evidence | Consider it only if data must stay on your own infrastructure, and expect much lower accuracy on photos |

## Caveats
- The benchmark uses 48 synthetic Norwegian documents across 8 types. Real scans, handwriting or other languages may behave differently.
- "Degraded" is a simulated phone photo. Real capture problems such as glare, folds or skew may hurt more or less than the simulation.
- Cost is entirely unpriced, so the choice may change once token prices are set. The `granite-docling` cost also depends heavily on keeping the GPU busy ($0.32 versus $1.42 per 1k pages).
- Only 1 self-hosted reader was tested. A stronger OCR model might narrow the gap.

## Setup

### Models

| role | name in tables | model | model id | served by | temperature | price |
|---|---|---|---|---|---|---|
| transcriber (self-hosted) | `granite-docling` | ibm-granite/granite-docling-258M @ 55d41aa2 | `ibm-granite/granite-docling-258M` | vLLM on SageMaker real-time endpoint | 0 | $1.20/hour (on-demand) |
| extraction agent (default) | `sonnet-4.5` | Claude Sonnet 4.5 (2025-09-29) | `anthropic.claude-sonnet-4-5-20250929-v1:0` | OpenAI-compatible gateway | 0 | $3.0 in / $15.0 out per 1M tokens |
| extractor | `sonnet-5.5` | Claude Sonnet 5.5 | `anthropic.claude-sonnet-5-5` | OpenAI-compatible gateway | 1 | – |
| recommendation writer | `opus-5.5` | Claude Opus 5.5 | `anthropic.claude-opus-5-5` | OpenAI-compatible gateway | 1 | – |

### The extraction agent

The **agent** (default extractor) is Claude Sonnet 4.5 (2025-09-29) (`anthropic.claude-sonnet-4-5-20250929-v1:0`), temperature 0. It gets the document type's field list (name, type, one-line description), plus either the page images (`image->agent`) or the text transcript of every page, in order (`<transcriber>->agent`), and returns one JSON object with the field values copied verbatim; max 2,048 output tokens. It never sees the ground truth. Pipelines that name another extractor (`sonnet-5.5`) use that model with the same input.

Models with temperature above 0 sample their output, so their results vary slightly between runs; differences of a point or two are within noise.

### GPU

| | |
|---|---|
| instance | `ml.g6.xlarge` (picked from pools: ml.g6.xlarge, ml.g6e.xlarge, ml.g5.xlarge, ml.g5.2xlarge) |
| GPU | NVIDIA L4, 24 GB |
| host | 4 vCPU, 16 GB RAM |
| price | $1.20/hour (on-demand), billed while the endpoint exists, busy or idle |
| region | eu-north-1 |
| host image | `al2023-ami-sagemaker-inference-gpu-4-1` |
| container | `docker.io/vllm/vllm-openai@sha256:b7a7be708c9a325107cdeddeba095e5637617716b3ab469c19d34759ec1afa39` (digest `sha256:9a2b4d9adfd5`) |
| serving flags | `--max-model-len 8192 --max-num-seqs 64 --gpu-memory-utilization 0.90` |
| post-processing | `doctags_to_text` |

### Run

| | |
|---|---|
| plan | `docling-vs-claude55`: IBM Granite-Docling (the Docling library's model, self-hosted on a GPU) as a reader in front of Claude Sonnet 5.5, against Sonnet 5.5 reading the page images directly. (3 transcription runs) |
| documents | 48 documents, 54 pages, 108 page images (clean + degraded), seed 7 |
| runner | fargate (2048 CPU units / 4096 MB) |
| code | git `e2b0a1c` |
| started | 2026-10-15T13:22:33 |

Transcription runs in this plan:

- `granite-docling` at concurrency 1
- `granite-docling` at concurrency 4
- `granite-docling` at concurrency 16

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
| `granite-docling->sonnet-5.5` | Two steps: the self-hosted model `granite-docling` (ibm-granite/granite-docling-258M @ 55d41aa2) transcribes every page to text, then the extractor reads that text (not the images) and returns the fields. Extractor: `sonnet-5.5` (Claude Sonnet 5.5). |
| `image->sonnet-5.5` | One step: the extractor reads the page images directly and returns the fields. This is the baseline: what you get without any separate OCR step. Extractor: `sonnet-5.5` (Claude Sonnet 5.5). |

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

### Speed and cost (stage 1)

| Backend | Conc. | Pages | OK | Pages/min | p50 (s) | p95 (s) | $/1k pages |
|---|---|---|---|---|---|---|---|
| granite-docling | 1 | 108 | 108 | 14.0 | 2.75 | 19.33 | $1.42 |
| granite-docling | 4 | 108 | 108 | 34.4 | 3.96 | 27.04 | $0.58 |
| granite-docling | 16 | 108 | 108 | 63.0 | 7.58 | 49.95 | $0.32 |

### Transcription quality (stage 1)

| Backend | Variant | Pages | Coverage | CER mean | CER median | Runaway | Token limit | Word recall | Number recall | Numbers missing | Invented numbers |
|---|---|---|---|---|---|---|---|---|---|---|---|
| granite-docling | clean | 54 | 100.0% | 0.134 | 0.079 | 0 | 2 | 92.8% | 92.8% | 117 | 3 |
| granite-docling | degraded | 54 | 100.0% | 0.381 | 0.283 | 3 | 8 | 63.5% | 60.9% | 526 | 639 |

### Field extraction (stage 2)

| Pipeline id | Variant | Docs | Coverage | Field accuracy | Extractor latency (s) | End-to-end (s) | $/1k docs |
|---|---|---|---|---|---|---|---|
| granite-docling->sonnet-5.5 | clean | 48 | 100.0% | 78.7% | 2.08 | 5.79 | – |
| granite-docling->sonnet-5.5 | degraded | 48 | 100.0% | 52.6% | 2.58 | 8.52 | – |
| image->sonnet-5.5 | clean | 48 | 100.0% | 100.0% | 1.72 | 1.72 | – |
| image->sonnet-5.5 | degraded | 48 | 100.0% | 99.7% | 1.37 | 1.37 | – |

Most-missed fields (count over clean + degraded):

- **granite-docling->sonnet-5.5**: `holiday_survey.activities` (12), `holiday_survey.accommodation` (12), `payslip.gross_pay` (10), `receipt.total` (9), `receipt.item_count` (9)
- **image->sonnet-5.5**: `rental_contract.monthly_rent` (1)
