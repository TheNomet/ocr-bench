## Bottom line
Choose `image->sonnet-5.5`. It is the most accurate pipeline on both clean pages (100.0%) and degraded pages (99.7%), and it is also the fastest. The self-hosted transcription step in `granite-docling->sonnet-5.5` loses information that the extractor cannot recover, especially on degraded pages (52.6%).

## What the facts say
- The two pipelines use the same extractor (Claude Sonnet 5.5). The accuracy gap therefore comes from the input. Reading page images directly keeps everything, while the `granite-docling` transcript drops or distorts content before extraction starts.
- `granite-docling` falls apart on degraded pages:
  - CER median rises from 0.079 to 0.283.
  - Number recall drops from 92.8% to 60.9%.
  - Invented numbers jump from 3 to 639.
  - 3 runaway pages and 8 pages at the token limit appear.
- The fields it misses most are numeric or list fields, such as `payslip.gross_pay` (10) and `receipt.total` (9), plus `holiday_survey.activities` and `holiday_survey.accommodation` (12 each). This suggests that tables, totals and checkbox-style content do not survive transcription.
- `image->sonnet-5.5` misses only `rental_contract.monthly_rent` (1). Its errors are therefore isolated, not systematic.
- Cost depends on the page variant:
  - On clean pages, `granite-docling->sonnet-5.5` is cheapest ($4.73/1k docs vs $7.06). That figure assumes the GPU is kept busy at concurrency 16. One request at a time, `granite-docling` costs $1.42/1k pages instead of $0.32.
  - On degraded pages, `image->sonnet-5.5` is the cheapest ($4.15/1k docs).
- Self-hosting does not help speed. `granite-docling->sonnet-5.5` takes 5.8 s/doc vs 1.7 s/doc.

## Recommendation
| goal | choice | why |
|---|---|---|
| highest accuracy | `image->sonnet-5.5` | Most accurate on clean and degraded pages |
| lowest latency | `image->sonnet-5.5` | Fastest: 1.7 s/doc clean, 1.4 s/doc degraded |
| lowest cost | `granite-docling->sonnet-5.5` (clean pages); `image->sonnet-5.5` (degraded pages) | Cheapest on clean is $4.73/1k docs, but only with a saturated GPU and at 78.7% accuracy; cheapest on degraded is $4.15/1k docs |
| checking / second opinion | `image->sonnet-5.5` | `granite-docling` invents numbers on degraded pages, so it is unreliable as a cross-check |
| self-host a model | Only if data cannot leave your infrastructure, pages are clean, and volume keeps the GPU busy | The GPU costs $1.20/hour whether busy or idle, and accuracy is far lower |

## Caveats
- The benchmark covers 48 synthetic Norwegian documents across 8 types. Real scans, other languages and other layouts may behave differently.
- "Degraded" means a simulated phone photo. Real capture defects could be harsher or milder.
- `granite-docling` costs depend on keeping throughput sustained. Bursty or low-volume workloads would raise its cost.
- Only 1 self-hosted reader was tested, with 1 extractor. Larger OCR models might narrow the gap.
