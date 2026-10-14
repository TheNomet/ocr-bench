## Bottom line
Choose `image->agent` for production extraction: it is the most accurate pipeline on both clean (97.0%) and degraded (83.0%) pages and also the fastest. `unlimited-ocr->agent` is the cheapest at $5.87/1k docs, but only when its GPU is kept busy, and it gives up some accuracy.

## What the facts say
- Both pipelines use the same extractor (Claude Sonnet 4.5), so the accuracy gap comes from the input. Sonnet 4.5 reading the page images beats it reading the `unlimited-ocr` transcript, even though `unlimited-ocr` is a strong reader.
- As a reader, `unlimited-ocr` does best on degraded pages. It has the lowest CER median (0.043), the highest number recall (90.8%), the fewest invented numbers (85) and 0 runaway pages. `haiku-4.5` and `sonnet-4.5` each had 1 runaway page. Better transcription still did not produce better extraction.
- The fields `unlimited-ocr->agent` misses most (`holiday_survey.activities`, `holiday_survey.accommodation`, `holiday_survey.pets`, `receipt.merchant`) look like layout or visual content. Checkboxes and logos tend to get lost when a page is flattened to text. `image->agent` misses different fields (`newsletter.new_fee`, `rental_contract.property_address`), so the 2 pipelines fail in different places.
- Degraded pages hurt every pipeline and every reader, and invented numbers rise sharply for all readers. Photo quality is the biggest risk to accuracy.
- The self-hosted model's cost advantage depends on load. The GPU costs $1.49/hour busy or idle. Its rate is $0.89/1k pages at full throughput but $2.64/1k pages one request at a time. It also adds latency: `unlimited-ocr->agent` takes 9.0 s/doc against 3.4 s/doc for `image->agent`.

## Recommendation
| goal | choice | why |
|---|---|---|
| highest accuracy | `image->agent` | Most accurate on clean and degraded pages |
| lowest latency | `image->agent` | Fastest pipeline (3.4 s/doc clean, 3.6 s/doc degraded) |
| lowest cost | `unlimited-ocr->agent` | Cheapest ($5.87/1k docs clean), assuming the GPU stays busy |
| checking/second opinion | `unlimited-ocr->agent` | Different miss pattern and fewest invented numbers in its transcripts, so it is useful for cross-checking amounts |
| when to self-host | `unlimited-ocr` | Only with high sustained volume: it beats `haiku-4.5` transcription above ~282 pages/hour and `sonnet-4.5` transcription above ~99 pages/hour. It is capped at ~1686 pages/hour on this instance. |

## Caveats
- The data is 48 synthetic Norwegian documents across 8 types. Real documents, other languages and other layouts may behave differently.
- The degraded pages are simulated phone photos. Real capture problems such as glare, folds or handwriting may hit the 2 inputs differently.
- Costs assume the stated prices and, for `unlimited-ocr`, a fully used ml.g5.xlarge. Idle time or a different instance changes the cost ranking.
- `sonnet-4.5->agent` was left out of the key facts because one model both transcribed and extracted. The comparison therefore covers only 2 pipelines with 1 extractor.
