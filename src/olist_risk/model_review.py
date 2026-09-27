"""Model (b): will this order get a 1-2 star review?

Two feature sets answer two different business questions:
  * 'at approval' : before the order ships (same inputs as the late-delivery model). Enables prevention.
  * 'at delivery' : adds the realised delivery outcome. Enables service recovery right after delivery.
The gap between them measures how much of a bad review is decided after the order ships.

Target : fact_reviews.is_low (score <= 2), latest review per order; reviewed delivered orders only.
Split  : train 2017-01..2018-03, test 2018-04..2018-08 (chronological). Jun-Aug are kept here because the
         target is a customer reaction, but AUC is reported by month so the delivery-time drift stays visible.
Caveat : ~5,100 orders have a review created before the recorded delivery date, so the delivery features are
         not strictly earlier than the review for those. Reported, not hidden.

Usage (from project root):  python -m olist_risk.model_review
"""
from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from olist_risk import features as ft
from olist_risk import ml_common as mc
from olist_risk.build_db import ROOT
from olist_risk.model_late import importance

log = logging.getLogger("olist_risk.model_review")
TABLE_DIR, FIG_DIR = ROOT / "reports" / "tables", ROOT / "reports" / "figures"
T = pd.Timestamp
TRAIN_END, TEST_START, TEST_END = T("2018-03-31"), T("2018-04-01"), T("2018-08-31")

LOSS_GIVEN_BAD_REVIEW = 0.30   # share of order value lost (refund, churn) behind a 1-2 star review
SUCCESS_RATE = 0.25            # share of flagged would-be bad reviews an outreach converts
INTERVENTION_COST = 3.0        # R$ per outreach (message / small credit)


def prepare() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    df = ft.load_orders()
    eligible = df["is_low"].notna() & df["delivery_days"].notna()
    train, test, priors = ft.split_with_history(df, eligible, TRAIN_END, TEST_START, TEST_END, known_col="answered_ts")
    train, test = train.assign(is_low=train["is_low"].astype(int)), test.assign(is_low=test["is_low"].astype(int))
    priors["aov"] = float(train["price_total"].mean())
    priors["review_before_delivery_share_test"] = float((test["answered_ts"] < test["delivered_ts"]).mean())
    return train, test, priors


def cost_model(stats: dict, cost: float = INTERVENTION_COST) -> mc.CostModel:
    return mc.CostModel(exposure=stats["aov"] * LOSS_GIVEN_BAD_REVIEW, success_rate=SUCCESS_RATE, intervention_cost=cost)


def run() -> dict:
    train, test, stats = prepare()
    sets = {"at approval": (ft.APPROVAL_NUMERIC, ft.APPROVAL_CATEGORICAL),
            "at delivery": (ft.APPROVAL_NUMERIC + ft.DELIVERY_NUMERIC, ft.APPROVAL_CATEGORICAL)}
    exps = {name: mc.run_experiment(train, test, num, cat, "is_low") for name, (num, cat) in sets.items()}
    y = test["is_low"].to_numpy()
    best_set = "at delivery"
    exp = exps[best_set]
    p = exp["scores"][exp["best"]].to_numpy()

    scores = pd.DataFrame({"y": y}, index=test.index)
    curves = {}
    for name, e in exps.items():
        scores[name] = e["scores"][e["best"]].to_numpy()
        curves[name] = {"pr_auc": e["models"][e["best"]]["pr_auc"]}
    by_month = {m: {"n": int(len(g)), "base_rate": float(g["is_low"].mean()),
                    "auc_at_approval": float(roc_auc_score(g["is_low"], exps["at approval"]["scores"].loc[g.index, exps["at approval"]["best"]])),
                    "auc_at_delivery": float(roc_auc_score(g["is_low"], exp["scores"].loc[g.index, exp["best"]]))}
                for m, g in test.groupby("purchase_month")}
    cm = cost_model(stats)
    imp = importance(exp["fitted"][exp["best"]], test, sets[best_set][0] + sets[best_set][1], "is_low")
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    imp.to_csv(TABLE_DIR / "review_feature_importance.csv", index=False)
    mc.calibration_table(y, p).to_csv(TABLE_DIR / "review_calibration.csv", index=False)

    m10 = exp["models"][exp["best"]]["at_shares"]["0.10"]
    mc.fig_precision_recall(scores, curves, best_set,
        f"Delivery outcome lifts bad-review prediction: PR-AUC {curves['at delivery']['pr_auc']:.2f} vs {curves['at approval']['pr_auc']:.2f} before shipping",
        f"1-2 star review model on Apr-Aug 2018 (unseen). Top-10% flagged: {m10['precision_top'] * 100:.0f}% precision vs {m10['base_rate'] * 100:.1f}% base rate ({m10['lift_top']:.1f}x lift)",
        FIG_DIR / "14_low_review_precision_recall.png")
    top10 = mc.net_at_shares(y, p, cm, (0.10,))[0]
    mc.fig_net_benefit(y, p, cm, [1.0, 3.0, 8.0],
        f"Outreach to likely unhappy customers pays if it costs under about R${top10['break_even_cost']:.1f} each",
        f"Net benefit vs share flagged, at-delivery model. Assumes {LOSS_GIVEN_BAD_REVIEW * 100:.0f}% of order value lost per bad review, {SUCCESS_RATE * 100:.0f}% rescue rate (exposure R${cm.exposure:.0f})",
        FIG_DIR / "15_low_review_net_benefit.png")

    results = {"split": {"train_end": str(TRAIN_END.date()), "test": [str(TEST_START.date()), str(TEST_END.date())],
                         "n_train": len(train), "n_test": len(test), "train_base_rate": exp["train_base_rate"], "test_base_rate": float(y.mean())},
               "stats": stats, "models": {k: {"best": e["best"], "metrics": e["models"]} for k, e in exps.items()},
               "by_test_month": by_month, "exposure_per_bad_review": cm.exposure,
               "net_at_shares": mc.net_at_shares(y, p, cm), "top_features_at_delivery": imp.head(10).to_dict("records")}
    (ROOT / "reports" / "review_model_results.json").write_text(json.dumps(results, indent=2, default=float), encoding="utf-8")
    log.info("at approval pr_auc=%.3f | at delivery pr_auc=%.3f", curves["at approval"]["pr_auc"], curves["at delivery"]["pr_auc"])
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run()
