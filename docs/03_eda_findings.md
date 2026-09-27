# 03 - EDA Findings

Reproduce: `python -m olist_risk.eda` (figures in `reports/figures/`, numbers in `reports/eda_findings.json`). Window 2017-01 to 2018-08, delivered orders, profit = proxy (65% COGS, less freight).

| # | Finding | Evidence | Caveat / how to say it in an interview |
|---|---|---|---|
| 1 | Revenue is 1.7x its 2017 monthly average but has **slipped 12%** since spring 2018 (Jun-Aug avg R$854k vs Mar-May R$968k) | fig 01 | Demand growth stalled; this is what the forecast has to handle. Nov 2017 (R$988k) is the peak month (likely Black Friday) |
| 2 | Twelve categories = **69%** of revenue; **5 of them earn below-platform margin** (bed_bath_table, furniture_decor, housewares, garden_tools, sports_leisure) | fig 02 | Margin is a proxy: the shape (freight-heavy categories lose margin) is robust to the COGS rate, the level is not. Sensitivity test comes with the risk metrics |
| 3 | Sellers are **fragmented yet skewed**: top 20% of sellers = 82% of revenue, top 1% = 26%, but seller HHI is 36 (no dependency on any single seller) | fig 03 | Marketplace: concentration risk sits in *tiers* of sellers, not in a single name. Downside scenarios test losing the top-K |
| 4 | **Delivery-time regime shift**: actual delivery fell from 13.2 to 8.6 days, promised from 25.5 to 20.9 days, from Jun 2018 | fig 04 | Not censoring (checked). Cause unknown; treated as concept drift and handled by decision D6 |
| 5 | Late rate peaked at **19%** in Mar 2018, the same month 1-2 star reviews peaked (21%) | fig 05 | Plain late rate is 6.8%; censoring-adjusted 8.4% (1,699 overdue undelivered orders) |
| 6 | A **late order is 6.7x more likely to get 1-2 stars** (62.4% vs 9.3% on time); 8+ days late is 8.6x (79%) | fig 06 | Association, not proof of causation (late orders may also differ in product/seller). It is the basis for the revenue-at-risk logic |
| 7 | **SP = 38%** of revenue; other big states pay 17-20% freight vs 14% in SP | fig 07 | Regional risk is geographic (demand concentration in the south-east) and freight-margin driven |

## Revenue at risk (from the KPI layer)
R$2.32M (17.6%) of delivered revenue sits on orders that were late or scored 1-2 stars (union, no double counting). Lost revenue on canceled/unavailable orders that carried items: R$92k (0.7%). Platform cancellation rate: 1.19% (seller-attributable subset is lower, see caveat 2 in docs/02).

## Implications for the next steps
- **Forecast (Q2):** 20 monthly points, one holiday spike, a plateau. Use a rolling-origin backtest; report seasonal-naive as the baseline any model must beat, and forecast intervals rather than point values.
- **Late-delivery model (D6):** train 2017-01 to 2018-03, test 2018-04 to 2018-05; Jun-Aug excluded.
- **Cost of lateness:** the 6.7x review effect gives the business-cost lever for the late-delivery model's threshold choice.
