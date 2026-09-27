# 04 - Demand Forecasting (monthly delivered orders)

Reproduce: `python -m olist_risk.forecast`. Code: `src/olist_risk/forecast.py`, tests: `tests/test_forecast.py`. Outputs: `reports/tables/forecast_*.csv`, `reports/forecast_results.json`, figures 08-09.

**Target (decision, 2026-09-24):** delivered orders per purchase month, 2017-01 to 2018-08 (20 points). Revenue is derived as orders x trailing-6-month AOV, because AOV is stable (R$124-149 a month) while order counts are the cleaner demand signal.

## Method and why
| Choice | Reason (interview answer) |
|---|---|
| No SARIMA / seasonal ETS | Seasonality needs >= 2 full cycles to estimate. With 20 months there is one January-to-August and a single November. A fitted seasonal model would be memorising one year |
| Six simple methods | naive, 3-month average, log-drift (6m), damped-trend ETS on log orders, seasonal-naive (the required baseline), and an equal-weight combo of naive + ma3 + damped ETS |
| Rolling-origin backtest | 12-month minimum training window, then re-forecast every month with horizons 1-3: 21 out-of-sample forecasts per method. Unit test proves forecasts at an origin do not change if later data changes (no look-ahead) |
| WAPE as the headline metric | Percentage errors explode on small actuals (Jan 2017 = 750 orders); WAPE weights by volume. MAPE, MASE and bias are reported next to it |
| Intervals from backtest log-errors | Band = point x exp(+-1.28 x sigma x sqrt(h)), sigma = RMS one-step log error (9.2%) |

## Results (21 backtest forecasts per method)
| Method | WAPE | MASE | Bias |
|---|---|---|---|
| combo | 7.1% | 0.55 | +0.1% |
| ma3 | 7.5% | 0.57 | -0.3% |
| naive | 7.8% | 0.60 | -0.2% |
| holt_damped | 8.1% | 0.63 | +0.9% |
| drift6 | 15.1% | 1.13 | +13.1% |
| snaive | 54.3% | 4.12 | -54.3% |

Error by horizon (combo): 6.1% at 1 month, 6.8% at 2, 9.0% at 3.

**Forecast, Sep-Nov 2018 (combo):** 6,444 / 6,455 / 6,463 orders, 80% band 5,271-7,925 by November. At the trailing-6-month AOV of R$139.6 that is about R$0.90M a month. Revenue conversion backtested at 8.0% WAPE.

## What this does and does not show
1. **The four flat-level methods are statistically tied** (7.1-8.1%). 21 forecasts cannot rank methods that close, so "combo is best" is not claimed. The finding is that *no added complexity beat a 3-month average*, which is itself the honest result for a plateaued series.
2. **Seasonal-naive fails (54%) because 2017 was a scale-up year.** It copies a same-month figure from a much smaller base. This is why the standard seasonal baseline cannot be used here, not a bug.
3. **The backtest never forecasts a holiday.** The first origin is Dec 2017, so November 2017 is only in training. The 7% error describes plateau months (Jan-Aug 2018) and says nothing about November accuracy.
4. **The November figure is a scenario, not a forecast.** Base Nov-2018 is 6,463; applying the Nov-2017 uplift (1.69x the Sep-Oct 2017 average) gives 10,920. It rests on one observation and is labelled as such on the chart.
5. **The interval is conservative.** Empirical coverage in the backtest is 95% against an 80% target. Reported as too wide, not tuned to look better. Small sample (21 points), so coverage itself is uncertain.
6. **Drift caveat.** The delivery-time regime shift from June 2018 (docs/02, caveat 5) may also affect what counts as a delivered order in the extract; its effect on order counts is untested.

## Next uses
The order forecast feeds the downside scenarios (demand shock, freight and late-rate stress) in the risk-metrics step and the forecast page of the Power BI model.
