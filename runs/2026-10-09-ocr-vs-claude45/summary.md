## Bottom line
Choose `image->agent` as the default: it is the most accurate pipeline on both clean (97.0%) and degraded (83.0%) pages, and also the fastest. `unlimited-ocr->agent` is the cheapest only if the GPU is kept busy. Its savings do not buy accuracy.

## What the facts say
- **Better transcription did not mean better extraction.** `unlimited-ocr` is the strongest reader on degraded pages. It has the lowest CER, the highest number recall, the fewest invented numbers and no runaway pages. Yet `unlimited-ocr->agent` still trails `image->agent`. The loss seems to happen when the page becomes text, not in reading the characters.
- **Text loses layout.** The most-missed fields for `unlimited-ocr->agent` include `holiday_survey.activities`, `holiday_survey.accommodation`, `holiday_survey.pets` and `receipt.merchant`. These look like checkbox, form-layout or logo/header content that plain text represents poorly. `image->agent` misses different fields (`newsletter.new_fee`, `rental_contract.property_address`), so the two pipelines fail in different places.
- **Degradation is the main risk.** Both pipelines lose substantial accuracy on simulated phone photos, and all readers invent far more numbers there. Phone-captured input needs validation whichever pipeline you pick.
- **Runaway pages hit only the Claude transcribers.** `sonnet-4.5` and `haiku-4.5` each had one runaway page on degraded input; `unlimited-ocr` had none. This is a reliability point in the self-hosted model's favour.
- **Self-hosting is cheap only at volume.** The quoted cost assumes the GPU is kept busy ($0.89/1k pages). One request at a time it rises to $2.64/1k pages, because the GPU is billed at $1.49/hour whether busy or idle.

## Recommendation
| Goal | Choice | Why |
|---|---|---|
| Highest accuracy | `image->agent` | Most accurate on clean and degraded pages |
| Lowest latency | `image->agent` | Fastest pipeline: one step, no OCR stage |
| Lowest cost | `unlimited-ocr->agent` | Cheapest pipeline, but only with a busy GPU; slightly less accurate |
| Checking / second opinion | `unlimited-ocr->agent` alongside `image->agent` | Different most-missed fields; `unlimited-ocr` invents fewer numbers on degraded pages |
| When to self-host | `unlimited-ocr`, at sustained volume | Beats `sonnet-4.5` transcription above ~95 pages/hour and `haiku-4.5` above ~307 pages/hour sustained; also worth it if you need transcripts or have data-residency needs |

## Caveats
- The benchmark is small: 48 synthetic Norwegian documents across eight types. Real documents, other languages and other layouts may behave differently.
- "Degraded" means simulated phone photos. Real capture problems such as glare, folds and handwriting are not covered.
- Self-hosted costs depend on keeping the GPU busy. Bursty or low traffic erases the savings.
- Only one extractor, `sonnet-4.5`, was tested. The `haiku-4.5` and `sonnet-4.5` transcribers were not run as full pipelines, so their end-to-end accuracy is unknown.
