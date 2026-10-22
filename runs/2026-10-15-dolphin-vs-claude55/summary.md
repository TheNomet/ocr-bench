## Bottom line
Choose `image->sonnet-5.5`: it is the most accurate pipeline on both clean (100.0%) and degraded (99.7%) pages and also the fastest. Sending page images straight to the extractor avoids the transcription errors that drag `dolphin-v2->sonnet-5.5` down to 79.0% clean and 69.9% degraded.

## What the facts say
- The accuracy gap comes from stage 1. `dolphin-v2` transcripts have a CER median of 0.084 on clean pages and 0.155 on degraded ones. Number recall is only 82.4% clean and 76.2% degraded, so the extractor never sees much of the content.
- `dolphin-v2` invents content. It produced 95 invented numbers on degraded pages, against 19 on clean pages. It also had runaway pages (3 clean, 5 degraded) and pages hitting the token limit (4 clean, 6 degraded). This points to generation loops rather than simple misreads.
- The misses cluster in specific fields. `holiday_survey.activities`, `holiday_survey.accommodation` and `holiday_survey.pets` (12 each) suggest the transcript loses form structure such as checkboxes and layout. `rental_contract.landlord` (8) suggests it also drops some named parties. `image->sonnet-5.5` missed only `rental_contract.monthly_rent` (1).
- Degraded photos hurt `dolphin-v2` far more than they hurt the image pipeline, which barely moves.
- Self-hosting does not help here. The two-step pipeline is slower (23.0 s/doc vs 2.3 s/doc clean). Its cost advantage on clean pages depends on keeping the GPU busy: one request at a time it rises to $8.68/1k pages, and the GPU costs $1.49/hour whether busy or idle.

## Recommendation
| goal | choice | why |
|---|---|---|
| highest accuracy | `image->sonnet-5.5` | Most accurate on clean and degraded pages |
| lowest latency | `image->sonnet-5.5` | Fastest: 2.3 s/doc clean, 1.6 s/doc degraded |
| lowest cost | `dolphin-v2->sonnet-5.5` on clean pages ($6.45/1k docs); `image->sonnet-5.5` on degraded pages ($4.14/1k docs) | The clean-page saving costs a large accuracy drop and assumes a saturated GPU, so `image->sonnet-5.5` is the practical choice |
| checking / second opinion | `image->sonnet-5.5` re-run or human review | `dolphin-v2` transcripts invent numbers, so they are unreliable as a cross-check |
| self-host a model | Only with sustained high volume and a privacy need that rules out sending images | You must accept lower accuracy and keep the GPU at concurrency to hold cost down |

## Caveats
- The 48 documents are synthetic and Norwegian, across 8 types. Real scans, other languages and other layouts may behave differently.
- Degradation is a simulated phone photo, not real capture noise.
- Only 1 self-hosted reader (`dolphin-v2` @ c37c6276) was evaluated. Other OCR models or prompts could close the gap.
- Cost figures for `dolphin-v2` assume the best sustained throughput of 25.4 pages/min. Real utilisation will likely be lower, which raises the cost.
