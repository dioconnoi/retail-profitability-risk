# 05 - Risk Metrics & Downside Scenarios

Reproduce: `python -m olist_risk.risk`. Code: `src/olist_risk/risk.py`, tests: `tests/test_risk.py`. Outputs: `reports/tables/risk_*.csv`, `reports/risk_results.json`, figures 10-11.

**Baseline for all scenarios:** Jun-Aug 2018 monthly average: revenue R$854k, freight R$154k, profit proxy (P2, 65% COGS) R$145k. Costs are variable only; Olist has no fixed-cost data, so no operating-leverage effects are modelled.

## Volatility and drawdown (monthly revenue)
| Metric | Value | How to read it |
|---|---|---|
| Std dev of monthly log growth, full window | 24.8% | Inflated by the 2017 scale-up (Jan 2017 = R$112k) |
| Same, last 12 months | 19.4% | Still includes the Nov-2017 spike and the Dec reversal |
| Coefficient of variation, Jan-Aug 2018 | 6.9% | The plateau is stable: revenue moves about 7% around its mean |
| Max drawdown | -26.5% (Nov 2017 to Dec 2017) | A holiday reversal, not structural decline |
| Current drawdown from the Nov-2017 peak | -15.1% | Aug 2018 sits 15% below the peak |

## Concentration
| Dimension | HHI (full window) | Trend |
|---|---|---|
| Sellers | 36 | 120 in Jan-Mar 2017 to 45 in Jun-Aug 2018 |
| Categories | 490 | Stable, 536 to 550 |
| Customer states | 1,897 | Moderate (SP = 38%) |

The falling seller HHI is a marketplace-growth effect, not diversification of a fixed base: active sellers rose from 219 (Jan 2017) to 1,261 (Aug 2018). Top-10 seller share fell from 26% to 14%.

## Downside scenarios (monthly, vs baseline)
| Scenario | Revenue | Profit proxy |
|---|---|---|
| Demand -10% | -R$85k (-10%) | -R$14k (-10%) |
| Demand -20% | -R$171k (-20%) | -R$29k (-20%) |
| Freight rates +15% | 0 | -R$23k (-16%) |
| Freight rates +30% | 0 | -R$46k (-32%) |
| Lose the top 10 sellers, no recapture | -R$121k (-14%) | -R$23k (-16%) |
| Lose the top 5% of sellers (148), no recapture | -R$439k (-51%) | -R$90k (-62%) |
| Severe: demand -20% and freight +15% | -R$171k (-20%) | -R$47k (-33%) |
| Late-delivery rate doubles | no change | exposure: +R$52k/month of revenue at risk |

**Key insight (say this in an interview):** monthly freight (R$154k) is larger than the whole profit proxy (R$145k), so each 1% rise in freight rates costs 1.07% of profit. Margin is thin enough that shipping cost, not demand, is the sharpest lever. The top 5% of sellers hold 51% of revenue but 62% of profit, so the best sellers are also the most profitable.

## COGS sensitivity (the assumption that matters most)
| COGS | 55% | 60% | 65% (base) | 70% | 75% |
|---|---|---|---|---|---|
| Platform margin after freight | 28.4% | 23.4% | 18.4% | 13.4% | 8.4% |
| Categories losing money | 1 | 2 | 5 | 7 | 15 |
| Revenue in loss-making categories | 0.0% | 0.0% | 0.1% | 0.3% | 4.7% |

Because margin = (1 - COGS) - freight/revenue, the category *ranking* is identical at every COGS rate. What changes is the level and which categories go negative. So the finding "freight-heavy categories are the weak ones" is robust; any absolute profit figure is not.

## Limits (state these before an interviewer finds them)
1. The late-rate scenario changes exposure only. Turning it into lost profit needs a refund or churn-cost assumption that Olist cannot support (about 3% of customers repeat).
2. "Lose sellers" assumes zero recapture by other sellers, which is the worst case. Real marketplaces recapture part of the demand.
3. Scenarios are stress tests on the last quarter, not predictions.
