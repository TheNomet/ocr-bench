## Bottom line
Choose `image->agent` as the default: it is the most accurate pipeline on both clean (97.0%) and degraded (83.0%) pages and also the fastest. `unlimited-ocr->agent` is the cheapest pipeline, but only if the GPU stays busy. Its accuracy is slightly lower and it is markedly slower.

## What the facts say
- **Transcription quality is not the bottleneck.** The `unlimited-ocr` reader produces strong transcripts: lowest degraded CER, best degraded number recall, fewest invented numbers and no runaway pages. Even so, `unlimited-ocr->agent` trails `image->agent`. Converting pages to text appears to discard information the agent uses when it sees the image.
- **The missed fields support this.** `unlimited-ocr->agent` misses mostly `holiday_survey` fields (`activities`, `accommodation`, `pets`) and `receipt.merchant`. These look like form selections and header or logo content, where layout carries meaning that plain text loses.
- **`image->agent` fails differently.** Its top misses are `newsletter.new_fee` and `rental_contract.property_address`. This suggests the two pipelines' errors are not identical, which makes `unlimited-ocr->agent` useful as a cross-check.
- **Degraded pages hurt every pipeline.** Accuracy falls sharply for both, and the LLM readers (`sonnet-4.5`, `haiku-4.5`) invent many more numbers on degraded pages and each had a runaway page. On noisy input, `unlimited-ocr` is the more trustworthy transcriber.
- **The self-hosted model's cost advantage depends on utilisation.** The `unlimited-ocr` price is $1.49/hour whether busy or idle. Pipeline costs assume the best sustained throughput ($0.89/1k pages). Sending one request at a time costs $2.64/1k pages, so the "cheapest" ranking holds only under sustained load.

## Recommendation
| Goal | Choice | Why |
|---|---|---|
| Highest accuracy | `image->agent` | Most accurate on clean and degraded pages |
| Lowest latency | `image->agent` | Fastest pipeline (3.4 s/doc clean); one step with no OCR stage |
| Lowest cost | `unlimited-ocr->agent` | Cheapest pipeline ($5.87/1k docs clean), assuming the GPU is kept busy |
| Checking / second opinion | `unlimited-ocr->agent` alongside `image->agent` | Different miss patterns; `unlimited-ocr` transcripts invent the fewest numbers on degraded pages |
| When to self-host OCR | Sustained volume, cost-driven | Beats `haiku-4.5` transcription above ~307 pages/hour and `sonnet-4.5` above ~95 pages/hour; below that, idle GPU time removes the saving |

## Caveats
- The documents are synthetic and Norwegian: 48 documents across eight types. Real layouts, languages and handwriting may shift the results.
- "Degraded" means a simulated phone photo. Real capture noise such as glare, folds or skew may behave differently.
- Throughput and cost come from a single ml.g5.xlarge in this run. Other hardware, batching or pricing changes would move the break-even points.
- The accuracy gaps between the pipelines are modest. With this sample size, field-level miss counts are small and should be read as indicative rather than definitive.
