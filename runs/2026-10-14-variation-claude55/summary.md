## Bottom line
Choose `image->sonnet-5.5`. Both pipelines score 100.0% on clean pages, but on degraded (phone-photo) pages `image->sonnet-5.5` reaches 99.7% against 97.9% for `image->haiku-5.5`. That gap is **outside** the run-to-run range, so repeat noise does not explain it.

## What the facts say
- **Clean pages do not separate the models.** Both pipelines score 100.0% with a 0.0 pts range across repeats. The difference only shows up on degraded input.
- **The reading quality explains the degraded gap.** Reading degraded pages, `haiku-5.5` has lower number recall (94.8% vs 99.3%) and more invented numbers (11 vs 3). It also hit the token limit on 2 pages, while `sonnet-5.5` hit it on 0. Its degraded transcripts are also less stable: CER mean ranges 0.016–0.052, and 77.8% of pages were not identical across repeats.
- **Missed fields cluster in `image->haiku-5.5`.** Its misses are `newsletter.new_fee` (3), `receipt.date`, `receipt.merchant` and `rental_contract.monthly_rent` (1 each). `image->sonnet-5.5` missed only `rental_contract.monthly_rent` (1). Money and date fields are where errors land.
- **No runaway pages.** Both readers show 0 runaway pages on clean and degraded input, so degenerate output loops are not a concern here.
- **Self-hosted models: no evidence.** The key facts contain no self-hosted pipeline, so this benchmark cannot show where self-hosting helps or hurts.

## Recommendation
| goal | choice | why |
|---|---|---|
| highest accuracy | `image->sonnet-5.5` | Most accurate on degraded pages (99.7%), with the gap outside run-to-run noise. It ties `image->haiku-5.5` on clean pages. |
| lowest latency | `image->haiku-5.5` on clean pages; `image->sonnet-5.5` on degraded pages | These are the fastest pipelines per the key facts (1.5 s/doc clean, 1.3 s/doc degraded). On clean pages both pipelines run at 1.5 s/doc, so latency does not justify giving up accuracy. |
| lowest cost | Unknown | No price is configured for `image->haiku-5.5` or `image->sonnet-5.5`, so neither can be called cheapest. Set `price_per_mtok` to compare them. |
| checking / second opinion | `image->haiku-5.5` as cross-check | Use it as an independent second read. Where it disagrees with `image->sonnet-5.5`, flag the field for review, especially money and date fields. |
| self-hosting | Not supported by these facts | No self-hosted pipeline appears in the key facts. Benchmark one before deciding. |

## Caveats
- **Synthetic data.** The test set is 48 synthetic Norwegian documents. Real scans, handwriting or other languages may behave differently.
- **Simulated degradation.** Degraded pages are simulated phone photos. Real capture problems such as glare, folds or skew may hurt the models more.
- **Small differences are fragile.** With so few misses, a single document can shift a percentage. The clean-page tie is within noise.
- **Missing pipelines and prices.** Only image-based pipelines are in the key facts. Without prices, the cost trade-off is unresolved.
