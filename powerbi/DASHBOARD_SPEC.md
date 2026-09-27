# Power BI Dashboard Spec

Interactive dashboard for the Olist retail profitability, forecasting and operational-risk project. This is a build guide: you create the `.pbix` in Power BI Desktop from the CSVs in `powerbi/model/`, the measures in `powerbi/measures.dax` and the theme in `powerbi/theme.json`. Regenerate the CSVs any time with `python -m olist_risk.export_powerbi`.

**Honest status:** the CSVs are tested (keys, referential integrity, reconciliation to the SQL layer). The DAX and the page designs were written without access to a Power BI engine, so they are not executed. Section 5 gives the numbers each measure must reproduce; check them before trusting any visual.

## 1. Build steps
1. **Get Data > Text/CSV**: load all 16 files in `powerbi/model/` (skip `reconciliation_expected.json`). Keep UTF-8 encoding.
2. **Set data types** in Power Query: `dim_date[date]`, `dim_date[month_start]`, `fact_sales[purchase_date]`, `fact_orders[purchase_date]`, `fact_forecast[month_start]`, `tbl_concentration_trend[month_start]` = Date; `is_*`, `at_risk`, `rank` = Whole number; all rate/amount columns = Decimal.
3. **Mark `dim_date` as the date table** (Table tools > Mark as date table > `date`).
4. **Sort** `dim_date[month_name]` by `month_num`, and `dim_date[period]` alphabetically.
5. **Relationships** (all single direction, one-to-many from dimension to fact):

| From (one) | To (many) |
|---|---|
| `dim_date[date]` | `fact_sales[purchase_date]`, `fact_orders[purchase_date]` |
| `dim_category[category_en]` | `fact_sales[category_en]` |
| `dim_seller[seller_id]` | `fact_sales[seller_id]`, `fact_seller_watchlist[seller_id]` |
| `dim_customer_state[state]` | `fact_sales[customer_state]`, `fact_orders[customer_state]` |

   Leave these **disconnected** on purpose: `fact_forecast`, `tbl_scenarios`, `tbl_cogs_sensitivity`, `tbl_concentration_trend`, `tbl_model_scorecard`, `tbl_risk_stats`, `tbl_forecast_scoreboard`.
6. **Create three what-if parameters** (Modeling > New parameter) exactly as listed at the top of `measures.dax`: COGS Rate, Freight Shock, Demand Shock.
7. **Create a `_Measures` table and add the measures** from `measures.dax` (one at a time; see the header of that file), then set formats and display folders.
8. **Add a calculated column** `Lateness Bucket` on `fact_orders` (code is commented in section 4 of `measures.dax`).
9. **Apply the theme** (View > Themes > Browse for themes > `theme.json`). Palette: blue `#2a78d6` (primary), orange `#eb6834` (comparison or alert), green `#1baf7a` (third series). It was validated for colour-blind separation.
10. **Hide** key and helper columns from report view (all `*_id`, `order_item_id`, `month_num`, `is_*` flags after measures exist).

## 2. Data dictionary
| Table | Grain | Notes |
|---|---|---|
| `fact_sales` | One row per **delivered** order item, Jan 2017 to Aug 2018 (109,880) | Revenue lives here. P2 profit is deliberately not stored: it is a DAX measure driven by the COGS what-if |
| `fact_orders` | One row per order in the window, **any status** (99,092) | Risk columns are blank where they do not apply. `revenue`, `is_late`, `review_score`, `at_risk` exist only for delivered orders |
| `dim_date` | One row per day, 2017-01-01 to 2018-12-31 | Flags: `is_full_month`, `in_late_model_window`, `period` (before or after the June 2018 regime shift) |
| `dim_seller` | One row per seller (3,095) | `revenue_tier` (Top 10, Top 5%, Top 20%, Long tail) from trailing 6-month revenue |
| `dim_customer_state` | 27 states | Maps to five macro-regions |
| `dim_category` | 74 categories | English names; `unknown` for products without a category |
| `fact_forecast` | Monthly, `series` = Actual / Forecast / Black-Friday scenario | 80% interval on the forecast rows only |
| `fact_seller_watchlist` | Top 100 active sellers by expected profit at risk (rank 1 to 100) | Model scores as of 2018-08-31 |
| `tbl_scenarios`, `tbl_cogs_sensitivity`, `tbl_concentration_trend`, `tbl_risk_stats` | Precomputed risk results | From `risk.py` |
| `tbl_model_scorecard`, `tbl_forecast_scoreboard` | Model and forecast evaluation | 96 and 6 rows |

