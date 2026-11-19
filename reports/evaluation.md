# Evaluation report


| Setting | Value |
|---|---|
| Generated | 2026-11-19T19:28:20+00:00 |
| Run tag | main |
| Documents evaluated | 10 |
| LLM provider / model | groq / openai/gpt-oss-120b (vision: qwen/qwen3.8-27b) |
| Routing mode | auto |
| Prompt version | v1 (fingerprint `f6ffe9b7209e`) |
| Pipeline version | 1.1.0 |
| Validation retry | True |
| Ground truth | data\ground_truth |

## Headline numbers

| Metric | Value |
|---|---|
| Labelled documents | 10 (9 invoices, 1 non-invoice(s)) |
| Full-document exact-match rate | 90.0% |
| Field accuracy on invoices (8 fields × invoices) | 98.6% |
| Classification F1 (invoice class) | 100.0% |
| Line-item F1 | 99.2% |
| Review-flag precision / recall | 100.0% / 100.0% |
| Processing failures | 0 |
| Avg processing time | 13134 ms |
| Avg LLM cost per document | $0.00217 |

## Field-level accuracy

Invoice fields are scored only on documents whose ground truth is an invoice, so non-invoices never add free correct nulls.

| Field | Evaluated on | N | Correct | Accuracy | Wrong | Missing | Spurious | Failed |
|---|---|---|---|---|---|---|---|---|
| is_invoice | all labelled documents | 10 | 10 | 100.0% | 0 | 0 | 0 | 0 |
| vendor | ground-truth invoices | 9 | 9 | 100.0% | 0 | 0 | 0 | 0 |
| invoice_number | ground-truth invoices | 9 | 9 | 100.0% | 0 | 0 | 0 | 0 |
| invoice_date | ground-truth invoices | 9 | 9 | 100.0% | 0 | 0 | 0 | 0 |
| currency | ground-truth invoices | 9 | 9 | 100.0% | 0 | 0 | 0 | 0 |
| line_items | ground-truth invoices | 9 | 8 | 88.9% | 1 | 0 | 0 | 0 |
| tax_total | ground-truth invoices | 9 | 9 | 100.0% | 0 | 0 | 0 | 0 |
| total | ground-truth invoices | 9 | 9 | 100.0% | 0 | 0 | 0 | 0 |

Non-invoice null compliance (all invoice fields null when the ground truth is a non-invoice): 100.0%.


## Invoice classification

| Expected \ Predicted | invoice | non-invoice | failed |
|---|---|---|---|
| **invoice** | 9 | 0 | 0 |
| **non-invoice** | 0 | 1 | 0 |

Precision 100.0% · Recall 100.0% · F1 100.0% · Accuracy 100.0%. Processing failures are counted as wrong (FN for invoices); never as correct non-invoices.


## Line items

| Metric | Value |
|---|---|
| Expected / predicted / matched rows | 66 / 65 / 65 |
| Row precision / recall / F1 | 100.0% / 98.5% / 99.2% |
| Invoices with every row correct | 8 / 9 |
| Invoices with the correct row count | 8 / 9 |
| Rows with the right amount but different description/qty | 0 |

## Validation

| Metric | Value |
|---|---|
| Schema validation success rate | 100.0% |
| Schema valid on first try (no repair call) | 100.0% |
| Financial validation pass rate | 88.9% (pass rate over predicted invoices whose totals and line amounts were all present) |
| Financial check outcomes | {"pass": 8, "mismatch": 1} |

## Human-review flag effectiveness

A document 'contains an error' if it is not a full exact match (or processing failed).

| Metric | Value |
|---|---|
| Documents flagged | 1 of 10 |
| Documents that contain errors | 1 |
| Incorrect results caught (flagged & wrong) | 1 |
| Incorrect results missed (not flagged & wrong) | 0 |
| Correct results flagged anyway | 0 |
| Review precision (% of flagged docs that contain errors) | 100.0% |
| Review recall (% of wrong docs that were flagged) | 100.0% |
| Missed documents | none |

## Performance and cost

| Metric | Value |
|---|---|
| Avg / median / max processing time | 13134 / 11702 / 28544 ms |
| Avg LLM cost per document | $0.00217 |
| Total LLM cost (first-run equivalent) | $0.02166 |
| Avg tokens per document | 3412 |

