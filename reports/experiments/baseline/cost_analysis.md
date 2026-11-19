# LLM cost analysis


| Setting | Value |
|---|---|
| Generated | 2026-11-19T19:27:54+00:00 |
| Run tag | baseline |
| Documents evaluated | 10 |
| LLM provider / model | groq / openai/gpt-oss-120b (vision: qwen/qwen3.8-27b) |
| Routing mode | auto |
| Prompt version | v1 (fingerprint `f6ffe9b7209e`) |
| Pipeline version | 1.1.0 |
| Validation retry | True |
| Ground truth | data\ground_truth |

## Pricing assumptions

| Model | Input / 1M | Cached input / 1M | Output / 1M |
|---|---|---|---|
| groq:openai/gpt-oss-120b | $0.15 | $0.075 | $0.6 |
| groq:qwen/qwen3.8-27b | $0.8 | $0.8 | $4.0 |

Cost per call = (prompt tokens − cached tokens) × input rate + cached tokens × cached rate + completion tokens × output rate, using the token counts returned by the API (`usage`). Image tokens are included in prompt tokens by the provider. Rates are configuration (`PRICE_*` / `MODEL_PRICING_JSON`) and must be checked against the provider's current price list.


## Totals

| Metric | Value |
|---|---|
| Documents | 10 (10 needed LLM calls) |
| Total API spend to extract these documents | $0.02166 |
| Spent during this run (cache hits cost $0) | $0.00000 (10 cached) |
| Average cost per document | $0.00217 |
| Minimum / maximum cost per document | $0.00041 / $0.00466 |
| Average input tokens (of which cached) | 2,480 (0) |
| Average output tokens | 932 |
| Average LLM latency per document | 2,908 ms |
| Average end-to-end processing time | 13,134 ms |
| Retry / repair attempts | 1 costing $0.00382 |
| HTTP calls (incl. transport retries) | 20 |
| Projected cost per 1,000 similar documents | $2.17 |
| Evaluation budget guard | $2.00 |

## Cost by route

| Route | Documents | Avg cost |
|---|---|---|
| digital_text | 7 | $0.00150 |
| vision | 3 | $0.00371 |

## LLM cost vs. hosting cost

These figures are **LLM provider charges only**. EC2/EBS/public-IPv4/data-transfer charges are billed by AWS separately (see README → *AWS cost awareness*). AWS promotional credits generally do not pay third-party LLM API bills.