## 3. Pages
Canvas 16:9 (1280 x 720). Header band (0 to 56 px): page title left, slicers right. Body uses a 12-column grid with 16 px gutters. Visual titles are **insight headlines** (dynamic where a `Title ...` measure exists). Every visual needs alt text.

**Sync slicers:** Date range (`dim_date[date]`, between) and Region (`dim_customer_state[region]`) sync across pages 1, 2, 4, 5. Pages 3, 6, 7 are not date-sliced (forecast, current watchlist, model card).

### Page 1: Executive summary
| Visual | Fields | Notes |
|---|---|---|
| 6 KPI cards | Gross Revenue, Orders, AOV, Profit Proxy (P2), Late Rate, At-Risk % | Small delta label vs prior month where sensible |
| Clustered column + line | Axis `dim_date[year_month]`; column [Gross Revenue]; line [Revenue 3M Avg] | Title bound to `Title Revenue` |
| Filled map or bar by state | `dim_customer_state[state_name]`, [Revenue Share %] | Bing maps needs internet; bar chart is the fallback |
| Text box "What to know" | Three bullets | Revenue plateaued after spring 2018; freight is larger than profit; late orders drive bad reviews |

### Page 2: Profitability (categories, sellers, regions)
| Visual | Fields | Notes |
|---|---|---|
| Bar (top 12) | Axis `dim_category[category_en]`, value [Gross Revenue]; tooltip [Profit Margin %], [Freight Ratio] | Conditional colour: orange when [Profit Margin %] is below the platform value |
| Scatter | X [Freight Ratio], Y [Profit Margin %], size [Gross Revenue], details category | Shows the freight-margin relationship; reference line at the platform margin |
| Slicer + card | **COGS Rate** what-if slider; card [Profit Margin %] | Title bound to `Title Margin`. The category ranking never changes with COGS, only the level |
| Matrix | Rows region then state; values [Gross Revenue], [Freight Ratio], [Profit Proxy (P2)] | Drill down from region to state |

### Page 3: Demand and forecast
| Visual | Fields | Notes |
|---|---|---|
| Line chart | Axis `fact_forecast[month_start]`; values [Actual Orders], [Forecast Orders], [Forecast Low (80%)], [Forecast High (80%)] | Forecast in orange dashed, band lines in light orange. Add [Black-Friday Scenario Orders] as a hollow marker |
| Cards | [Forecast Revenue per Month], [Forecast Orders] (Sep) | Revenue = orders x trailing-6-month AOV (R$139.6) |
| Bar (method comparison) | `tbl_forecast_scoreboard[model]` by `wape` | Four methods tie at 7-8% error; seasonal-naive is off by 54% |
| Text box "How to read" | Fixed text | 20 months cannot identify seasonality; the backtest never forecasts a holiday; November is a labelled scenario from one observation; the 80% band is conservative (95% empirical coverage) |

### Page 4: Operational risk
| Visual | Fields | Notes |
|---|---|---|
| 4 KPI cards | Late Rate, Low Review Rate, Cancellation Rate, At-Risk Revenue | Late Rate is conditional on delivery |
| Line chart | Axis `dim_date[year_month]`; [Late Rate], [Low Review Rate] | Shade Jun-Aug 2018 (regime shift) using a reference band or the `period` legend |
| Line chart | Axis `year_month`; [Avg Delivery Days], [Avg Promised Days] | Evidence of the June 2018 regime shift |
| Column | Axis `fact_orders[Lateness Bucket]`; [Low Review Rate] | Title bound to `Title Late`. Association, not proof of cause |
| Table | State, [Late Rate], [Orders], [Avg Delivery Days] | Geography drives lateness |

### Page 5: Concentration and stress tests
| Visual | Fields | Notes |
|---|---|---|
| 4 cards | [Seller HHI], [Category HHI], [State HHI], [Top 20% Seller Share] | Title bound to `Title Concentration`. HHI bands: under 1,500 low, 1,500 to 2,500 moderate |
| Line | `tbl_concentration_trend`: seller_hhi, category_hhi by month_start | Seller HHI falls because sellers grew from 219 to 1,261 |
| Sliders | **Freight Shock**, **Demand Shock**, **COGS Rate** | The interactive stress test |
| Cards | [Profit under Shock (P2)], [Profit Change %], [Freight Leverage] | Freight leverage above 1 means freight exceeds the profit pool |
| Bar | `tbl_scenarios[scenario]` by `d_p2` | Precomputed stress tests on the Jun-Aug 2018 baseline |
| Table | `tbl_cogs_sensitivity` | Loss-making categories rise from 1 to 15 as COGS goes from 55% to 75% |
| Cards | `tbl_risk_stats` rows | Volatility, max drawdown (-26.5%), current drawdown (-15.1%) |

