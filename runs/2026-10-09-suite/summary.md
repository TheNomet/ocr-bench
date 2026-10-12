<!-- LLM comment by opus-5.5 (anthropic.claude-opus-5-5) on the computed key facts; 2205 in / 1709 out tokens; unverified numbers: none -->
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
