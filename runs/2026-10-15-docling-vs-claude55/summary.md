## Bottom line
Choose `image->sonnet-5.5`. It is the most accurate pipeline on both clean (100.0%) and degraded (99.7%) pages, and also the fastest. The `granite-docling` transcription step adds errors that the extractor cannot recover from, especially on phone-photo pages.

## What the facts say
- **The transcript limits accuracy.** Both pipelines use the same extractor (Claude Sonnet 5.5), so the accuracy gap comes from the input. `granite-docling->sonnet-5.5` reaches 78.7% clean and 52.6% degraded, and the extractor can only work with what the transcript contains.
- **Degradation breaks `granite-docling`.** On degraded pages its CER median rises from 0.079 to 0.283 and number recall falls from 92.8% to 60.9%. Invented numbers jump from 3 to 639, and 3 pages ran away. Reading the images directly barely changes between clean and degraded pages.
- **The most-missed fields point to lost structure and lost numbers.**
  - `holiday_survey.activities` and `holiday_survey.accommodation` are the top misses for `granite-docling->sonnet-5.5`. This suggests survey layouts such as checkboxes or tables do not survive transcription.
  - `payslip.gross_pay` and `receipt.total` are also often missed, which matches the drop in number recall.
- **Token-limit pages signal looping or overlong output.** `granite-docling` hit the token limit on 2 clean pages and 8 degraded pages. That output becomes noisy text for the extractor.
- **`image->sonnet-5.5` has only 1 recurring miss.** It missed `rental_contract.monthly_rent` (1), which is too few to show a pattern.
- **Self-hosting does not help here.** `granite-docling` is slower (5.8 s/doc versus 1.7 s/doc clean) and less accurate. Its GPU costs $1.20/hour whether busy or idle.

## Recommendation
| goal | choice | why |
|---|---|---|
| highest accuracy | `image->sonnet-5.5` | Most accurate on clean and degraded pages |
| lowest latency | `image->sonnet-5.5` | Fastest pipeline: 1.7 s/doc clean, 1.4 s/doc degraded |
| lowest cost | unknown | No price is configured for either pipeline, so neither can be called cheapest. `granite-docling` transcription alone is $0.32/1k pages at full load, but the extractor cost is still missing. Set `price_per_mtok` to compare. |
| checking / second opinion | Re-run `image->sonnet-5.5` on low-confidence fields | `granite-docling->sonnet-5.5` is too unreliable to serve as a check, especially on degraded pages |
| when to self-host | Not on this evidence | Consider it only if data must stay on your own infrastructure, and expect much lower accuracy on photos |

## Caveats
- The benchmark uses 48 synthetic Norwegian documents across 8 types. Real scans, handwriting or other languages may behave differently.
- "Degraded" is a simulated phone photo. Real capture problems such as glare, folds or skew may hurt more or less than the simulation.
- Cost is entirely unpriced, so the choice may change once token prices are set. The `granite-docling` cost also depends heavily on keeping the GPU busy ($0.32 versus $1.42 per 1k pages).
- Only 1 self-hosted reader was tested. A stronger OCR model might narrow the gap.
