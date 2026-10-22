## Bottom line
Choose `image->haiku-5.5` as the default: it is the cheapest and the most accurate on clean pages (100.0%), and it stays close to the top on degraded pages (98.2%). If your documents are mostly phone photos and every field matters, use `image->sonnet-5.5`, which is the most accurate on degraded pages (99.1%) but costs more.

## What the facts say
- Reading page images directly beats transcribing first. The self-hosted pipeline `unlimited-ocr->sonnet-5.5` uses the same extractor as `image->sonnet-5.5`, yet it is less accurate, slower and dearer. So the losses come from the transcript, not from the extractor.
- Degraded pages are where `unlimited-ocr` breaks down. Its accuracy falls to 78.7%, its number recall drops to 90.8%, and it invents 85 numbers on degraded pages. That is far more invented numbers than either Claude reader.
- `unlimited-ocr`'s most-missed fields are `holiday_survey.activities`, `holiday_survey.accommodation`, `holiday_survey.pets` and `receipt.merchant`. This suggests trouble with checkbox and form layouts and with logos or stylised headers, which a text transcript flattens away.
- The image pipelines share one hard field, `newsletter.new_fee`, plus occasional misses on rental-contract amounts. These look like ambiguous fields rather than reading errors.
- No reader produced runaway pages or hit the token limit. The failures here are misreads, not truncation.
- Self-hosting does not pay off against `haiku-5.5`. `unlimited-ocr` is not cheaper than `haiku-5.5` transcription at any volume one GPU handled. It is only cheaper than `sonnet-5.5` transcription above ~131 pages/hour sustained.

## Recommendation
| goal | choice | why |
|---|---|---|
| highest accuracy | `image->sonnet-5.5` for degraded pages, `image->haiku-5.5` for clean pages | Each is the top pipeline in its condition: 99.1% degraded, 100.0% clean. |
| lowest latency | `image->haiku-5.5` | It is the fastest on clean pages (1.5 s/doc). `image->sonnet-5.5` is the fastest on degraded pages. |
| lowest cost | `image->haiku-5.5` | It is the cheapest pipeline in both conditions ($0.37/1k docs clean). |
| checking/second opinion | Run `image->sonnet-5.5` against `image->haiku-5.5` | Both are strong, so where they disagree is a useful flag for review. |
| self-host a model | Don't, unless data cannot leave your infrastructure | `unlimited-ocr->sonnet-5.5` is the least accurate pipeline, and its GPU costs $1.49/hour even when idle. |

## Caveats
- The benchmark uses 48 synthetic Norwegian documents across 8 types. Real scans, handwriting and other languages may behave differently.
- "Degraded" means a simulated phone photo. Real capture problems such as glare, folds or skew may be harsher.
- Both extractors ran at temperature 1, and the missed-field counts are small, so rankings near the top could shift if the benchmark were rerun.
- Only 1 self-hosted model and 1 GPU size were tested. The result says nothing about other OCR models or hardware.
