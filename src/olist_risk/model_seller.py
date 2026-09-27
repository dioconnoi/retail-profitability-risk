"""Model (c): seller failure, under two definitions that can be compared.

Snapshots: at each cutoff, features use only orders BEFORE it; labels use the 90 days AFTER it.
  A  Inactivity : >= 5 orders before the cutoff and none in the next 90 days.
  B  Quality    : >= 5 orders in the next 90 days AND (cancellation rate > CANCEL_THR OR avg review < review
                  threshold). The review threshold is derived from TRAINING label windows only (5th percentile).
Split : train = monthly cutoffs 2017-07-01..2018-03-01 (label windows end 2018-05-30); test = the single cutoff
        2018-06-02 whose label window ends at the data cutoff (2018-08-31). Label windows never overlap the test
        snapshot, so no training label leaks into the test period.

Usage (from project root):  python -m olist_risk.model_seller
"""
from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from olist_risk import features as ft
from olist_risk import ml_common as mc
from olist_risk.build_db import ROOT

log = logging.getLogger("olist_risk.model_seller")
TABLE_DIR, FIG_DIR = ROOT / "reports" / "tables", ROOT / "reports" / "figures"
T = pd.Timestamp
TRAIN_CUTOFFS = list(pd.date_range("2017-07-01", "2018-03-01", freq="MS"))
TEST_CUTOFF, WATCHLIST_CUTOFF = T("2018-06-02"), T("2018-08-31")
HORIZON_DAYS, MIN_ORDERS, MIN_LABEL_ORDERS = 90, 5, 5
CANCEL_THR = 0.05

NUMERIC = ["n_orders_total", "revenue_total", "n_orders_30d", "n_orders_60d", "n_orders_90d", "revenue_90d",
           "days_since_last", "tenure_days", "order_trend", "aov", "cancel_rate", "late_rate", "avg_review"]
CATEGORICAL = ["seller_state"]

# Cost assumptions for a retention programme (stated, never hidden)
PROFIT_MARGIN = 0.18        # platform profit-proxy margin on seller revenue (docs/02, 65% COGS)
SUCCESS_RATE = 0.30         # share of at-risk sellers a retention call keeps active
CONTACT_COST = 50.0         # R$ per seller contacted (account-manager time)


def derive_review_threshold(so: pd.DataFrame) -> float:
    """5th percentile of avg review over training label windows (sellers with enough orders)."""
    day = pd.Timedelta(days=1)
    vals = []
    for c in TRAIN_CUTOFFS:
        w = so[(so["purchase_ts"] >= c) & (so["purchase_ts"] < c + HORIZON_DAYS * day)]
        g = w.groupby("seller_id").agg(n=("order_id", "size"), r=("review_score", "mean"))
        vals.append(g.loc[g["n"] >= MIN_LABEL_ORDERS, "r"].dropna())
    return float(np.round(pd.concat(vals).quantile(0.05), 2))


def snapshot(so: pd.DataFrame, cutoff: pd.Timestamp, review_thr: float) -> pd.DataFrame:
    return ft.seller_snapshot(so, cutoff, HORIZON_DAYS, MIN_ORDERS, MIN_LABEL_ORDERS, CANCEL_THR, review_thr)


def bootstrap_ci(y: np.ndarray, p: np.ndarray, n_boot: int = 400, seed: int = 0) -> dict:
    """95% percentile bootstrap for ROC-AUC and PR-AUC; essential when the test set has few positives."""
    rng = np.random.default_rng(seed)
    aucs, aps = [], []
    for _ in range(n_boot):
        idx = rng.integers(0, len(y), len(y))
        if y[idx].sum() in (0, len(idx)):
            continue
        aucs.append(roc_auc_score(y[idx], p[idx]))
        aps.append(average_precision_score(y[idx], p[idx]))
    return {"roc_auc_ci": [float(np.percentile(aucs, 2.5)), float(np.percentile(aucs, 97.5))],
            "pr_auc_ci": [float(np.percentile(aps, 2.5)), float(np.percentile(aps, 97.5))]}


