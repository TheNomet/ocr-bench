## Bottom line
Choose `image->haiku-5.5` as the default. It is the cheapest pipeline on both clean ($0.38/1k docs) and degraded pages ($0.28/1k docs). It ties for most accurate on clean pages (100.0%) and is close behind the leader on degraded pages (98.8% vs 99.1%). You do not need to trade accuracy for cost here.

## What the facts say
- **Differences come from the extractor.** All 4 pipelines read page images directly with no OCR step, so the extracting model alone explains the gaps. The 2 Claude models lead on both clean and degraded pages.
- **Degraded pages hurt every pipeline, but unevenly.** `image->haiku-5.5` and `image->sonnet-5.5` lose little. `image->gpt-6.1-sol` drops to 95.4% and `image->gpt-6-luna` drops to 87.5%. Robustness to phone-photo noise is the clearest separator between models.
- **GPT models miss structured identifiers.** `image->gpt-6-luna` misses `invoice.kid` (10) and `receipt.card_last4` (4). `image->gpt-6.1-sol` misses `invoice.account_number` (4). Errors on payment-critical digit strings are costly in practice.
- **Claude misses are few and scattered.** They fall mostly on amount fields such as `newsletter.new_fee` and `rental_contract.monthly_rent`, which suggests ambiguity in the documents rather than reading failure.
- **Price does not track accuracy.** `image->sonnet-5.5` and `image->gpt-6.1-sol` cost far more per 1k docs than `image->haiku-5.5` without a matching accuracy gain.
- **No runaway-page or self-hosted data.** None of the listed pipelines is self-hosted, so these facts say nothing about where self-hosting helps.

## Recommendation
| goal | choice | why |
|---|---|---|
| highest accuracy | `image->sonnet-5.5` (degraded); `image->haiku-5.5` or `image->sonnet-5.5` (clean) | Best degraded accuracy at 99.1%; both reach 100.0% on clean pages |
| lowest latency | `image->haiku-5.5` (clean, 1.8 s/doc); `image->sonnet-5.5` (degraded, 1.6 s/doc) | Fastest pipeline per the key facts |
| lowest cost | `image->haiku-5.5` | Cheapest on both clean and degraded pages |
| checking/second opinion | `image->sonnet-5.5` | Different model with strong accuracy; its misses barely overlap with Haiku's |
| self-hosting | Not supported by this data | No self-hosted pipeline is in the key facts; test one before deciding |

## Caveats
- The benchmark uses 48 synthetic Norwegian documents. Real scans, other languages, and longer documents may behave differently.
- Degradation is simulated phone photos. Real damage such as stamps, handwriting, or folds is not covered.
- Miss counts are small, so the field-level patterns are suggestive rather than conclusive.
- Prices and latencies reflect current API conditions and can change.
