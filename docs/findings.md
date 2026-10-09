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

## Run 2: this harness

See `results/<tag>/report.md` once it has run; a summary will be added here.
