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

## Recommendation
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
