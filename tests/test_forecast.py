"""Unit tests for the forecasting module (synthetic series: fast, no database needed)."""
import numpy as np
import pandas as pd
import pytest

from olist_risk import forecast as fc


@pytest.fixture
def series() -> pd.Series:
    idx = pd.date_range("2017-01-01", periods=20, freq="MS")
    rng = np.random.default_rng(0)
    vals = 1000 * np.exp(0.05 * np.arange(20)) * (1 + rng.normal(0, 0.03, 20))
    return pd.Series(vals, index=idx, name="orders")


# ---- metrics -------------------------------------------------------------------------
def test_wape_known_value():
    assert fc.wape(np.array([100, 200]), np.array([110, 180])) == pytest.approx(30 / 300)


def test_bias_sign():
    assert fc.bias(np.array([100.0, 100.0]), np.array([120.0, 120.0])) == pytest.approx(0.2)  # over-forecast > 0


def test_mase_equals_one_for_naive_on_its_own_scale():
    train = np.array([10.0, 12.0, 11.0, 13.0])
    scale = np.abs(np.diff(train)).mean()
    assert fc.mase(np.array([1.0]), np.array([1.0 + scale]), train) == pytest.approx(1.0)


# ---- models --------------------------------------------------------------------------
@pytest.mark.parametrize("name", list(fc.MODELS))
def test_every_model_returns_positive_forecast_of_requested_length(series, name):
    out = fc.MODELS[name](series.to_numpy(), 3)
    assert out.shape == (3,) and np.all(out > 0) and np.all(np.isfinite(out))


def test_naive_repeats_last_value(series):
    assert fc.MODELS["naive"](series.to_numpy(), 2) == pytest.approx([series.iloc[-1]] * 2)


def test_seasonal_naive_copies_same_month_last_year(series):
    y = series.to_numpy()
    assert fc.MODELS["snaive"](y, 1)[0] == pytest.approx(y[len(y) - 12])


# ---- backtest integrity --------------------------------------------------------------
def test_backtest_has_no_lookahead(series):
    """Forecasts made at an origin must not change if later observations change."""
    base = fc.backtest(series, min_train=12, max_h=2)
    tampered = series.copy()
    tampered.iloc[-3:] = tampered.iloc[-3:] * 10  # alter only the last 3 months
    other = fc.backtest(tampered, min_train=12, max_h=2)
    first_origin = base["origin"].min()  # origin uses months <= index 11, far from the tampering
    a = base[base["origin"] == first_origin].sort_values(["model", "horizon"])["forecast"].to_numpy()
    b = other[other["origin"] == first_origin].sort_values(["model", "horizon"])["forecast"].to_numpy()
    assert a == pytest.approx(b)


def test_backtest_shape_and_targets_after_origin(series):
    bt = fc.backtest(series, min_train=12, max_h=3)
    assert (bt["target_month"] > bt["origin"]).all()
    assert set(bt["horizon"]) == {1, 2, 3}
    # actuals in the backtest are the true observations
    joined = bt.merge(series.rename("truth"), left_on="target_month", right_index=True)
    assert joined["actual"].to_numpy() == pytest.approx(joined["truth"].to_numpy())


def test_scoreboard_is_sorted_by_wape(series):
    board = fc.scoreboard(fc.backtest(series, min_train=12, max_h=3), series)
    assert board["wape"].is_monotonic_increasing
    assert {"model", "wape", "mape", "bias", "mase", "n"} <= set(board.columns)


# ---- intervals -----------------------------------------------------------------------
def test_interval_brackets_point_and_widens_with_horizon():
    point = np.array([100.0, 100.0, 100.0])
    lo, hi = fc.interval(point, sigma=0.1)
    assert np.all(lo < point) and np.all(point < hi)
    assert np.all(np.diff(hi - lo) > 0)


def test_coverage_is_fraction_inside_band(series):
    bt = fc.backtest(series, min_train=12, max_h=3)
    cov = fc.coverage(bt[bt["model"] == "naive"], sigma=10.0)  # absurdly wide band
    assert cov == pytest.approx(1.0)
