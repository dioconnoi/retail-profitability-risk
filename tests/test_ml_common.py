"""Unit tests for the shared modelling utilities."""
import numpy as np
import pandas as pd
import pytest

from olist_risk import ml_common as mc


def test_cost_model_net_and_break_even():
    cost = mc.CostModel(exposure=100.0, success_rate=0.5, intervention_cost=10.0)
    # 3 true positives gain 0.5*100 - 10 = 40 each; 2 false positives cost 10 each
    assert cost.net(tp=3, fp=2) == pytest.approx(3 * 40 - 2 * 10)
    # a flagged order pays for itself when precision * s * E > c  ->  break-even cost = precision * s * E
    assert cost.break_even_cost(precision=0.4) == pytest.approx(0.4 * 0.5 * 100)


def test_benefit_curve_perfect_ranking_peaks_at_base_rate_share():
    y = np.array([1] * 20 + [0] * 80)
    p = np.r_[np.linspace(0.99, 0.9, 20), np.linspace(0.5, 0.01, 80)]
    curve = mc.benefit_curve(y, p, mc.CostModel(100.0, 0.5, 10.0))
    best = curve.loc[curve["net"].idxmax()]
    assert best["share_flagged"] == pytest.approx(0.20)       # flag exactly the positives
    assert best["net"] == pytest.approx(20 * (50 - 10))


def test_benefit_curve_random_ranking_is_not_profitable_when_cost_is_high():
    rng = np.random.default_rng(1)
    y = (rng.random(5000) < 0.1).astype(int)
    curve = mc.benefit_curve(y, rng.random(5000), mc.CostModel(exposure=30.0, success_rate=0.3, intervention_cost=5.0))
    assert curve["net"].iloc[-1] < 0                            # flagging everyone at random loses money


def test_evaluate_reports_all_metrics_and_orders_models_sanely():
    rng = np.random.default_rng(2)
    y = (rng.random(4000) < 0.15).astype(int)
    good = np.clip(y * 0.5 + rng.random(4000) * 0.6, 0, 1)
    m_good, m_rand = mc.evaluate(y, good, top_share=0.1), mc.evaluate(y, rng.random(4000), top_share=0.1)
    assert m_good["roc_auc"] > 0.8 > m_rand["roc_auc"] > 0.4
    assert {"pr_auc", "brier", "base_rate", "precision_top", "recall_top", "lift_top", "n"} <= set(m_good)
    assert m_good["lift_top"] > m_rand["lift_top"]


def test_top_share_precision_and_recall_hand_checked():
    y = np.array([1, 1, 0, 0, 0, 0, 0, 0, 0, 0])
    p = np.array([0.9, 0.1, 0.8, 0.2, 0.3, 0.1, 0.05, 0.04, 0.03, 0.02])
    m = mc.evaluate(y, p, top_share=0.2)                       # flag top 2: {0.9 (pos), 0.8 (neg)}
    assert m["precision_top"] == pytest.approx(0.5) and m["recall_top"] == pytest.approx(0.5)
    assert m["lift_top"] == pytest.approx(0.5 / 0.2)


def test_calibration_table_bins_cover_all_rows():
    rng = np.random.default_rng(3)
    p = rng.random(1000)
    y = (rng.random(1000) < p).astype(int)                      # perfectly calibrated by construction
    cal = mc.calibration_table(y, p, bins=10)
    assert cal["n"].sum() == 1000
    assert (cal["mean_pred"] - cal["obs_rate"]).abs().max() < 0.12


def test_pipelines_fit_and_predict_with_missing_and_unseen_categories():
    rng = np.random.default_rng(4)
    train = pd.DataFrame({"x": rng.normal(size=400), "c": rng.choice(["a", "b", "c"], 400)})
    train.loc[::17, "x"] = np.nan
    y = (train["x"].fillna(0) + (train["c"] == "a") * 1.0 + rng.normal(0, 0.5, 400) > 0.8).astype(int)
    test = pd.DataFrame({"x": [0.1, np.nan], "c": ["a", "NEW_CATEGORY"]})
    for name, pipe in mc.build_pipelines(["x"], ["c"]).items():
        p = pipe.fit(train, y).predict_proba(test)[:, 1]
        assert p.shape == (2,) and np.all((p >= 0) & (p <= 1)), name


def test_benefit_curve_with_per_item_exposure():
    y = np.array([1, 1, 0])
    p = np.array([0.9, 0.8, 0.1])
    exposure = np.array([200.0, 50.0, 999.0])          # false positive's exposure is irrelevant
    cost = mc.CostModel(exposure=0.0, success_rate=0.5, intervention_cost=10.0)
    curve = mc.benefit_curve(y, p, cost, exposure=exposure)
    # item 1: 0.5*200-10 = 90 | item 2: +0.5*50-10 = 15 -> 105 | item 3 (false positive): -10 -> 95
    assert curve["net"].tolist() == pytest.approx([90.0, 105.0, 95.0])


def test_net_at_shares_accepts_exposure_vector():
    y = np.array([1, 0, 0, 0])
    p = np.array([0.9, 0.1, 0.2, 0.3])
    rows = mc.net_at_shares(y, p, mc.CostModel(0.0, 1.0, 5.0), shares=(0.25,), exposure=np.array([100.0, 1.0, 1.0, 1.0]))
    assert rows[0]["net"] == pytest.approx(100 - 5) and rows[0]["precision"] == pytest.approx(1.0)


def test_break_even_uses_exposure_of_flagged_items_not_the_average():
    """With per-item exposure the break-even is the mean gross benefit per FLAG, not precision x mean exposure."""
    y = np.array([1, 0, 1, 0])
    p = np.array([0.9, 0.8, 0.7, 0.1])
    exposure = np.array([10.0, 500.0, 1000.0, 500.0])       # the big exposures sit on a false positive / lower rank
    rows = mc.net_at_shares(y, p, mc.CostModel(0.0, 1.0, 1.0), shares=(0.25, 0.75), exposure=exposure)
    assert rows[0]["break_even_cost"] == pytest.approx(10.0)             # 1 flag: gross 10
    assert rows[1]["break_even_cost"] == pytest.approx((10.0 + 1000.0) / 3)   # 3 flags: two true positives
