# 06 - Predictive Risk Models

Reproduce: `python -m olist_risk.model_late`, `model_review`, `model_seller` (about 15 minutes in total). Code: `src/olist_risk/{features,ml_common,model_late,model_review,model_seller}.py`. Tests: `tests/test_features.py`, `test_split.py`, `test_ml_common.py` (leakage, order of operations, cost model). Outputs: `reports/*_model_results.json`, `reports/tables/`, figures 12-18.

## Design rules that make the results defensible
| Rule | How it is enforced |
|---|---|
| **Time-causal features.** A feature may only use outcomes known strictly before prediction time | `asof_stats` (strictly earlier), unit-tested and cross-checked against a brute-force recomputation on real data. A late flag is known at delivery, a review score at its answer timestamp |
| **Chronological splits, no shuffling** | Train and test never overlap in time (`time_split`, tested) |
| **Embargo** | Train rows whose label became known on or after the test start are dropped (3,852 for the late model, 3,970 for reviews). A model trained the day before the test could not have seen them |
| **Priors from training only; history built on all orders** | `split_with_history`, tested by perturbing test-period outcomes and checking train features and priors do not move |
| **No selection on the test set** | The primary model (gradient boosting) is fixed in advance; logistic regression is the reported baseline; both are shown. An independent review flagged the earlier "best of two by test score" as a bias, so it was removed |
| **Uncertainty shown** | 95% bootstrap intervals for the seller models, whose test sets are small |
| **Costs are conditional** | Olist has no refund or churn data. Every R$ figure rests on stated assumptions and is reported as a *break-even cost per action*, not a single number |

## Targets and splits
| Model | Target | Predicted at | Train | Test | Test base rate |
|---|---|---|---|---|---|
| (a) Late delivery | `is_late` (calendar-day rule) | Order approval | 2017-01 to 2018-03 (60,201 orders) | 2018-04 to 2018-05 (13,547) | 5.5% |
| (b) Low review | 1-2 star score | Approval, and separately at delivery | 2017-01 to 2018-03 (59,596) | 2018-04 to 2018-08 (31,994) | 10.4% |
| (c-A) Seller inactivity | No orders in next 90 days, among sellers active in the prior 90 days | Snapshot cutoff | Monthly cutoffs 2017-07 to 2018-03 (6,301 rows) | Cutoff 2018-06-02 (1,101 sellers) | 16.8% |
| (c-B) Seller quality | Cancellation > 5% or avg review < 3.03 in next 90 days (>= 5 orders) | Snapshot cutoff | 3,406 rows (last cutoff dropped as embargo) | Same test snapshot (563 sellers) | 4.3% (24 sellers) |

Model (a) follows decision D6 (June-August 2018 excluded because of the delivery-time regime shift). Definition B's review threshold (3.03) is the 5th percentile of training label windows, and the cancellation threshold (5%) is a round number near the 97th percentile.

## Results (held-out test)
| Model | ROC-AUC | PR-AUC (base rate) | Top 10% flagged: precision / recall / lift |
|---|---|---|---|
| (a) Late delivery, gradient boosting | 0.722 | 0.143 (0.055) | 17.3% / 31% / 3.1x |
| (a) logistic baseline | 0.729 | 0.129 | 15.6% / 28% / 2.8x |
| (b) Low review at approval, GB | 0.628 | 0.180 (0.104) | 22.5% / 22% / 2.2x |
| (b) Low review at delivery, GB | 0.724 | 0.354 (0.104) | 38.8% / 37% / 3.7x |
| (c-A) Inactivity, active sellers, GB | 0.805 (95% CI 0.78-0.83) | 0.445 (0.168) | 52.7% / 31% / 3.1x |
| (c-B) Quality, GB | 0.666 (95% CI 0.56-0.76) | 0.096 (0.043) | 8.9% / 21% / 2.1x |

Stability by month: the late model scores ROC-AUC 0.77 in April and 0.69 in May 2018, drifting down as the delivery-time regime shift approaches. The review model at delivery holds 0.68-0.75 across April-August.

