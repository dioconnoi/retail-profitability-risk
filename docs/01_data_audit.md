# 01 - Data Audit (Olist Brazilian E-Commerce)

Reproduce: `python -m olist_risk.audit` (see `src/olist_risk/audit.py`). Source: Kaggle `olistbr/brazilian-ecommerce`, CC-BY-NC-SA-4.0, 9 CSVs, 42.6 MB zipped.

## Verdict
Fits all four business questions. Referential integrity is clean (0 orphan keys across orders/items/sellers/products/customers). The issues below are handled in design, not blockers.

## Facts and their design implications

| Finding | Implication |
|---|---|
| 99,441 orders, 112,650 item rows, 3,095 sellers, 73 categories | Enough volume for SQL joins, HHI, classification |
| Orders span 2016-09 to 2018-10, but 2016 is sparse (329 orders in total) and Sep/Oct 2018 are truncated (20 orders) | **Analysis window = 2017-01 to 2018-08 (20 full months).** Everything outside is excluded and documented |
| Only ~20 monthly points | Forecast needs rolling-origin backtests and simple, defensible models (seasonal naive, ETS, SARIMA); also model weekly demand for more observations. Do not claim more than 20 points support |
| Data effectively ends 2018-08-29 | Seller-inactivity "final 90 days" is anchored to the data cutoff (2018-08-31), not the max timestamp |
| 96,478 delivered, 625 canceled, 609 unavailable, plus 1,107 shipped and ~620 other in-flight statuses | Revenue recognized on delivered orders (base case); cancellations/unavailable feed the risk layer |
| 775 orders have no items (mostly canceled/unavailable) | Zero revenue; keep in the risk (cancellation) analysis, drop from revenue joins |
| Late rate 8.1% on 96,470 delivered orders with a delivery date (8 delivered orders have no delivery date) | Class imbalance ~1:11 for the late-delivery model; use PR-AUC and a cost-based threshold |
| Review score: 1-2 stars = 14.7% (1 star 11.5%, 2 stars 3.2%) | Low-review target is ~1:6. 547 orders have >1 review, so keep the latest review per order |
| 96,096 unique customers among 99,441 customer_ids (~3% repeat) | Customer-level risk is mostly one-shot buyers. Flag customers via order-level risk and RFM-lite, and say so plainly. The strongest "who is high risk" story is at seller level |
| 1,278 multi-seller orders; payments are order-level | Revenue and profit are computed at item level (price). Payments are used only for reconciliation |
| Item revenue R$13.59M, freight R$2.25M (16.6% of revenue) | Freight is the only cost field in the data |
| 610 products with null category; 2 categories missing from the translation table | Map to "unknown" and add the 2 translations manually (`pc_gamer`, `portateis_cozinha_e_preparadores_de_alimentos`) |
| Geolocation has 1,000,163 rows, many per zip prefix | Aggregate to one centroid per zip prefix before joining |
| No cost or margin field | "Profit" is a proxy, see below |

## Profit proxy (both, as requested)
- **P1: contribution after freight** = item price - freight_value. Fully data-driven.
- **P2: assumed-margin profit** = price x (1 - assumed COGS rate) - freight_value. The rate is an explicit, sensitivity-tested assumption.

Both are labelled as proxies in every chart, measure and report section.
