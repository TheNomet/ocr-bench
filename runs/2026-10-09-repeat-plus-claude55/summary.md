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
