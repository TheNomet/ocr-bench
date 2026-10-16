## Bottom line
Choose `image->sonnet-5.5`. It is the most accurate pipeline on both clean (100.0%) and degraded (99.7%) pages, and also the fastest. Adding a separate `dolphin-v2` transcription step lowers accuracy and adds latency.

## What the facts say
- **The OCR step is the bottleneck.** Both pipelines use the same extractor, Claude Sonnet 5.5, so the accuracy gap comes from the input. `dolphin-v2->sonnet-5.5` reaches only 79.0% clean and 69.9% degraded, because the extractor can only work with what the transcript contains.
- **Layout-dependent fields get lost.** `dolphin-v2->sonnet-5.5` most often misses `holiday_survey.activities`, `holiday_survey.accommodation` and `holiday_survey.pets` (12 each), plus `rental_contract.landlord` (8). This suggests form-like structure such as checkboxes and labelled blocks does not survive transcription. `image->sonnet-5.5` missed only `rental_contract.monthly_rent` (1).
- **Degraded photos hurt the transcriber badly.** For `dolphin-v2`, CER median rises from 0.084 to 0.155 and number recall falls from 82.4% to 76.2%. Invented numbers jump from 19 to 95, which is a serious risk for invoices, payslips and bank statements.
- **Some transcription failures are runaways.** `dolphin-v2` had 3 clean and 5 degraded runaway pages, and hit the token limit on 4 and 6 pages respectively. These failures produce bad text rather than an error, so nothing downstream flags them.
- **Self-hosting did not help here.** `dolphin-v2` takes 10.8 s/page one at a time and peaks at 25 pages/min at concurrency 16. Its GPU costs $1.49/hour whether busy or idle. The image pipeline beats it on both accuracy and speed.

## Recommendation
| Goal | Choice | Why |
|---|---|---|
| Highest accuracy | `image->sonnet-5.5` | Most accurate on clean and degraded pages |
| Lowest latency | `image->sonnet-5.5` | Fastest: 2.3 s/doc clean, 1.6 s/doc degraded, vs 23.0 for `dolphin-v2->sonnet-5.5` |
| Lowest cost | Unknown: no cheapest pipeline can be named | No price was configured for either pipeline. Set `price_per_mtok` to compare them. `dolphin-v2` transcription alone is $0.98/1k pages at full load, or $8.68/1k pages one request at a time, and that excludes the extractor's cost |
| Checking / second opinion | `image->sonnet-5.5` alone, optionally with a human spot-check | The `dolphin-v2` transcript is too error-prone, and invents numbers, to be useful as a check |
| When to self-host | Not on this evidence | Only worth reconsidering if data must stay in-house, and only after re-testing with a stronger transcriber |

## Caveats
- The 48 documents are synthetic Norwegian documents across 8 types. Real scans, other languages and longer documents may behave differently.
- "Degraded" is a simulated phone photo. Real capture problems such as glare, folds and handwriting are not covered.
- With no prices configured, the cost comparison is open. A cheap enough extractor price could still change the trade-off for high-volume use.
- Only 1 self-hosted model was tested on 1 GPU type. Other transcribers or hardware could close part of the gap.
