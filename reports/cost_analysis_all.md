# LLM cost analysis


| Setting | Value |
|---|---|
| Generated | 2026-11-19T19:33:20+00:00 |
| Run tag | all-outputs |
| Documents evaluated | 20 |
| LLM provider / model | groq / openai/gpt-oss-120b (vision: qwen/qwen3.8-27b) |
| Routing mode | auto |
| Prompt version | v1 (fingerprint `f6ffe9b7209e`) |
| Pipeline version | 1.1.0 |
| Validation retry | True |
| Ground truth | n/a (cost only) |

## Pricing assumptions

| Model | Input / 1M | Cached input / 1M | Output / 1M |
|---|---|---|---|
| groq:openai/gpt-oss-120b | $0.15 | $0.075 | $0.6 |
| groq:qwen/qwen3.8-27b | $0.8 | $0.8 | $4.0 |

Cost per call = (prompt tokens − cached tokens) × input rate + cached tokens × cached rate + completion tokens × output rate, using the token counts returned by the API (`usage`). Image tokens are included in prompt tokens by the provider. Rates are configuration (`PRICE_*` / `MODEL_PRICING_JSON`) and must be checked against the provider's current price list.


## Totals

| Metric | Value |
|---|---|
| Documents | 20 (20 needed LLM calls) |
| Total API spend to extract these documents | $0.03842 |
| Spent during this run (cache hits cost $0) | $0.00732 (14 cached) |
| Average cost per document | $0.00192 |
| Minimum / maximum cost per document | $0.00041 / $0.00741 |
| Average input tokens (of which cached) | 2,381 (192) |
| Average output tokens | 804 |
| Average LLM latency per document | 2,469 ms |
| Average end-to-end processing time | 8,993 ms |
| Retry / repair attempts | 2 costing $0.00759 |
| HTTP calls (incl. transport retries) | 36 |
| Projected cost per 1,000 similar documents | $1.92 |
| Evaluation budget guard | $2.00 |

## Cost by route

| Route | Documents | Avg cost |
|---|---|---|
| digital_text | 15 | $0.00105 |
| vision | 5 | $0.00454 |

## LLM cost vs. hosting cost

These figures are **LLM provider charges only**. EC2/EBS/public-IPv4/data-transfer charges are billed by AWS separately (see README → *AWS cost awareness*). AWS promotional credits generally do not pay third-party LLM API bills.