def run_definition(name: str, target: str, train: pd.DataFrame, test: pd.DataFrame) -> dict:
    tr, te = train.dropna(subset=[target]), test.dropna(subset=[target])
    exp = mc.run_experiment(tr, te, NUMERIC, CATEGORICAL, target)
    best = exp["best"]
    y, p = exp["scores"]["y"].to_numpy(), exp["scores"][best].to_numpy()
    singles = {"days_since_last": roc_auc_score(y, te["days_since_last"]), "n_orders_90d_neg": roc_auc_score(y, -te["n_orders_90d"]),
               "avg_review_neg": roc_auc_score(y, -te["avg_review"].fillna(te["avg_review"].median())),
               "cancel_rate": roc_auc_score(y, te["cancel_rate"].fillna(0))}
    return {"definition": name, "n_train": len(tr), "n_test": len(te), "positives_train": int(tr[target].sum()),
            "positives_test": int(y.sum()), "train_base_rate": exp["train_base_rate"], "test_base_rate": float(y.mean()),
            "best": best, "metrics": exp["models"], "single_feature_auc": {k: float(v) for k, v in singles.items()},
            "ci": bootstrap_ci(y, p), "exp": exp, "y": y, "p": p, "test_index": te.index}


def watchlist(so: pd.DataFrame, exp_a: dict, exp_b: dict | None, review_thr: float) -> pd.DataFrame:
    """Score sellers active at the data cutoff. Expected profit at risk = P(inactive) x run-rate profit."""
    snap = snapshot(so, WATCHLIST_CUTOFF, review_thr)
    cols = NUMERIC + CATEGORICAL
    snap["p_inactive"] = exp_a["fitted"][exp_a["best"]].predict_proba(snap[cols])[:, 1]
    if exp_b is not None:
        snap["p_quality_fail"] = exp_b["fitted"][exp_b["best"]].predict_proba(snap[cols])[:, 1]
    snap["run_rate_profit_90d"] = run_rate_profit(snap)
    snap["expected_profit_at_risk"] = snap["p_inactive"] * snap["run_rate_profit_90d"]
    keep = ["seller_id", "seller_state", "n_orders_total", "n_orders_90d", "days_since_last", "avg_review", "late_rate",
            "cancel_rate", "p_inactive", "p_quality_fail", "run_rate_profit_90d", "expected_profit_at_risk"]
    return snap[[c for c in keep if c in snap.columns]].sort_values("expected_profit_at_risk", ascending=False)


def run_rate_profit(df: pd.DataFrame) -> pd.Series:
    """Profit at stake if a currently active seller stays: its last-90-day revenue x profit-proxy margin."""
    return df["revenue_90d"] * PROFIT_MARGIN


def active_only(df: pd.DataFrame) -> pd.DataFrame:
    """Sellers with at least one order in the 90 days before the cutoff. Definition A on ALL eligible sellers
    is inflated: it counts sellers that were already dormant before the cutoff, which recency alone 'predicts'."""
    return df[df["n_orders_90d"] >= 1]


