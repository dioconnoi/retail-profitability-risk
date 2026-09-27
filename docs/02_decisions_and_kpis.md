# 02 - Locked Decisions & KPI Definitions

## Locked decisions (2026-09-24)
| # | Decision | Choice | Why (interview answer) |
|---|---|---|---|
| D1 | SQL engine | SQLite, built from the raw CSVs by a Python script | Zero setup, anyone can reproduce; window functions (3.25+) cover the need |
| D2 | Analysis window | 2017-01-01 to 2018-08-31 (20 full months) | 2016 is sparse, Sep/Oct 2018 truncated. Including them fakes a demand collapse and false seller inactivity |
| D3 | Revenue recognition | `order_status = 'delivered'` only | Conservative: revenue is not booked until the customer has the goods. Non-delivered orders feed the risk layer |
| D4 | Profit proxies | P1 = price - freight; P2 = price x (1 - 0.65) - freight | No cost field exists; the P2 rate is an assumption tested at 55-75% |
| D5 | Data cutoff | 2018-08-31 | Anchors "last 90 days" (2018-06-03 to 2018-08-31) and all time-based splits |
| D6 | Late-delivery model months | Exclude Jun-Aug 2018 purchases. Train 2017-01 to 2018-03, test 2018-04 to 2018-05 | Delivery-time regime shift from Jun 2018 (caveat 5). Chosen 2026-09-24 after initially proposing Jun-Jul on a wrong censoring rationale; the data showed Aug is at least as shifted. Other models and all descriptive KPIs still use the full window |

## Grain rules (prevents the classic join fan-out bug)
- **Revenue lives at order-item grain** (`order_items`). Never sum `payments` for revenue; one order has many payment rows (installments/vouchers) and joining them to items multiplies revenue.
- **Order-level flags** (late, cancelled, review) live at order grain. To attribute to a seller/category, join order -> items, and count each order once per seller (`COUNT(DISTINCT order_id)`).
- **Reviews** are deduplicated to one per order (latest `review_answer_timestamp`) before any join.
- **Customer** = `customer_unique_id`, not `customer_id` (Olist issues a new `customer_id` per order).

## KPI dictionary
Time base for period KPIs is `order_purchase_timestamp` (month = purchase month) unless stated.

### Profitability
| KPI | Formula | Grain |
|---|---|---|
| Gross revenue | SUM(price) over delivered items | item |
| Freight | SUM(freight_value) | item |
| Net revenue (P1) | SUM(price - freight_value) | item |
| Profit proxy (P2) | SUM(price x 0.35 - freight_value) | item |
| P2 margin % | P2 / Gross revenue | any |
| Orders | COUNT(DISTINCT order_id) | order |
| AOV | Gross revenue / Orders | order |
| Items per order | COUNT(items) / Orders | order |
| Freight ratio | Freight / Gross revenue | any |
| Repeat rate | Customers with >=2 delivered orders / customers | customer |

### Concentration & volatility (Risk/Quant)
| KPI | Formula |
|---|---|
| Share_i | Revenue_i / total revenue (i = seller or category) |
| HHI | SUM(Share_i^2) x 10,000 (<1,500 unconcentrated, 1,500-2,500 moderate, >2,500 high; US DOJ bands) |
| Top-N share | Revenue of top N / total (N = 10, 20% of sellers) |
| Effective N | 1 / SUM(Share_i^2) |
| Revenue volatility | Std dev of monthly log growth; also coefficient of variation of monthly revenue |
| Max drawdown | Largest peak-to-trough fall in monthly revenue |
| Downside scenarios | (a) lose top-K sellers, (b) demand shock of -10/-20%, (c) freight +15%, (d) late rate doubles; each reported as revenue and P2 impact |

### Operational risk
| KPI | Formula | Note |
|---|---|---|
| Late delivery | date(delivered_customer_date) > date(estimated_delivery_date), delivered orders with a delivery date | Rate = late / eligible. **Calendar-day comparison** (the estimate is a date at 00:00:00, so a same-day delivery is on time). Full-history timestamp comparison in the audit gave 8.1%; the in-window calendar-day rate is 6.8%. Both are reported so the definition change is transparent |
| Days late | MAX(0, delivered - estimated) in days | Severity |
| Cancellation rate | status in (`canceled`, `unavailable`) / all orders in window | Treated as failed fulfilment |
| Low-review rate | review_score <= 2 / reviewed orders | |
| Avg review score | mean of latest review per order | |
| Revenue at risk | Gross revenue of orders that are late, or scored 1-2 stars | Reported separately, and as a union without double counting |
| Lost revenue | Value of items on canceled/unavailable orders (price) | Never booked, shown as leakage |
| Delivery time | delivered_customer - purchase (days) | |

