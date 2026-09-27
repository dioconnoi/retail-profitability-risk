"""Model (a): will this order arrive late?  Predicted at order approval time.

Target : fact_orders.is_late (calendar-day rule). Delivered orders with a delivery date only.
Split  : D6 - train 2017-01..2018-03, test 2018-04..2018-05 (Jun-Aug 2018 excluded: delivery-time regime shift).
Inputs : only what is known at approval (order contents, geography, promised lead time, seller/route
         history built from outcomes known strictly before purchase). No delivery outcome, no review.

Usage (from project root):  python -m olist_risk.model_late
"""
from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd
from sklearn.inspection import permutation_importance
from sklearn.metrics import roc_auc_score

from olist_risk import features as ft
from olist_risk import ml_common as mc
from olist_risk.build_db import ROOT
from olist_risk.data import query

log = logging.getLogger("olist_risk.model_late")
TABLE_DIR, FIG_DIR = ROOT / "reports" / "tables", ROOT / "reports" / "figures"
T = pd.Timestamp
TRAIN_END, TEST_START, TEST_END = T("2018-03-31"), T("2018-04-01"), T("2018-05-31")

# Cost assumptions (Olist has no refund/churn data): stated, sensitivity-tested, never hidden.
LOSS_GIVEN_BAD_REVIEW = 0.30   # share of order value lost (refund, churn) when a 1-2 star review follows
SUCCESS_RATE = 0.30            # share of flagged late orders an intervention rescues
INTERVENTION_COST = 5.0        # R$ per flagged order (proactive contact / priority handling)


