<!-- LLM comment by opus-5.5 (anthropic.claude-opus-5-5) on the computed key facts; 2316 in / 1711 out tokens; unverified numbers: none -->
## Bottom line
Choose `image->sonnet-5.5`: it is the most accurate pipeline on degraded pages (99.1%) and the fastest there (1.7 s/doc), and real-world input looks more like degraded phone photos than clean scans. `image->haiku-5.5` is a strong alternative on clean pages. None of the pipelines in the cost note has a price, so cost cannot decide the choice yet.

## What the facts say
- **Reading images directly works best.** Both image pipelines beat the OCR route because the agent sees the page itself. A separate transcription step can only lose information.
- **The self-hosted `ocr` reader degrades badly on phone-photo pages.** On degraded pages, compared with the Claude readers, it has:
  - a higher CER median (0.044),
  - lower number recall (92.6%),
  - far more invented numbers (82).

  Invented numbers are the most dangerous failure, because they look plausible in extracted fields.
- **`ocr->sonnet-5.5` misses fields in a clear pattern.** Its misses cluster on `holiday_survey` fields (`activities`, `accommodation`, `pets`) and on `receipt.merchant`. This suggests the transcript loses layout cues such as checkboxes and stylised merchant headers. The agent cannot recover these from text alone.
- **Both image pipelines miss `newsletter.new_fee`.** A miss shared by both models points to an ambiguous field definition or label, not to a reading problem. It is worth checking the field description and ground truth.
- **No reader produced runaway pages.** The `ocr` reader also has the lowest throughput (29 pages/min at concurrency 16, against 175 for `sonnet-5.5`). Self-hosting brings no speed benefit here.

## Recommendation

| Goal | Choice | Why |
|---|---|---|
| Highest accuracy | `image->sonnet-5.5` (degraded); `image->haiku-5.5` (clean) | Most accurate on each page variant: 99.1% degraded, 100.0% clean |
| Lowest latency | `image->haiku-5.5` (clean, 1.5 s/doc); `image->sonnet-5.5` (degraded, 1.7 s/doc) | Fastest pipeline on each variant |
| Lowest cost | No pipeline can be named | No priced pipeline exists; cost is unknown for `image->haiku-5.5`, `image->sonnet-5.5` and `ocr->sonnet-5.5`. Set `price_per_mtok` and rerun before ranking. |
| Checking / second opinion | Run `image->haiku-5.5` alongside `image->sonnet-5.5` and flag disagreements | Two independent strong readers with mostly different misses; not `ocr->sonnet-5.5`, given its invented numbers |
| Self-host the OCR model | Only if data must stay on your own infrastructure | It is less accurate and slower than the image pipelines; any cost advantage is unproven without pricing |

## Caveats
- The benchmark uses 48 synthetic Norwegian documents. Real scans, other languages and handwriting may behave differently.
- "Degraded" is a simulated phone photo. Real capture problems (glare, folds, skew) may hurt every reader more.
- All models ran at temperature 1, so single-run results carry sampling noise. The small miss counts may not be stable.
- The facts give no accuracy figures for `sonnet-5.5->sonnet-5.5`. It cannot be judged here.