### Seller KPIs (feed risk flags and Q4)
Orders, revenue, P2, late rate, cancellation rate, avg review, share of revenue, active months, days since last order (vs 2018-08-31), revenue trend (last 90d vs prior 90d).

## Prediction targets (defined before modelling; features come later)
| Model | Unit | Target = 1 when | Prediction time |
|---|---|---|---|
| (a) Late delivery | delivered order | `fact_orders.is_late` (calendar-day rule above) | Order approval time; only features known then (seller, product weight/size, freight, estimated lead time, seller's history up to that date, distance) |
| (b) Low review | reviewed delivered order | review_score in (1, 2) | Delivery time; adds delivery outcome features (actual delay) as a second, separate feature set so the "at approval" model stays leak-free |
| (c) Seller failure | seller | Definition A or B below | Seller snapshot at a cutoff date; labels come from the period after it |

Seller failure definitions (both implemented; thresholds for B are proposed from the data during the seller step, not guessed now):
- **A: Inactivity.** Seller has >= 5 orders before the last 90 days and 0 orders in 2018-06-03 to 2018-08-31.
- **B: Quality.** Cancellation rate above the P90 of sellers with enough volume, or average review below a data-derived threshold, in the label period.

Leakage guards (apply to all three): time-ordered splits only; seller and product history features computed strictly from data before each order's timestamp; no post-outcome fields (`review_score`, `delivered_customer_date`) in a model that predicts them.

## Known data caveats (found in SQL-layer review, 2026-09-24)
1. **Censoring of late delivery.** 1,699 in-window orders (shipped/invoiced/processing/approved) never reached `delivered`; all were already past their promised date when the extract ended. Plain late rate (6.8%) is conditional on delivery; the censoring-adjusted rate (`v_late_rates`) counts them as late and is the more honest headline. Censoring is spread evenly (about 1-2% of each month's orders; June 2018 is the lowest at 0.75%), so it does NOT explain the low late rates of Jun-Aug 2018. That is a separate delivery-time regime shift (see caveat 5).
2. **Seller cancellation is under-attributed.** Only 448 of 1,182 failed orders have item rows (all `unavailable` orders have none), so `v_seller_perf.cancellation_rate` (~0.45%) is below the platform rate (1.19%). Definition B thresholds are set on the attributable rate and labelled as such.
3. **`v_seller_perf` is descriptive only.** It aggregates the whole window, so it contains the label period. Model features are rebuilt as-of a cutoff in Python (`src/olist_risk/features.py`, next step); `fact_order_risk` label columns (`is_late`, `review_score`, `is_low`, `at_risk`) are never used as features, and `at_risk` is a reporting metric, not a target.
4. **Review timing.** ~5,100 delivered orders have a review created before the recorded delivery date, so a review is not strictly post-delivery information. Model (b) therefore has two feature sets: "at approval" (no delivery info) and "at delivery" (adds actual delivery outcome).
5. **Delivery-time regime shift from June 2018 (concept drift).** Mean actual delivery time falls from 11-16 days (2017-01 to 2018-05) to 9.2 (Jun), 9.0 (Jul) and 7.7 (Aug) days, while the promised lead time swings from ~23 days to 27.8 (Jun), 19.7 (Jul) and 15.2 (Aug). Late rates in those months (1.2%, 3.4%, 6.2%) are therefore not comparable with earlier months, and a model trained on earlier months would be evaluated on a different process. Likely causes (untested): sample construction of the public extract, or a change in Olist's estimate logic. Treated as a modelling constraint, not explained away.

## Cost interpretation (used for the business-cost layer)
Expected cost = FN x cost of a missed risky order + FP x cost of an unnecessary intervention. Proposed defaults (all sensitivity-tested and explicitly assumptions): a late order's lost-review-revenue = observed revenue gap between late and on-time orders' repeat rate; intervention cost = expedited freight ~ 1x average freight. Final numbers are derived from the data in the modelling step.