See `cost_analysis.md` for the full breakdown.


## Per-document results

| Document | GT | Status | Route | Correct | Exact | Wrong fields | Flagged | Review reasons | Cost |
|---|---|---|---|---|---|---|---|---|---|
| L01 | invoice | success | digital_text | 8/8 | yes | - | no | - | $0.00074 |
| L02 | invoice | success | vision | 8/8 | yes | - | no | - | $0.00379 |
| L03 | invoice | success | digital_text | 8/8 | yes | - | no | - | $0.00113 |
| L04 | invoice | success | digital_text | 8/8 | yes | - | no | - | $0.00168 |
| L05 | invoice | success | digital_text | 8/8 | yes | - | no | - | $0.00101 |
| L06 | invoice | success | digital_text | 8/8 | yes | - | no | - | $0.00091 |
| L07 | invoice | success | digital_text | 7/8 | no | line_items | yes | TOTAL_MISMATCH, LLM_RESULT_CONFLICT | $0.00466 |
| L08 | non-invoice | success | digital_text | 2/2 | yes | - | no | - | $0.00041 |
| L09 | invoice | success | vision | 8/8 | yes | - | no | - | $0.00368 |
| L10 | invoice | success | vision | 8/8 | yes | - | no | - | $0.00366 |

## Change vs. saved baseline

Baseline: tag `baseline`, routing `auto`, prompt `v1`, generated 2026-11-19T19:27:54+00:00.

| Metric | Baseline (baseline) | This run (main) | Δ |
|---|---|---|---|
| Exact-match rate | 90.0% | 90.0% | +0.0 pp |
| Field accuracy (invoices) | 98.6% | 98.6% | +0.0 pp |
| · is_invoice | 100.0% | 100.0% | +0.0 pp |
| · vendor | 100.0% | 100.0% | +0.0 pp |
| · invoice_number | 100.0% | 100.0% | +0.0 pp |
| · invoice_date | 100.0% | 100.0% | +0.0 pp |
| · currency | 100.0% | 100.0% | +0.0 pp |
| · line_items | 88.9% | 88.9% | +0.0 pp |
| · tax_total | 100.0% | 100.0% | +0.0 pp |
| · total | 100.0% | 100.0% | +0.0 pp |
| Classification F1 | 100.0% | 100.0% | +0.0 pp |
| Line-item F1 | 99.2% | 99.2% | +0.0 pp |
| Review precision | 100.0% | 100.0% | +0.0 pp |
| Review recall | 100.0% | 100.0% | +0.0 pp |
| Avg cost / doc | $0.00217 | $0.00217 | +0.00000 |

## Matching rules

```text
Field comparison rules used by the evaluation harness.

The rules are deliberately conservative: they only forgive differences that do not
change meaning (case, whitespace, punctuation, number formatting, legal-suffix
spelling). Anything else counts as an error.

| Field          | Normalisation before exact comparison                                        |
|----------------|-------------------------------------------------------------------------------|
| is_invoice     | boolean equality; a processing failure (null) is always wrong                |
| vendor         | lower-case, '&'->'and', punctuation removed, whitespace collapsed, and        |
|                | trailing legal-form words dropped (Pvt, Private, Ltd, Limited, Inc, LLC, LLP, |
|                | Co, Company, Corp, GmbH, PLC...): "Acme Pvt. Ltd." == "ACME" but             |
|                | "Acme Supplies" != "Acme Traders"                                              |
| invoice_number | whitespace removed, upper-cased, leading '#' or 'NO.' removed                |
| invoice_date   | parsed to a calendar date (ISO); compared as dates                           |
| currency       | ISO code after mapping unambiguous symbols (₹->INR, €->EUR, £->GBP, $->USD)  |
| tax_total/total| Decimal, |expected - predicted| <= 0.01                                        |
| line_items     | one-to-one maximum matching, order ignored; a pair matches only if            |
|                | description similarity >= 0.85, quantity equal, amount within 0.01           |
| nulls          | null == null is correct; null vs value is "missing", value vs null "spurious"|
```

## Regenerate

```bash
make eval            # extract (cached) + score + write all reports
make eval-score      # re-score existing outputs without calling the LLM
```

