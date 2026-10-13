## Bottom line
Choose `image->sonnet-5.5` for production. It is the most accurate on degraded pages (99.1%) and nearly perfect on clean pages (99.7%), so it holds up best on real-world phone photos. `image->haiku-5.5` is a close alternative if clean scans dominate.

## What the facts say
- **Reading images directly beats transcribing first.** Both image pipelines stay near-perfect. `unlimited-ocr->sonnet-5.5` drops to 95.1% on clean pages and 78.7% on degraded pages, because errors in the OCR text cannot be recovered by the extractor.
- **The self-hosted reader degrades badly on photos.** `unlimited-ocr` falls to 90.8% number recall and produces 85 invented numbers on degraded pages. The Claude readers produce single-digit counts.
- **The OCR pipeline's misses are mostly on form-style fields.** `holiday_survey.activities`, `holiday_survey.accommodation` and `holiday_survey.pets` are probably checkbox or selection items. This suggests the transcript loses marks and layout. Its misses on `receipt.merchant` may come from logos or stylised headers.
- **The image pipelines share one blind spot.** Both miss `newsletter.new_fee` (3 each), which points to an ambiguous field (e.g. old vs. new fee) rather than a reading problem.
- **No runaway pages occurred for any reader**, so the failures are degradation and hallucination, not runaway output.
- **Self-hosting does not help here.** `unlimited-ocr` is slower per page (6.1 s p50) and much lower in throughput (29 vs. 175 pages/min for `sonnet-5.5`). Its GPU costs $1.49/hour even when idle.

## Recommendation
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