## What each model says, and what to conclude
**(a) Late delivery. Real but modest signal, mostly geography and promise.** The strongest features are customer state, promised lead time and route history. Seller history alone is weak (single-feature AUC 0.55 against 0.64 for route history), so lateness is a **route and promise problem more than a seller problem**. Calibration is close on the test months (top decile predicted 21%, observed 17%).

**(b) Low review. Most of a bad review is decided after shipping.** ROC-AUC rises from 0.63 to 0.72 once delivery outcome is known. Top features at delivery: number of items in the order (multi-item orders review worse), days late, the seller's prior low-review rate, delivery days. Caveat: 2.6% of test orders have a review answered before the recorded delivery date, so the delivery features are not strictly earlier for them.

**(c-A) Seller inactivity. The model does not beat a simple rule.** Among currently active sellers, days since last order alone scores ROC-AUC 0.814, against 0.805 for gradient boosting and 0.816 for logistic regression. Those are ties. The first version of this analysis scored 0.91, but it counted sellers that had already gone dormant before the cutoff, which recency "predicts" trivially; the active-only population is the honest one, and the all-eligible figures are kept in the JSON for transparency. Inactivity also rose across snapshots (train base rate 13.5%, test 16.8%).

**(c-B) Seller quality. Too rare to model reliably.** Only 24 test positives, ROC-AUC interval 0.56-0.76, and the single feature "average review" scores 0.65. This definition is better used as a monitoring rule than a model.

**The two seller definitions are not comparable populations.** B is undefined for sellers with fewer than 5 label-window orders, which includes every inactive seller, so no seller is both. A finds sellers who will leave; B finds sellers who stay and deliver badly.

## Business-cost interpretation (all conditional on assumptions)
Assumptions: 30% of order value is lost behind a bad review; success rates of 30% (late orders), 25% (reviews) and 30% (sellers); profit margin 18%. The useful output is the **break-even cost**: the most an action can cost per flagged item and still pay.

| Action | Break-even cost per action | At the assumed cost | Reading |
|---|---|---|---|
| Expedite or handle flagged late-risk orders (top 10%) | about R$1.1 | Loses money at R$2, R$5 and R$10 | Exposure per late order is only about R$21, so paid intervention does not pay |
| Free lever: add days to the promised date of the riskiest 10% of orders | costs nothing | Their late rate falls from 17.3% to 14.0% (+2 days), 10.6% (+4), 7.2% (+7) | This is a mechanical effect; the unmeasured cost is lower conversion from longer promises |
| Outreach to likely unhappy customers after delivery (top 5-10%) | R$5.5 (top 5%), R$3.9 (top 10%) | Net +R$4.0k / +R$2.9k over 32k test orders at R$3 per contact | Positive but small: roughly R$0.6-0.8k a month, about 0.1% of revenue |
| Retention call to at-risk active sellers (top 10%) | about R$11 | Loses money at R$50 per call | The sellers about to go quiet are small, so only automated nudges are affordable |

## Recommendations these results support
1. **Fix the promise, not the parcel.** Use route-and-state risk to set more realistic delivery dates for high-risk orders instead of paying for faster shipping.
2. **Trigger cheap, automated post-delivery outreach** for late or multi-item orders; do not build a costly service team on this signal.
3. **Start seller monitoring with rules, not models.** Flag active sellers whose days-since-last-order or 90-day order count deteriorates, and route them to automated nudges. A watchlist of the 100 highest expected-profit-at-risk sellers is in `reports/tables/seller_watchlist_top100.csv`.

## Limits to state before an interviewer does
- Cost figures depend on assumed loss, rescue and contact costs; the break-even framing exists for that reason.
- One test period per model, chosen chronologically; the late model tests on only two months.
- The delivery-time regime shift (from June 2018) caps how far any of these models can be trusted forward.
- Cancellation timestamps do not exist, so the 14-day "seasoning" of cancellation features is an assumption (tested mechanically, not empirically).
- The final test snapshot and the watchlist use models trained on earlier snapshots only, not refit on the newest data.
