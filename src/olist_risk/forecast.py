"""Step 4: monthly order-count forecasting with a rolling-origin backtest.

Design constraints (see docs/04_forecasting.md):
  * 20 monthly points cannot identify seasonality (needs >= 2 cycles; Nov-2017 is a single spike).
    So we compare simple, defensible methods against seasonal-naive and let the backtest choose.
  * Every forecast is made from data available at its origin only (no look-ahead; unit-tested).
  * Intervals come from backtest log-errors and their coverage is checked, not assumed.

Usage (from project root):  python -m olist_risk.forecast
"""
from __future__ import annotations

import json
import logging
import warnings
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd
from statsmodels.tsa.holtwinters import ExponentialSmoothing

from olist_risk import viz
from olist_risk.build_db import ROOT
from olist_risk.data import query, with_month_ts

log = logging.getLogger("olist_risk.forecast")
TABLE_DIR = ROOT / "reports" / "tables"
FIG_DIR = ROOT / "reports" / "figures"
Z80 = 1.2816  # two-sided 80% normal quantile
TIE_MARGIN = 0.02  # methods within 2 WAPE points are treated as tied (21 forecasts cannot separate them)

ModelFn = Callable[[np.ndarray, int], np.ndarray]


# --------------------------------------------------------------------------- metrics
def wape(actual: np.ndarray, fcst: np.ndarray) -> float:
    """Weighted absolute percentage error: sum|a-f| / sum(a). Robust to small actuals."""
    return float(np.abs(actual - fcst).sum() / np.abs(actual).sum())


def mape(actual: np.ndarray, fcst: np.ndarray) -> float:
    return float(np.mean(np.abs(actual - fcst) / np.abs(actual)))


def bias(actual: np.ndarray, fcst: np.ndarray) -> float:
    """Relative bias: > 0 means over-forecasting."""
    return float((fcst - actual).sum() / actual.sum())


def mase(actual: np.ndarray, fcst: np.ndarray, train: np.ndarray) -> float:
    """Mean absolute scaled error vs the in-sample one-step naive method (1.0 = same as naive)."""
    scale = np.abs(np.diff(train)).mean()
    return float(np.mean(np.abs(actual - fcst)) / scale)


# --------------------------------------------------------------------------- models
def _naive(y: np.ndarray, h: int) -> np.ndarray:
    return np.repeat(y[-1], h)


def _ma3(y: np.ndarray, h: int) -> np.ndarray:
    return np.repeat(y[-3:].mean(), h)


def _drift6(y: np.ndarray, h: int) -> np.ndarray:
    """Log-linear drift over the last 6 months (growth rate estimated from recent data only)."""
    slope = (np.log(y[-1]) - np.log(y[-7])) / 6
    return np.exp(np.log(y[-1]) + slope * np.arange(1, h + 1))