def prepare() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Chronological split with training-only priors; returns (train, test, train_stats)."""
    df = ft.load_orders()
    eligible = df["is_late"].notna() & (df["purchase_ts"] < TEST_END + pd.Timedelta(days=1))   # D6: nothing after May 2018
    train, test, priors = ft.split_with_history(df, eligible, TRAIN_END, TEST_START, TEST_END, known_col="delivered_ts")
    train, test = train.assign(is_late=train["is_late"].astype(int)), test.assign(is_late=test["is_late"].astype(int))
    rev = train.dropna(subset=["review_score"])
    stats = {**priors, "aov": float(train["price_total"].mean()),
             "p_low_late": float(rev.loc[rev["is_late"] == 1, "is_low"].mean()),
             "p_low_on_time": float(rev.loc[rev["is_late"] == 0, "is_low"].mean())}
    return train, test, stats


def cost_model(stats: dict, loss: float = LOSS_GIVEN_BAD_REVIEW, cost: float = INTERVENTION_COST) -> mc.CostModel:
    """Exposure per missed late order = AOV x extra probability of a 1-2 star review x loss given a bad review."""
    exposure = stats["aov"] * (stats["p_low_late"] - stats["p_low_on_time"]) * loss
    return mc.CostModel(exposure=exposure, success_rate=SUCCESS_RATE, intervention_cost=cost)


def sensitivity(y: np.ndarray, p: np.ndarray, stats: dict, share: float = 0.10) -> pd.DataFrame:
    rows = []
    for loss in (0.10, 0.30, 0.50):
        for cost in (2.0, 5.0, 10.0):
            cm = cost_model(stats, loss, cost)
            r = mc.net_at_shares(y, p, cm, (share,))[0]
            rows.append({"loss_given_bad_review": loss, "intervention_cost": cost, "exposure_per_late_order": cm.exposure,
                         "net_benefit_top10": r["net"], "break_even_cost": r["break_even_cost"]})
    return pd.DataFrame(rows)


def promise_padding(test: pd.DataFrame, p: np.ndarray, share: float = 0.10, pads=(0, 2, 4, 7)) -> list[dict]:
    """A free lever: extend the promised date for the riskiest orders. Mechanical effect on the late
    rate of the flagged group (delivery times are unchanged; only the promise moves)."""
    k = int(round(share * len(test)))
    flagged = test.iloc[np.argsort(-p, kind="stable")[:k]]
    return [{"extra_days_promised": d, "late_rate_flagged": float((flagged["days_late"] > d).mean())} for d in pads]


def importance(pipe, test: pd.DataFrame, cols: list[str], target: str) -> pd.DataFrame:
    sample = test.sample(min(20_000, len(test)), random_state=0)
    res = permutation_importance(pipe, sample[cols], sample[target], scoring="roc_auc", n_repeats=3, random_state=0)
    return (pd.DataFrame({"feature": cols, "auc_drop": res.importances_mean}).sort_values("auc_drop", ascending=False).reset_index(drop=True))


def run() -> dict:
    train, test, stats = prepare()
    numeric, categorical = ft.APPROVAL_NUMERIC, ft.APPROVAL_CATEGORICAL
    exp = mc.run_experiment(train, test, numeric, categorical, "is_late")
    best, scores = exp["best"], exp["scores"]
    y, p = scores["y"].to_numpy(), scores[best].to_numpy()

    single = {f: float(roc_auc_score(y, test[f].fillna(test[f].median()))) for f in ("seller_late_rate_prior", "route_late_rate_prior", "lead_days_est")}
    by_month = {m: {"n": int(len(g)), "base_rate": float(g["is_late"].mean()),
                    "roc_auc": float(roc_auc_score(g["is_late"], scores.loc[g.index, best]))}
                for m, g in test.groupby("purchase_month")}
    imp = importance(exp["fitted"][best], test, numeric + categorical, "is_late")
    cm = cost_model(stats)
    sens = sensitivity(y, p, stats)
    calib = mc.calibration_table(y, p)
    padding = promise_padding(test.loc[scores.index], p)

    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    imp.to_csv(TABLE_DIR / "late_feature_importance.csv", index=False)
    sens.to_csv(TABLE_DIR / "late_cost_sensitivity.csv", index=False)
    calib.to_csv(TABLE_DIR / "late_calibration.csv", index=False)
    scores.to_csv(TABLE_DIR / "late_test_scores.csv", index_label="row")

    m = exp["models"][best]["at_shares"]["0.10"]
    mc.fig_precision_recall(scores, exp["models"], best,
        f"Flagging the riskiest 10% of orders catches {m['recall_top'] * 100:.0f}% of late deliveries",
        f"Late-delivery model on Apr-May 2018 (never seen in training). Top-10% precision {m['precision_top'] * 100:.0f}% vs {m['base_rate'] * 100:.1f}% base rate ({m['lift_top']:.1f}x lift)",
        FIG_DIR / "12_late_delivery_precision_recall.png")
    top10 = mc.net_at_shares(y, p, cm, (0.10,))[0]
    mc.fig_net_benefit(y, p, cm, [0.5, 1.5, 5.0],
        f"Acting on late-risk flags pays only if it costs under about R${top10['break_even_cost']:.1f} per order",
        f"Net benefit vs share flagged. Assumes {LOSS_GIVEN_BAD_REVIEW * 100:.0f}% of order value lost on a bad review and a {SUCCESS_RATE * 100:.0f}% rescue rate (exposure R${cm.exposure:.0f} per late order)",
        FIG_DIR / "13_late_delivery_net_benefit.png")

    results = {"split": {"train_end": str(TRAIN_END.date()), "test": [str(TEST_START.date()), str(TEST_END.date())],
                         "n_train": len(train), "n_test": len(test), "train_base_rate": exp["train_base_rate"],
                         "test_base_rate": float(y.mean())},
               "train_stats": stats, "best_model": best, "models": exp["models"],
               "single_feature_auc": single, "by_test_month": by_month,
               "exposure_per_late_order": cm.exposure, "net_at_shares": mc.net_at_shares(y, p, cm),
               "top_features": imp.head(10).to_dict("records"), "promise_padding_top10": padding}
    (ROOT / "reports" / "late_model_results.json").write_text(json.dumps(results, indent=2, default=float), encoding="utf-8")
    log.info("best=%s roc_auc=%.3f pr_auc=%.3f", best, exp["models"][best]["roc_auc"], exp["models"][best]["pr_auc"])
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run()