### Page 6: Seller watchlist (model output)
| Visual | Fields | Notes |
|---|---|---|
| Table | rank, seller_id, `dim_seller[seller_state]`, p_inactive, n_orders_90d, days_since_last, avg_review, late_rate, [Expected Profit at Risk] | Data bars on p_inactive and expected profit; conditional icons on avg_review below 3.5 |
| Scatter | X p_inactive, Y run_rate_profit_90d, size expected_profit_at_risk | Top right = highest priority |
| Cards | [Watchlist Sellers], [Expected Profit at Risk], [Avg P(Inactive)] | |
| Text box "Use this carefully" | Fixed text | Among active sellers the model ties a simple days-since-last-order rule (ROC-AUC 0.81 vs 0.81). Retention calls pay only below about R$11 per seller, so use automated nudges. Scores are as of 2018-08-31 |
| Drill-through page "Seller detail" | Filters: `seller_id`; visuals: monthly [Gross Revenue], category mix, [Late Rate] | Target for right-click from the watchlist table |

### Page 7: Model card and methodology
| Visual | Fields | Notes |
|---|---|---|
| Matrix | Rows `tbl_model_scorecard[model]` and `variant`; columns `metric`; values [Model Metric Value] | Filter the metrics shown to roc_auc, pr_auc, base_rate, lift_top10 |
| Text blocks | Decisions D1 to D6; assumptions; leakage guards | Copy from `docs/02_decisions_and_kpis.md` and `docs/06_predictive_models.md` |
| Text block "Limits" | Fixed text | Cost figures are conditional on stated assumptions; delivery-time regime shift from June 2018; small test sets for seller quality (24 positives) |

## 4. Interactions, accessibility, polish
- Turn **off** cross-filtering from KPI cards; keep it on for column and bar visuals.
- Bookmarks: "Base case" (all sliders reset) and "Severe stress" (demand -20%, freight +15%, COGS 65%).
- Tab order follows reading order; alt text on every visual; no meaning carried by colour alone (labels and legends present).
- Number formats: currency `R$ #,##0`, rates `0.0%`, HHI `#,##0`.
- Export a PDF of all pages and PNGs of pages 1, 3, 5, 6 for the README and the interview deck.

## 5. Reconciliation checklist (do this before anything else)
Put these on a temporary page with no slicers applied. Each measure must match.

| Measure | Expected |
|---|---|
| Gross Revenue | R$ 13,181,027.13 |
| Freight | R$ 2,192,092.88 |
| Orders | 96,211 |
| Items | 109,880 |
| Customers | 93,104 |
| Profit Proxy (P2) at 65% COGS | R$ 2,421,266.62 |
| Profit Margin % at 65% COGS | 18.4% |
| AOV | R$ 137.00 |
| Freight Ratio | 16.6% |
| Repeat Rate | about 3.0% |
| Active Sellers | 2,945 |
| Seller HHI / Category HHI / State HHI | 36.4 / 489.8 / 1,897.1 |
| Top 20% Seller Share | 82.2% |
| All Orders / Failed Orders | 99,092 / 1,182 |
| Cancellation Rate | 1.19% |
| Late Orders / Late Evaluable Orders | 6,531 / 96,203 |
| Late Rate | 6.79% |
| Low Review Orders / Reviewed Orders | 12,229 / 95,568 |
| Low Review Rate | 12.80% |
| At-Risk Revenue / At-Risk % | R$ 2,323,963.11 / 17.6% |
| Lost Revenue (failed orders) | R$ 92,183.10 |
| Low Review Rate if Late / if On Time / Lateness Multiplier | 62.4% / 9.3% / 6.7x |
| Freight Leverage (full window) | 0.91 (1.07 on the Jun-Aug 2018 baseline) |

Machine-readable copy: `powerbi/model/reconciliation_expected.json`.

## 6. One-line interview answer per page
1. Revenue is 1.7x its 2017 level but slipped 12% from spring 2018; freight is larger than the profit proxy.
2. Twelve categories earn 69% of revenue and five earn below-average margin, because freight, not COGS, separates them.
3. No method beat a 3-month average on orders; seasonality cannot be learned from 20 months, so I labelled November as a scenario.
4. A late order is 6.7x as likely to get 1-2 stars, and delivery times shifted in June 2018, so late rates from then are not comparable.
5. Sellers are fragmented (HHI 36) but skewed; profit is more sensitive to freight rates than to demand.
6. Among active sellers the model only ties a recency rule, so the honest recommendation is a rule-based watchlist with cheap automated outreach.
7. Every model uses time-causal features, an embargo and a pre-specified primary model; cost figures are conditional on stated assumptions.