def _holt_damped(y: np.ndarray, h: int) -> np.ndarray:
    """Damped-trend exponential smoothing on log orders: growth fades instead of extrapolating."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fit = ExponentialSmoothing(np.log(y), trend="add", damped_trend=True,
                                   initialization_method="estimated").fit()
    return np.exp(np.asarray(fit.forecast(h)))


def _snaive(y: np.ndarray, h: int) -> np.ndarray:
    """Seasonal naive: same month last year. The standard baseline any model must beat."""
    n = len(y)
    return np.array([y[n + k - 12] for k in range(h)])  # requires n >= 12 and h <= 12


def _combo(y: np.ndarray, h: int) -> np.ndarray:
    return (_naive(y, h) + _ma3(y, h) + _holt_damped(y, h)) / 3


MODELS: dict[str, ModelFn] = {
    "naive": _naive, "ma3": _ma3, "drift6": _drift6,
    "holt_damped": _holt_damped, "snaive": _snaive, "combo": _combo,
}


# --------------------------------------------------------------------------- backtest
def backtest(y: pd.Series, min_train: int = 12, max_h: int = 3,
             models: dict[str, ModelFn] | None = None) -> pd.DataFrame:
    """Rolling-origin evaluation. At each origin only y[:n] is visible to the models."""
    models = models or MODELS
    rows: list[dict] = []
    for n in range(min_train, len(y)):
        train = y.iloc[:n].to_numpy()
        horizon = min(max_h, len(y) - n)
        for name, fn in models.items():
            preds = fn(train, horizon)
            for k in range(horizon):
                rows.append({"origin": y.index[n - 1], "horizon": k + 1, "target_month": y.index[n + k],
                             "model": name, "actual": float(y.iloc[n + k]), "forecast": float(preds[k])})
    return pd.DataFrame(rows)


def scoreboard(bt: pd.DataFrame, y: pd.Series) -> pd.DataFrame:
    out = []
    for name, g in bt.groupby("model"):
        a, f = g["actual"].to_numpy(), g["forecast"].to_numpy()
        scaled = [abs(r.actual - r.forecast) / np.abs(np.diff(y.loc[:r.origin].to_numpy())).mean()
                  for r in g.itertuples()]
        out.append({"model": name, "wape": wape(a, f), "mape": mape(a, f), "bias": bias(a, f),
                    "mase": float(np.mean(scaled)), "n": len(g)})
    return pd.DataFrame(out).sort_values("wape").reset_index(drop=True)


# --------------------------------------------------------------------------- intervals
def interval(point: np.ndarray, sigma: float, z: float = Z80) -> tuple[np.ndarray, np.ndarray]:
    """Multiplicative band: point * exp(+-z * sigma * sqrt(h)); error variance grows with horizon."""
    width = z * sigma * np.sqrt(np.arange(1, len(point) + 1))
    return point * np.exp(-width), point * np.exp(width)


def log_sigma(bt_model: pd.DataFrame) -> float:
    """RMS of one-step-ahead log errors (includes bias, so it does not flatter the model)."""
    h1 = bt_model[bt_model["horizon"] == 1]
    return float(np.sqrt(np.mean(np.log(h1["actual"] / h1["forecast"]) ** 2)))


def coverage(bt_model: pd.DataFrame, sigma: float, z: float = Z80) -> float:
    """Share of backtest actuals that fall inside the band implied by sigma."""
    width = z * sigma * np.sqrt(bt_model["horizon"].to_numpy())
    lo, hi = bt_model["forecast"].to_numpy() * np.exp(-width), bt_model["forecast"].to_numpy() * np.exp(width)
    a = bt_model["actual"].to_numpy()
    return float(np.mean((a >= lo) & (a <= hi)))


# --------------------------------------------------------------------------- final forecast
def load_series() -> tuple[pd.Series, pd.Series]:
    m = with_month_ts(query("SELECT purchase_month, orders, gross_revenue FROM v_kpi_monthly")).set_index("month")
    return m["orders"].astype(float).rename("orders"), m["gross_revenue"].astype(float).rename("revenue")


def next_months(y: pd.Series, h: int) -> pd.DatetimeIndex:
    return pd.date_range(y.index[-1] + pd.offsets.MonthBegin(1), periods=h, freq="MS")


def final_forecast(y: pd.Series, model: str, sigma: float, h: int = 3) -> pd.DataFrame:
    point = MODELS[model](y.to_numpy(), h)
    lo, hi = interval(point, sigma)
    return pd.DataFrame({"point": point, "lo80": lo, "hi80": hi}, index=next_months(y, h))


def november_scenario(y: pd.Series, fcst: pd.DataFrame) -> dict:
    """Black-Friday scenario: uplift observed in Nov-2017 vs Sep-Oct 2017, applied to Nov-2018.
    Based on ONE observation, so it is a labelled scenario, never a model output."""
    nov, sep, octo = (y.loc[f"2017-{m}-01"] for m in ("11", "09", "10"))
    uplift = float(nov / np.mean([sep, octo]))
    base = float(fcst.loc["2018-11-01", "point"])
    return {"uplift_vs_sep_oct_2017": uplift, "base_nov_2018": base, "scenario_nov_2018": base * uplift}


def revenue_check(bt_model: pd.DataFrame, orders: pd.Series, revenue: pd.Series, window: int = 6) -> float:
    """WAPE of revenue forecast = order forecast x trailing-6-month AOV known at the origin."""
    fc_rev, act = [], []
    for r in bt_model.itertuples():
        hist_o, hist_r = orders.loc[:r.origin].iloc[-window:], revenue.loc[:r.origin].iloc[-window:]
        fc_rev.append(r.forecast * hist_r.sum() / hist_o.sum())
        act.append(revenue.loc[r.target_month])
    return wape(np.array(act), np.array(fc_rev))


# --------------------------------------------------------------------------- figures
def fig_forecast(y: pd.Series, fcst: pd.DataFrame, scenario: dict, model: str) -> Path:
    fig, ax = viz.new_axes()
    ax.plot(y.index, y.values, color=viz.BLUE, marker="o", ms=4, zorder=3)
    ax.fill_between(fcst.index, fcst["lo80"], fcst["hi80"], color=viz.ORANGE, alpha=0.18, lw=0, zorder=1)
    ax.plot([y.index[-1], *fcst.index], [y.iloc[-1], *fcst["point"]], color=viz.ORANGE, ls="--", marker="o", ms=4, zorder=3)
    nov = pd.Timestamp("2018-11-01")
    ax.scatter([nov], [scenario["scenario_nov_2018"]], s=70, facecolor=viz.SURFACE, edgecolor=viz.ORANGE, lw=2, zorder=4)
    ax.annotate(f"Black-Friday scenario: {scenario['scenario_nov_2018']:,.0f}\n(Nov-2017 uplift x base; one year of evidence)",
                (nov, scenario["scenario_nov_2018"]), xytext=(-12, 0), textcoords="offset points",
                fontsize=8.5, color=viz.INK_2, va="center", ha="right")
    ax.set_ylim(0, max(y.max(), scenario["scenario_nov_2018"]) * 1.12)
    ax.set_xlim(y.index.min() - pd.Timedelta(days=25), nov + pd.Timedelta(days=75))
    ax.set_ylabel("Delivered orders per month")
    ax.yaxis.set_major_formatter(viz.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    last = fcst.iloc[-1]
    ax.text(nov, fcst["lo80"].min() * 0.88, f"Base forecast ({model})\nwith 80% interval",
            color=viz.ORANGE, fontsize=8.5, fontweight="bold", ha="right", va="top")
    viz.title_block(fig, f"Orders are forecast to hold near {fcst['point'].iloc[0]:,.0f} a month through autumn 2018",
                    f"Delivered orders per month with a {model} forecast for Sep-Nov 2018 (80% band: {last['lo80']:,.0f} to {last['hi80']:,.0f} by Nov)")
    viz.source_note(fig, "Source: Olist public dataset. Band from backtest log-errors; November scenario applies the Nov-2017 uplift.")
    return viz.save(fig, FIG_DIR / "08_orders_forecast.png")


def fig_scoreboard(board: pd.DataFrame) -> Path:
    b = board.iloc[::-1]
    fig, ax = viz.new_axes(9.4, 5.2)
    tied = board[board["wape"] <= board["wape"].iloc[0] + TIE_MARGIN]["model"].tolist()
    colors = [viz.BLUE if m in tied else viz.AXIS for m in b["model"]]
    ax.barh(b["model"], b["wape"], color=colors, height=0.62, zorder=2)
    ax.grid(axis="y", visible=False)
    viz.pct(ax, "x")
    for y_pos, r in enumerate(b.itertuples()):
        viz.bar_label(ax, r.wape + b["wape"].max() * 0.012, y_pos, f"WAPE {r.wape * 100:.1f}%  |  MASE {r.mase:.2f}  |  bias {r.bias * 100:+.1f}%")
    ax.set_xlim(0, b["wape"].max() * 1.65)
    sn = board.loc[board["model"] == "snaive", "wape"].iloc[0]
    hi = board.loc[board["model"].isin(tied), "wape"].max()
    viz.title_block(fig, f"{len(tied)} simple methods tie at {board['wape'].iloc[0] * 100:.0f}-{hi * 100:.0f}% error; seasonal-naive is off by {sn * 100:.0f}%",
                    f"Rolling-origin backtest, horizons 1-3 months, {int(board['n'].iloc[0])} forecasts per method. Blue = within {TIE_MARGIN * 100:.0f} points of the best. MASE < 1 beats one-step naive",
                    left=0.15)
    viz.source_note(fig, "Source: Olist public dataset. Lower is better. 20 months of history, so differences of a few points are noise.")
    return viz.save(fig, FIG_DIR / "09_forecast_backtest_wape.png")


# --------------------------------------------------------------------------- driver
def _jsonable(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, pd.Timestamp):
        return o.strftime("%Y-%m")
    raise TypeError(type(o))


def run() -> dict:
    orders, revenue = load_series()
    bt = backtest(orders)
    board = scoreboard(bt, orders)
    best = board["model"].iloc[0]
    bt_best = bt[bt["model"] == best]
    sigma = log_sigma(bt_best)
    fcst = final_forecast(orders, best, sigma)
    scenario = november_scenario(orders, fcst)
    aov = float(revenue.iloc[-6:].sum() / orders.iloc[-6:].sum())
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    bt.to_csv(TABLE_DIR / "forecast_backtest.csv", index=False)
    board.to_csv(TABLE_DIR / "forecast_scoreboard.csv", index=False)
    fcst.assign(revenue_point=fcst["point"] * aov).to_csv(TABLE_DIR / "forecast_next3.csv", index_label="month")
    results = {"best_model": best, "sigma_log_h1": sigma, "coverage_80": coverage(bt_best, sigma),
               "revenue_wape_best": revenue_check(bt_best, orders, revenue), "trailing6_aov": aov,
               "scoreboard": board.to_dict("records"), "forecast": fcst.reset_index().to_dict("records"),
               "november_scenario": scenario}
    fig_forecast(orders, fcst, scenario, best)
    fig_scoreboard(board)
    (ROOT / "reports" / "forecast_results.json").write_text(json.dumps(results, indent=2, default=_jsonable), encoding="utf-8")
    log.info("best=%s wape=%.3f coverage80=%.2f", best, board["wape"].iloc[0], results["coverage_80"])
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run()
