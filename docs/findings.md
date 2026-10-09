# Findings

## Run 1: prototype (15 documents, 30 page images)

This run used a predecessor of this harness with a smaller document set: five types,
including an earlier form layout that has since been replaced. It ran from a container in a
private VPC.
- **OCR model:** Unlimited-OCR (rev `07dea832`) on SageMaker `ml.g5.2xlarge` (1× A10G 24 GB,
  about $1.61/h in eu-north-1).
- **LLMs:** Claude Sonnet 4.5 and Haiku 4.5 through an OpenAI-compatible gateway, with cache
  busting.

### Speed

| | Unlimited-OCR (1 GPU) | Haiku 4.5 | Sonnet 4.5 |
|---|---|---|---|
| p50 per page, concurrency 1 | **3.2 s** | 4.5 s | 9.4 s |
| pages/min at concurrency 32 | 48.6 (GPU saturated, p50 36 s) | **312** | 138 |

A single GPU saturates at about 48 pages/min. Hosted LLM latency stayed flat with
concurrency, and there was no throttling up to 32 parallel requests.

### Transcription quality

| | Unlimited-OCR | Haiku | Sonnet |
|---|---|---|---|
| clean: number recall | 100% | 100% | 100% |
| clean: CER | 0.039 | 0.001 | 0.000 |
| degraded: number recall | **96.7%** | 94.6% | 94.2% |
| degraded: invented numbers | **2** | 10 | 9 |

- **Unlimited-OCR:** misreads Norwegian letters (ø→o, Ø→∅). On key/value forms it merges
  every label into one table cell and every value into another, so the pairing is lost. It
  reads by layout column, which inflates CER on tilted receipts (all labels first, then all
  amounts). Even so, it was the most faithful on numbers in degraded photos.
- **Claude:** its mistakes on degraded input were the riskier kind, plausible substitutions
  (`2,7L`→`3,7L`, `TOTALT`→`Tomat`). Sonnet also added an unrequested "Note: …" paragraph.

### Cost per 1,000 pages (transcription only)

| Sonnet | Haiku | Unlimited-OCR at full utilisation |
|---|---|---|
| ≈ $10.3 | ≈ $3.4 | ≈ $0.55 |

The GPU bills while idle, so it beats Haiku only above about 470 pages/hour and Sonnet above
about 160 pages/hour, unless it scales to zero.

### Operational lessons

- EC2 GPU instances on a stock Deep Learning AMI were stopped automatically by a landing-zone
  policy that allows only approved AMIs, which is why the model now runs on SageMaker.
- GPU capacity was scarce. One instance type alone failed after 31 minutes with
  `InsufficientInstanceCapacity`; instance pools fixed it.
- Cold start from zero took about 14 minutes (host plus a 9 GB image pull). vLLM itself
  starts in about 40 s.
- The gateway cached identical requests, so the first batch numbers were cache hits at
  0.06 s. Hence `cache_bust`.

## Run 2: this harness, `suite` plan (48 documents, 108 page images)

The OCR model ran on SageMaker `ml.g5.xlarge` (1× A10G, $1.49/h in eu-north-1); the instance
pools placed it there. The LLMs were Claude Sonnet 4.5 and Haiku 4.5 through an
OpenAI-compatible gateway with cache busting. The extraction agent was Sonnet 4.5. Everything
ran from Fargate in a private VPC.

### Speed

| | Unlimited-OCR | Haiku 4.5 | Sonnet 4.5 |
|---|---|---|---|
| p50 per page, concurrency 1 | **6.2 s** | 8.0 s | 14.1 s |
| best throughput | 28 pages/min (conc 32, p50 65 s) | **137 pages/min** (conc 32) | 43 pages/min (conc 16) |

- **This GPU host was about 2× slower than run 1** (`ml.g5.2xlarge`, 48 pages/min,
  3.2 s p50). Both hosts have the same A10G, but `ml.g5.xlarge` has half the vCPUs.
  Image preprocessing is the likely bottleneck, but that hasn't been measured.
- To pin the faster host, put `ml.g5.2xlarge` first in `instance_pools`.
- At concurrency 16 one request hit the 60 s SageMaker limit.

### Transcription (degraded pages)

| | Unlimited-OCR | Haiku | Sonnet |
|---|---|---|---|
| CER median | **0.044** | 0.081 | 0.079 |
| number recall | **92.6%** | 87.8% | 88.3% |
| invented numbers (runaway pages excluded) | **82** | 120 | 137 |
| runaway outputs (repetition loops) | **0** | 1 | 1 |

- On clean pages all three get ≥99% of numbers. Sonnet has the best text (median CER
  0.002); Unlimited-OCR still drops Norwegian letters.
- Each Claude model produced **one runaway page**: Sonnet on a degraded payslip (19.6× the
  page length, `0303…`), Haiku on a degraded rental contract. That alone distorts mean CER.

### Field extraction (agent = Sonnet)

| pipeline | clean | degraded | end-to-end s | $ / 1k docs |
|---|---|---|---|---|
| `gateway-sonnet->agent` | **98.8%** | **85.7%** | 19.5–21.8 | 22–23 |
| `image->agent` | 97.0% | 83.0% | **3.4–3.6** | **6.8–7.4** |
| `ocr->agent` | 95.7% | 81.5% | 9.0–9.7 | 7.9 |

- Feeding the agent Unlimited-OCR text is **less accurate** than letting it read the images
  (−1.3 pp clean, −1.5 pp degraded) and **slower** (two hops). On this GPU host it doesn't
  cost less either.
- Its weak spots are checkbox and form fields (`holiday_survey.activities`, `pets`,
  `accommodation`) and receipt merchant names, which matches the label/value pairing
  problem.
- Transcribing with Sonnet first, then extracting, is the most accurate, but about 3× the
  cost and about 6× the latency of `image->agent`.

### Verdict for this model

As a **replacement** for sending images to Claude, Unlimited-OCR loses on accuracy and
latency and doesn't win on cost at this scale. As an **independent second reading** it is
useful: it is the most faithful on numbers in poor photos, never looped, and gives a
transcript to check LLM quotes against.

### Harness fixes from this run

- **Ground truth for `rental_contract.property_address`** now includes the flat number,
  which the document prints. Every pipeline had been "wrong" in the same way.
- **Report:** shows median CER and runaway counts, so one loop can't dominate the means.
- **Fargate sidecars:** the exit code is read from the `bench` container by name, because
  accounts may inject sidecar containers into every task.
