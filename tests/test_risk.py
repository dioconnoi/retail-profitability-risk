"""Unit tests for risk metrics (pure functions on small hand-checkable inputs)."""
import numpy as np
import pandas as pd
import pytest

from olist_risk import risk


def test_hhi_equal_shares_and_monopoly():
    assert risk.hhi(pd.Series([25, 25, 25, 25])) == pytest.approx(2500)
    assert risk.hhi(pd.Series([100])) == pytest.approx(10000)


def test_hhi_ignores_scale():
    assert risk.hhi(pd.Series([1, 3])) == pytest.approx(risk.hhi(pd.Series([100, 300])))


def test_max_drawdown_depth_and_dates():
    s = pd.Series([100, 120, 90, 110, 60, 80], index=pd.date_range("2018-01-01", periods=6, freq="MS"))
    dd = risk.max_drawdown(s)
    assert dd["depth"] == pytest.approx(-0.5)  # 120 -> 60
    assert dd["peak"] == pd.Timestamp("2018-02-01") and dd["trough"] == pd.Timestamp("2018-05-01")


def test_max_drawdown_monotonic_growth_is_zero():
    assert risk.max_drawdown(pd.Series([1.0, 2.0, 3.0]))["depth"] == 0.0


def test_log_growth_vol_constant_growth_is_zero():
    assert risk.log_growth_vol(pd.Series([100 * 1.1 ** i for i in range(8)])) == pytest.approx(0.0, abs=1e-12)


def test_scenario_p2_baseline_and_shocks():
    # revenue 100, freight 15, cogs 65% -> P2 = 35 - 15 = 20
    assert risk.scenario_p2(100, 15, 0.65) == pytest.approx(20)
    assert risk.scenario_p2(100, 15, 0.65, freight_mult=1.15) == pytest.approx(35 - 17.25)
    # demand scales revenue and freight together (variable costs only)
    assert risk.scenario_p2(100, 15, 0.65, demand_mult=0.8) == pytest.approx(16)
    combined = risk.scenario_p2(100, 15, 0.65, demand_mult=0.8, freight_mult=1.15)
    assert combined == pytest.approx(0.8 * (35 - 17.25))


def test_top_k_loss_takes_largest_sellers_and_their_profit():
    rev = pd.Series({"a": 50.0, "b": 30.0, "c": 20.0})
    p2 = pd.Series({"a": 5.0, "b": 9.0, "c": 6.0})
    r, p = risk.top_k_loss(rev, p2, k=2)
    assert r == 80.0 and p == 14.0


def test_cogs_sensitivity_monotonic_and_ranking_invariant():
    cat = pd.DataFrame({"category": ["x", "y", "z"], "revenue": [100.0, 100.0, 100.0], "freight": [10.0, 20.0, 30.0]})
    out = risk.cogs_sensitivity(cat, rates=[0.55, 0.65, 0.75])
    assert out["platform_margin"].is_monotonic_decreasing
    assert out["loss_revenue_share"].is_monotonic_increasing
    # margin = (1 - cogs) - freight/revenue: at 75% COGS, 25% - {10,20,30}% -> only z (-5%) loses money
    row = out[out["cogs"] == 0.75].iloc[0]
    assert row["n_loss_categories"] == 1 and row["loss_revenue_share"] == pytest.approx(1 / 3)


def test_late_exposure_delta_formula():
    # d(at_risk)/d(p_late) = 1 - q; doubling p_late adds p_late * (1 - q)
    assert risk.late_exposure_delta(p_late=0.07, p_low_on_time=0.10) == pytest.approx(0.07 * 0.90)
