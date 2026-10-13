<!-- LLM comment by opus-5.5 (anthropic.claude-opus-5-5) on the computed key facts; 3414 in / 1617 out tokens; unverified numbers: none -->
## Bottom line
Choose `image->sonnet-5.5` for accuracy. It is the most accurate pipeline on degraded pages (99.1%), and its reader barely degrades on phone photos. Its cost is unknown because no price is configured, so if a priced, predictable budget matters, `image->haiku-4.5` is the cheapest and fastest pipeline.

## What the facts say
- **Degradation separates the readers.** `sonnet-5.5` and `haiku-5.5` stay near-perfect on degraded pages. `sonnet-4.5` and `haiku-4.5` lose number recall and invent many numbers. Only these two older readers produced runaway pages, so input quality matters far more for them.
- **The self-hosted `ocr` falls in the middle.** On degraded pages it beats `sonnet-4.5` and `haiku-4.5` on number recall and runaway pages. It is clearly worse than the 5.5 readers.
- **OCR pipelines miss layout-dependent fields.** `ocr->agent` and `ocr->sonnet-5.5` miss nearly the same fields: `holiday_survey.activities`, `.accommodation`, `.pets` and `receipt.merchant`. Swapping in a stronger extractor didn't fix this, so the information (likely checkboxes and logo-style merchant names) is lost at the transcription step.
- **One field is missed by every image pipeline.** `newsletter.new_fee` appears in all of their missed lists. That points to an ambiguous field definition or document design rather than a reading failure, and it is worth reviewing the field description.
- **Self-hosting is only cheap at sustained volume.** It undercuts `sonnet-4.5` transcription above ~90 pages/hour and `haiku-4.5` above ~307 pages/hour, because it bills $1.49/hour even when idle. Its peak throughput at concurrency was also the lowest of all readers.

## Recommendation
| Goal | Choice | Why |
|---|---|---|
| Highest accuracy | `image->sonnet-5.5` (degraded); `image->haiku-5.5` (clean, 100.0%) | Most accurate in the key facts; cost unknown (unpriced) |
| Lowest latency | `image->haiku-4.5` | Fastest pipeline, 1.3 s/doc |
| Lowest cost | `image->haiku-4.5` | Cheapest priced pipeline; the 5.5 pipelines are unpriced, not cheap |
| Checking / second opinion | `image->haiku-5.5` alongside `image->sonnet-5.5` | Both readers are strong but differ slightly in missed fields; disagreements flag fields for review |
| Self-hosting `ocr` | Only with sustained volume above ~307 pages/hour, or a hard data-residency requirement | Idle cost, lower throughput, and systematic misses on holiday_survey and receipt fields |

## Caveats
- The data is 48 synthetic Norwegian documents. Real scans, handwriting and other languages may behave differently.
- The 5.5 models ran at temperature 1, so their results may vary between runs. Repeat the runs before committing.
- The "degraded" variant is a simulated phone photo and may not match real capture artefacts.
- Cost rankings will change once `price_per_mtok` is set for `sonnet-5.5` and `haiku-5.5`.
