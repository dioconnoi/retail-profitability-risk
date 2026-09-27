# Retail Profitability, Demand Forecasting and Operational Risk
### An analysis of 96,211 delivered orders on the Olist Brazilian marketplace (Jan 2017 to Aug 2018)

## Executive summary
- **Profit is thin and freight-driven.** Delivered revenue was R$13.18M, but freight cost R$2.19M against a profit proxy of R$2.42M. In the most recent quarter monthly freight (R$154k) exceeded the entire profit proxy (R$145k), so each 1% rise in freight rates costs about 1.07% of profit. Demand shocks are proportionally less dangerous.
- **Growth has stalled.** Revenue is 1.7x its 2017 monthly level but sits 12% below spring 2018. Orders are forecast to hold near 6,450 a month through autumn 2018.
- **Lateness is a promise-and-route problem.** A late order is 6.7x as likely to receive a 1-2 star review (62% against 9%). Lateness is best explained by promised lead time and customer state, and only weakly by seller history.
- **Sellers are fragmented but skewed.** No single seller matters (HHI 36) yet the top 20% earn 82% of revenue. Among active sellers, a simple "days since last order" rule predicts inactivity as well as a machine-learning model does.
- **Most predictive models add modest value.** Lift on the riskiest 10% of flags is 2 to 4x, and the money is small unless an intervention costs almost nothing. The honest recommendation is cheap, automated, rule-first operations.

## 1. Problem
Four questions from a marketplace operator's point of view:
1. Which categories, sellers and regions drive profit, and how concentrated is revenue?
2. What does monthly demand look like, and can it be forecast?
3. Where is operational risk (late deliveries, cancellations, low reviews), and what does it put at stake?
4. Which sellers are highest risk, and how should they be flagged?

## 2. Data and method
**Data.** Olist public dataset (9 relational tables, about 99k orders, 3,095 sellers, 73 categories). It has no cost, margin, refund or churn data, so every profit and cost figure below is a proxy or an assumption.

**Decisions (each defended in `docs/02_decisions_and_kpis.md`).** Analysis window January 2017 to August 2018 (20 full months; 2016 and late 2018 are sparse or truncated). Revenue recognised on delivered orders only. Profit proxy P1 = price minus freight; P2 also subtracts an assumed 65% cost of goods (sensitivity-tested from 55% to 75%). The late-delivery model excludes June to August 2018 because delivery times shifted from 13 to 9 days in that period.

**Method.**
- **SQL layer (SQLite):** typed dimensions and facts, KPI views, and 22 tests that reconcile every headline number against an independent pandas calculation.
- **Forecasting:** rolling-origin backtest of six simple methods on monthly order counts.
- **Risk metrics:** volatility, drawdown, HHI concentration, and stress tests.
- **Predictive models:** late delivery, 1-2 star review, and seller failure under two definitions (inactivity and quality). Features are strictly time-causal, splits are chronological with an embargo, the primary model was fixed before seeing test results, and an independent review checked for leakage. Total test suite: 100+ tests.
- **Delivery:** interactive Power BI model and dashboard spec, the SQL and Python source, and this report.

## 3. Findings

### Q1. What drives profit, and how concentrated is it?
- Twelve categories earn **69%** of revenue. Five of them earn **below-platform margin** (platform 18.4%): bed_bath_table 15.3%, furniture_decor 11.3%, housewares 11.8%, garden_tools 14.4%, sports_leisure 17.9%. Freight, not COGS, separates them: it takes 24% of price in furniture_decor against 8% in watches_gifts.
- The category ranking by margin is identical at every COGS rate because margin equals (1 minus COGS) minus freight ratio; only the level moves. At 75% COGS, 15 categories lose money instead of 1 at 55%.
- **Regions:** São Paulo is 38% of revenue (state HHI 1,897, moderate). Other large states pay 17 to 20% freight against 14% in São Paulo.
- **Sellers:** 2,945 active sellers; HHI 36 (unconcentrated); top 10 sellers hold 13% but the top 20% hold 82%. The top 5% of sellers earn 51% of revenue and 62% of profit, so the best sellers are also the most profitable.

### Q2. Demand and forecast
- Monthly orders climbed through 2017, spiked in November 2017, and have plateaued near 6,000 to 7,000 since.
- **No method beat a 3-month average.** Four simple methods tie at 7 to 8% weighted error on 21 backtest forecasts; seasonal-naive fails at 54% because 2017 was a scale-up year.
- **Forecast (combo method):** 6,444 / 6,455 / 6,463 orders for Sep, Oct, Nov 2018 (about R$0.90M revenue a month), 80% band 5,271 to 7,925 by November.
- **What it cannot claim:** twenty months cannot identify seasonality, and the backtest never forecasts a holiday. The November figure (10,920 orders) is a labelled scenario from one observation, not a forecast.