def run() -> dict:
    from olist_risk.features import load_seller_orders
    so = load_seller_orders()
    review_thr = derive_review_threshold(so)
    train = pd.concat([snapshot(so, c, review_thr) for c in TRAIN_CUTOFFS], ignore_index=True)
    # Definition B needs review scores, which keep arriving for ~2 weeks after a label window closes: drop the last
    # training cutoff for B so no training label depends on a review answered after the test cutoff (embargo).
    train_b = train[train["cutoff"] < TRAIN_CUTOFFS[-1]]
    test = snapshot(so, TEST_CUTOFF, review_thr)
    res_all = run_definition("A inactivity, all eligible sellers", "fail_a", train, test)
    res_a = run_definition("A inactivity, currently active sellers", "fail_a", active_only(train), active_only(test))
    res_b = run_definition("B quality", "fail_b", train_b, test)

    # economics for definition A (churn prevention), per-seller exposure
    te_a = test.loc[res_a["test_index"]]
    exposure = run_rate_profit(te_a).to_numpy()
    cm = mc.CostModel(exposure=float(exposure.mean()), success_rate=SUCCESS_RATE, intervention_cost=CONTACT_COST)
    net = mc.net_at_shares(res_a["y"], res_a["p"], cm, exposure=exposure)
    wl = watchlist(so, res_a["exp"], res_b["exp"], review_thr)
    wl = wl[wl["n_orders_90d"] >= 1]                       # already-dormant sellers are not "at risk", they have gone

    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    wl.head(100).to_csv(TABLE_DIR / "seller_watchlist_top100.csv", index=False)
    scores = te_a[["seller_id"]].assign(y_a=res_a["y"], p_a=res_a["p"], exposure=exposure)
    scores.to_csv(TABLE_DIR / "seller_test_scores_a.csv", index=False)
    m_a, m_b = res_a["metrics"][res_a["best"]], res_b["metrics"][res_b["best"]]
    mc.fig_precision_recall(res_a["exp"]["scores"], res_a["metrics"], res_a["best"],
        f"Among active sellers, going silent is predictable: ROC-AUC {m_a['roc_auc']:.2f} vs {res_a['single_feature_auc']['days_since_last']:.2f} for a recency rule",
        f"Active sellers, Jun-Aug 2018: {res_a['n_test']:,} sellers, {res_a['test_base_rate'] * 100:.0f}% went silent (all-seller AUC {res_all['metrics'][res_all['best']]['roc_auc']:.2f} is inflated by dormant sellers)",
        FIG_DIR / "16_seller_inactivity_precision_recall.png")
    mc.fig_precision_recall(res_b["exp"]["scores"], res_b["metrics"], res_b["best"],
        f"Quality failures are rare ({res_b['positives_test']} of {res_b['n_test']} sellers) and harder to rank: ROC-AUC {m_b['roc_auc']:.2f}",
        f"Definition B (cancellation > {CANCEL_THR * 100:.0f}% or avg review < {review_thr:.2f}) on the same snapshot. 95% CI on ROC-AUC {res_b['ci']['roc_auc_ci'][0]:.2f} to {res_b['ci']['roc_auc_ci'][1]:.2f}",
        FIG_DIR / "17_seller_quality_precision_recall.png")
    top10 = net[1]
    mc.fig_net_benefit(res_a["y"], res_a["p"], cm, [20.0, CONTACT_COST, 150.0],
        f"Retention calls pay only if they cost under about R${top10['break_even_cost']:,.0f} per seller",
        f"Net benefit of contacting predicted-inactive sellers, each seller's own 90-day profit; {SUCCESS_RATE * 100:.0f}% of calls keep the seller",
        FIG_DIR / "18_seller_retention_net_benefit.png", money_unit=1000.0, exposure=exposure)

    out = {"thresholds": {"cancel": CANCEL_THR, "review_p5_train": review_thr},
           "snapshots": {"train_cutoffs": [str(c.date()) for c in TRAIN_CUTOFFS], "test_cutoff": str(TEST_CUTOFF.date())},
           "overlap_a_b_test": pd.crosstab(test["fail_a"], test["fail_b"].fillna(-1)).to_dict(),
           "A_all_eligible": {k: v for k, v in res_all.items() if k not in ("exp", "y", "p", "test_index")},
           "A": {k: v for k, v in res_a.items() if k not in ("exp", "y", "p", "test_index")},
           "B": {k: v for k, v in res_b.items() if k not in ("exp", "y", "p", "test_index")},
           "cost": {"success_rate": SUCCESS_RATE, "contact_cost": CONTACT_COST, "mean_exposure": cm.exposure}, "net_at_shares_A": net,
           "watchlist_n": len(wl)}
    (ROOT / "reports" / "seller_model_results.json").write_text(json.dumps(out, indent=2, default=float), encoding="utf-8")
    log.info("A: auc=%.3f | B: auc=%.3f", m_a["roc_auc"], m_b["roc_auc"])
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run()
