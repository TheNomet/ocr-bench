## Bottom line
Use `image->haiku-5.5` for clean pages: it ties `image->sonnet-5.5` at 100.0% and is the cheapest at $0.37/1k docs. For degraded pages (phone photos), use `image->sonnet-5.5`. It is the most accurate on degraded pages at 99.7%, and its lead over Haiku there is **outside** the run-to-run range.

## What the facts say
- **Clean pages:** both pipelines reach 100.0% with a 0.0 pts range, so there is no accuracy reason to pay for Sonnet. Cost is the deciding factor: $0.37 vs $7.07 per 1k docs.
- **Degraded pages:** this is where the 2 pipelines differ. `image->haiku-5.5` drops to 97.9% and `image->sonnet-5.5` holds 99.7%. The 1.4 pts gap exceeds the 0.6 pts repeat range, so it is a real difference.
- **Haiku's reading on degraded pages:** `haiku-5.5` shows number recall of 94.8%, 11 invented numbers and 2 pages at the token limit. `sonnet-5.5` shows 99.3% and 3. Haiku is also less stable across repeats: 77.8% of its pages were not identical between runs, and its number recall ranged 94.8%–98.6%.
- **Runaway pages:** none for either reader.
- **Most-missed fields:** Haiku's misses cluster on `newsletter.new_fee` (3), plus single receipt fields. Both pipelines missed `rental_contract.monthly_rent` once. The misses are numeric and amount fields, which are the fields that matter most.
- **Speed:** both pipelines take 1.5 s/doc on clean pages. Latency is not a real differentiator.
- **Self-hosted models:** none were included among the pipelines shown, so these facts give no evidence either way.

## Recommendation
| goal | choice | why |
|---|---|---|
| highest accuracy | `image->sonnet-5.5` | Ties on clean pages and is most accurate on degraded pages (99.7%), a gap outside the noise range |
| lowest latency | `image->haiku-5.5` (clean); `image->sonnet-5.5` (degraded) | Fastest per the key facts: 1.5 s/doc clean, 1.3 s/doc degraded; the difference is negligible |
| lowest cost | `image->haiku-5.5` | Cheapest: $0.37/1k docs clean, $0.27/1k degraded |
| checking/second opinion | `image->sonnet-5.5` re-reads Haiku outputs on degraded pages or numeric fields | Fewer invented numbers and higher number recall |
| self-hosting | Not supported by these facts | No self-hosted pipeline was among those shown; its cost and accuracy are unknown |

## Caveats
- The data is 48 synthetic Norwegian documents across 8 types. Real scans, handwriting or other languages may behave differently.
- "Degraded" here means a simulated phone photo. Real capture defects (glare, folds, skew) could widen the gap between the 2 pipelines.
- Miss counts are tiny (single digits), so the field-level patterns are suggestive rather than conclusive.
- Prices and latencies reflect this run's setup. Recheck them at your own volume and concurrency.