### Q3. Operational risk
- Late rate 6.8% of delivered orders (8.4% counting 1,699 orders that never arrived although their promised date passed). Low-review rate 12.8%. Cancellation rate 1.2%.
- **R$2.32M (17.6%) of delivered revenue** sits on orders that were late or scored 1-2 stars (counted once). Lost revenue on canceled orders that carried items was R$92k (0.7%).
- A **delivery-time regime shift** from June 2018 (actual delivery 13 to 9 days) means late rates from then are not comparable with earlier months. Cause unknown; treated as a constraint.
- **Stress tests (monthly, on the Jun-Aug 2018 baseline):** freight +15% costs R$23k of profit (-16%); demand -10% costs R$14k (-10%); losing the top 10 sellers costs R$23k (-16%); losing the top 5% (148 sellers) costs R$90k (-62%). Doubling the late rate leaves profit unchanged but exposes about R$52k more monthly revenue.

### Q4. Highest-risk sellers and customers
- **Customers:** only 3.0% buy more than once, so customer-level risk scoring has little to work with. Risk sits at seller and order level.
- **Late delivery model (April to May 2018, unseen):** ROC-AUC 0.72; the riskiest 10% of orders show 17% late against 5.5% base (3.1x). Strongest drivers are customer state, promised lead time and route history.
- **Low-review model:** ROC-AUC 0.63 before shipping, 0.72 once the delivery outcome is known (3.7x lift on the top 10%). The strongest single feature is the number of items in the order.
- **Seller inactivity (among active sellers):** ROC-AUC 0.81, identical to a recency rule (0.81). An earlier version scored 0.91 but counted sellers that were already dormant; that was removed.
- **Seller quality failure:** only 24 events in the test snapshot; ROC-AUC 0.67 with a 95% interval of 0.56 to 0.76. Too rare to model; better as a monitoring rule.
- A watchlist of the 100 active sellers with the highest expected profit at risk is in `reports/tables/seller_watchlist_top100.csv`.

## 4. Recommendations
Each is tagged with the strength of the evidence. All money figures depend on stated assumptions (30% of order value lost behind a bad review; 25 to 30% intervention success).

1. **Fix delivery promises, not parcels.** Set state- and route-aware promised dates, and add days for the riskiest orders. Padding the promise by 7 days for the riskiest 10% of orders cuts their late rate from 17.3% to 7.2% at no shipping cost; paid expediting does not pay (break-even about R$1.1 per order against about R$21 of exposure). *Evidence: strong on the mechanical effect; conversion impact of longer promises is untested, so run an A/B test.*
2. **Treat freight as the primary profit lever.** Renegotiate carrier rates or apply shipping thresholds for the five below-margin categories (furniture, bed and bath, housewares, garden tools, sports), and review pricing for far-away regions. Each 1% freight saving is worth about 1.07% of profit. *Evidence: strong on the direction; the level depends on the assumed COGS.*
3. **Use cheap, automated post-delivery outreach.** For late or multi-item orders, an automated apology or check-in pays only if it costs under about R$4 to R$5 per order; at R$3 the test-period net benefit was R$2.9k to R$4.0k over 32k orders (about R$0.6k to R$0.8k a month). Do not build a paid service team on this signal. *Evidence: moderate; value is small.*
4. **Monitor sellers by rule, and nudge automatically.** Flag active sellers whose days since last order or 90-day order count deteriorates, plus a quality rule (cancellation above 5% or average review below 3.0). Retention calls pay only below about R$11 per seller, so automate the contact. *Evidence: moderate; the model ties the rule.*
5. **Plan for a flat baseline with a holiday option, and test retention.** Provision for about 6,450 orders a month and treat a November of roughly 10,900 as a scenario until a second holiday is observed. With only 3% repeat purchases, a retention experiment (post-purchase email, loyalty credit) is the largest untested growth lever. *Evidence: weak to moderate; forecast is a plateau, and the repeat-rate finding is descriptive.*

## 5. Limitations and honest caveats
- Profit and all cost figures are proxies or assumptions; Olist has no cost, refund or churn data.
- The forecast has 20 monthly points, learns no seasonality, and was never tested on a holiday.
- The delivery-time regime shift from June 2018 limits how far the risk models can be trusted forward, and the late model is tested on two months only.
- Model gains are modest and one test period per model was used.
- Cancellation timing is unknown, so a 14-day maturity rule for seller cancellation features is an assumption.

## 6. Reproducibility
Raw data goes in `data/raw/`. Run the pipeline from the project root: `python -m olist_risk.build_db`, then `eda`, `forecast`, `risk`, `model_late`, `model_review`, `model_seller`, `export_powerbi`. Run `pytest` to verify. Method details and decisions are in `docs/01` to `docs/06`.
