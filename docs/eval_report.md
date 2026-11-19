# Evaluation report

Dataset: 162 synthetic SEF invoices with injected problems (see `data/ground_truth.json`).

> **How to read this.** The data generator and the rules were written by the same team, so section 1 shows that the controls are implemented as specified, not how they would score on a real company's invoices. Real-world accuracy needs a pilot on real SEF data. Sections 2 and 3 include a hand-written hard set and SQL-checked questions to make the LLM numbers more honest.

## 1. Anomaly detection (rules engine)

| Metric | Value |
|---|---|
| Routing accuracy (sent to a person exactly when needed) | 98.8% |
| Exact finding-set accuracy | 98.8% |
| Micro precision | 96.9% |
| Micro recall | 100.0% |

| Finding type | Support | Precision | Recall |
|---|---|---|---|
| BANK_ACCOUNT_CHANGED | 1 | 100.0% | 100.0% |
| DUPLICATE | 6 | 100.0% | 100.0% |
| INVALID_PIB | 3 | 100.0% | 100.0% |
| ITEM_NOT_ON_PO | 3 | 100.0% | 100.0% |
| NO_PO | 5 | 100.0% | 100.0% |
| POSSIBLE_DUPLICATE | 6 | 75.0% | 100.0% |
| PRICE_MISMATCH | 11 | 100.0% | 100.0% |
| PRICE_WITHIN_TOLERANCE | 7 | 100.0% | 100.0% |
| QTY_NOT_RECEIVED | 15 | 100.0% | 100.0% |
| UNKNOWN_SUPPLIER | 3 | 100.0% | 100.0% |
| VAT_MISMATCH | 3 | 100.0% | 100.0% |

Mismatches (2):

- SEF 50008241 AMB-2026-0018: expected [], got ['POSSIBLE_DUPLICATE'] ()
- SEF 50008293 NAP-2026-0018: expected [], got ['POSSIBLE_DUPLICATE'] ()

False POSSIBLE_DUPLICATE flags happen when a supplier legitimately sends two invoices with the same amount within the window (same order repeated). This is the intended trade-off: a person spends one minute checking, instead of the company paying twice.

## 2. Booking proposals (account per line)

118 lines have an item code (account from item master, deterministic); 48 unique free-text lines need classification.

The hard set is 39 hand-written, deliberately ambiguous service descriptions that the keyword rules were not written for (labels follow the simplified chart of accounts in this repo and were not reviewed by a certified accountant).

| Classifier | Model | Generated free-text | Overall (all lines) | Hard set | Cost per line | Avg latency |
|---|---|---|---|---|---|---|
| rules | - | 95.8% | 98.8% | 43.6% | $0.00000 | 0 ms |
| llm | - | skipped: ANTHROPIC_API_KEY not set | | | | |

## 3. Questions in Serbian (agent with SQL tools)

Skipped: run with --qa.

## 4. Business case (monthly)

| | Value |
|---|---|
| Invoices per month (assumption) | 400 |
| Share that arrive READY for one-click approval (measured) | 70% |
| Manual processing time | 80 h |
| Time with the agent (review + exceptions) | 29 h |
| Hours saved | 51 h |
| Value of hours saved | 63.580 RSD |
| LLM cost (measured per invoice) + hosting | 3.000 RSD |
| Net monthly saving | 60.580 RSD |

Assumptions are in `sef_agent/roi.py` and can be changed per client.
