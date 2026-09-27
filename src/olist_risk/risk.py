"""Step 5: risk metrics and downside scenarios (Risk / Quant layer).

Baseline for every scenario = the Jun-Aug 2018 monthly average (most recent full quarter).
Scenarios are deliberately simple and transparent: the assumptions are the point, not false
precision. Costs are variable only (no fixed-cost data exists in Olist).

Usage (from project root):  python -m olist_risk.risk
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from olist_risk import viz
from olist_risk.build_db import ROOT
from olist_risk.data import query, with_month_ts

log = logging.getLogger("olist_risk.risk")
TABLE_DIR = ROOT / "reports" / "tables"
FIG_DIR = ROOT / "reports" / "figures"
COGS_RATES = [0.55, 0.60, 0.65, 0.70, 0.75]
BASE_COGS = 0.65


# --------------------------------------------------------------------------- pure metrics
def hhi(revenue: pd.Series) -> float:
    """Herfindahl-Hirschman index on a 0-10,000 scale (scale-invariant)."""
    share = revenue / revenue.sum()
    return float((share ** 2).sum() * 10_000)


def max_drawdown(s: pd.Series) -> dict:
    """Largest peak-to-trough fall of a level series, with the dates it happened."""
    running_peak = s.cummax()
    dd = s / running_peak - 1
    trough = dd.idxmin()
    peak = s.loc[:trough].idxmax()
    return {"depth": float(dd.min()), "peak": peak, "trough": trough}


def log_growth_vol(s: pd.Series) -> float:
    """Standard deviation of month-on-month log growth."""
    return float(np.log(s).diff().dropna().std(ddof=1))


def scenario_p2(revenue: float, freight: float, cogs: float, demand_mult: float = 1.0,
                freight_mult: float = 1.0) -> float:
    """Profit proxy under a shock. Demand scales revenue and freight together (variable costs);
    freight_mult scales the freight rate paid per order."""
    return demand_mult * (revenue * (1 - cogs) - freight_mult * freight)


def top_k_loss(seller_rev: pd.Series, seller_p2: pd.Series, k: int) -> tuple[float, float]:
    """Revenue and profit proxy lost if the k largest sellers (by revenue) disappear."""
    top = seller_rev.nlargest(k).index
    return float(seller_rev.loc[top].sum()), float(seller_p2.loc[top].sum())


def late_exposure_delta(p_late: float, p_low_on_time: float) -> float:
    """Extra share of orders 'at risk' if the late rate doubles.
    at_risk = p_late + (1 - p_late) * q  =>  d(at_risk)/d(p_late) = 1 - q, and doubling adds p_late."""
    return p_late * (1 - p_low_on_time)


def cogs_sensitivity(cat: pd.DataFrame, rates: list[float]) -> pd.DataFrame:
    """How the profit proxy behaves across COGS assumptions.
    Margin = (1 - cogs) - freight/revenue, so the ranking of categories never changes with cogs;
    only the level, and which categories cross below zero."""
    rows = []
    total_rev = cat["revenue"].sum()
    for c in rates:
        margin = (1 - c) - cat["freight"] / cat["revenue"]
        loss = margin < 0
        rows.append({"cogs": c, "platform_margin": (1 - c) - cat["freight"].sum() / total_rev,
                     "n_loss_categories": int(loss.sum()),
                     "loss_revenue_share": float(cat.loc[loss, "revenue"].sum() / total_rev)})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- data
def load_monthly() -> pd.DataFrame:
    return with_month_ts(query("SELECT purchase_month, gross_revenue, freight, p2_assumed_margin, orders FROM v_kpi_monthly")).set_index("month")


def load_trailing_sellers(months: int = 6) -> pd.DataFrame:
    """Per-seller revenue and P2 over the trailing `months`, for ranking and loss scenarios."""
    return query(f"""
        SELECT seller_id, SUM(price) AS revenue, SUM(p2_assumed_margin) AS p2
        FROM fact_sales
        WHERE purchase_month IN (SELECT purchase_month FROM v_kpi_monthly ORDER BY purchase_month DESC LIMIT {months})
        GROUP BY seller_id""").set_index("seller_id")


def load_concentration_trend() -> pd.DataFrame:
    seller = query("SELECT purchase_month, seller_id AS e, SUM(price) AS rev FROM fact_sales GROUP BY 1, 2")
    cat = query("SELECT purchase_month, category_en AS e, SUM(price) AS rev FROM fact_sales GROUP BY 1, 2")
    out = pd.DataFrame({
        "seller_hhi": seller.groupby("purchase_month")["rev"].apply(hhi),
        "seller_top10_share": seller.groupby("purchase_month")["rev"].apply(lambda r: r.nlargest(10).sum() / r.sum()),
        "category_hhi": cat.groupby("purchase_month")["rev"].apply(hhi)})
    return out


def load_category_costs() -> pd.DataFrame:
    return query("SELECT category_en AS category, SUM(price) AS revenue, SUM(freight_value) AS freight FROM fact_sales GROUP BY 1")


def load_late_probs() -> dict:
    r = query("""SELECT AVG(is_late) AS p_late,
                        AVG(CASE WHEN is_late = 0 THEN is_low END) AS q_on_time
                 FROM fact_order_risk WHERE is_late IS NOT NULL AND review_score IS NOT NULL""").iloc[0]
    return {"p_late": float(r["p_late"]), "p_low_on_time": float(r["q_on_time"])}


# --------------------------------------------------------------------------- scenarios
def build_scenarios(base: dict, sellers: pd.DataFrame, probs: dict) -> pd.DataFrame:
    rev, frt = base["revenue"], base["freight"]
    p2 = scenario_p2(rev, frt, BASE_COGS)
    rows = [{"scenario": "Baseline (Jun-Aug 2018 monthly avg)", "d_revenue": 0.0, "d_p2": 0.0, "d_at_risk_revenue": 0.0,
             "freight_monthly": frt, "p2_monthly": p2}]

    def add(name: str, demand: float = 1.0, freight: float = 1.0, at_risk: float = 0.0) -> None:
        rows.append({"scenario": name, "d_revenue": rev * (demand - 1),
                     "d_p2": scenario_p2(rev, frt, BASE_COGS, demand, freight) - p2,
                     "d_at_risk_revenue": at_risk})

    add("Demand -10%", demand=0.90)
    add("Demand -20%", demand=0.80)
    add("Freight rates +15%", freight=1.15)
    add("Freight rates +30%", freight=1.30)
    add("Late-delivery rate doubles", at_risk=rev * late_exposure_delta(**probs))
    for k, label in [(10, "Lose the top 10 sellers"), (int(np.ceil(0.05 * len(sellers))), "Lose the top 5% of sellers")]:
        lost_rev, lost_p2 = top_k_loss(sellers["revenue"], sellers["p2"], k)
        # the sellers table covers the trailing 6 months, so divide by 6 for a monthly figure
        rows.append({"scenario": f"{label} (no recapture)", "d_revenue": -lost_rev / 6,
                     "d_p2": -lost_p2 / 6, "d_at_risk_revenue": 0.0})
    add("Severe combined: demand -20%, freight +15%", demand=0.80, freight=1.15)
    out = pd.DataFrame(rows)
    out["d_p2_pct"] = out["d_p2"] / p2
    out["d_revenue_pct"] = out["d_revenue"] / rev
    return out


# --------------------------------------------------------------------------- figures
def fig_scenarios(sc: pd.DataFrame) -> Path:
    base = sc.iloc[0]
    late_exposure = sc.loc[sc["d_at_risk_revenue"] > 0, "d_at_risk_revenue"].iloc[0]
    d = sc.iloc[1:]
    d = d[d["d_p2"] != 0].sort_values("d_p2", ascending=False)  # exposure-only scenarios are noted in the footer
    fig, ax = viz.new_axes(10.0, 5.8)
    ax.barh(d["scenario"], d["d_p2"], color=viz.ORANGE, height=0.62, zorder=2)
    ax.axvline(0, color=viz.AXIS, lw=1)
    ax.grid(axis="y", visible=False)
    viz.money_k(ax, "x")
    for y, r in enumerate(d.itertuples()):
        viz.bar_label(ax, r.d_p2 - abs(d["d_p2"].min()) * 0.012, y, f"{r.d_p2 / 1000:,.0f}k  ({r.d_p2_pct * 100:.0f}%)", ha="right")
    ax.set_xlim(d["d_p2"].min() * 1.45, abs(d["d_p2"].min()) * 0.05)
    freight_x = base["freight_monthly"] / base["p2_monthly"]
    viz.title_block(fig, f"Freight (R${base['freight_monthly'] / 1000:,.0f}k a month) is larger than the profit proxy (R${base['p2_monthly'] / 1000:,.0f}k)",
                    f"Change in monthly profit proxy vs Jun-Aug 2018. Each 1% rise in freight rates costs {freight_x:.2f}% of profit. Variable costs only",
                    left=0.33)
    viz.source_note(fig, f"Illustrative stress tests, not forecasts. Not shown: doubling the late rate leaves profit unchanged but puts R${late_exposure / 1000:,.0f}k more monthly revenue at risk.")
    return viz.save(fig, FIG_DIR / "10_downside_scenarios.png")


def fig_cogs(cs: pd.DataFrame) -> Path:
    fig, ax = viz.new_axes(10.0, 5.6)
    labels = [f"{c * 100:.0f}%" for c in cs["cogs"]]
    colors = [viz.BLUE if abs(c - BASE_COGS) < 1e-9 else viz.AXIS for c in cs["cogs"]]
    ax.bar(labels, cs["n_loss_categories"], color=colors, width=0.6, zorder=2)
    ax.grid(axis="x", visible=False)
    top = cs["n_loss_categories"].max()
    for i, r in enumerate(cs.itertuples()):
        noun = "category" if r.n_loss_categories == 1 else "categories"
        text = (f"{r.n_loss_categories} {noun}\n{r.loss_revenue_share * 100:.1f}% of revenue\n"
                f"platform margin {r.platform_margin * 100:.1f}%")
        viz.bar_label(ax, i, r.n_loss_categories + top * 0.03, text, ha="center", va="bottom")
    ax.set_ylim(0, top * 1.55)
    ax.set_ylabel("Categories losing money after freight")
    ax.set_xlabel("Assumed cost of goods (share of price)")
    lo, hi = cs.iloc[0], cs.iloc[-1]
    viz.title_block(fig, f"Loss-making categories rise from {int(lo['n_loss_categories'])} to {int(hi['n_loss_categories'])} as assumed COGS goes from {lo['cogs'] * 100:.0f}% to {hi['cogs'] * 100:.0f}%",
                    "Categories with a negative profit proxy by assumed COGS rate (blue = base case). The ranking never changes; only the level does",
                    left=0.09)
    viz.source_note(fig, "Source: Olist public dataset. Olist has no cost data: COGS is an assumption, so the level is illustrative.")
    return viz.save(fig, FIG_DIR / "11_cogs_sensitivity.png")


# --------------------------------------------------------------------------- driver
def _jsonable(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, pd.Timestamp):
        return o.strftime("%Y-%m")
    raise TypeError(type(o))


def run() -> dict:
    m = load_monthly()
    last3 = m.iloc[-3:]
    base = {"revenue": float(last3["gross_revenue"].mean()), "freight": float(last3["freight"].mean())}
    base["p2"] = scenario_p2(base["revenue"], base["freight"], BASE_COGS)
    sellers, probs = load_trailing_sellers(), load_late_probs()
    sc = build_scenarios(base, sellers, probs)
    cs = cogs_sensitivity(load_category_costs(), COGS_RATES)
    trend = load_concentration_trend()
    rev = m["gross_revenue"]
    results = {
        "baseline_monthly": base, "late_probs": probs,
        "volatility": {"log_growth_vol_full": log_growth_vol(rev), "log_growth_vol_last12": log_growth_vol(rev.iloc[-12:]),
                       "cv_2018": float(rev.loc["2018-01-01":].std(ddof=1) / rev.loc["2018-01-01":].mean())},
        "max_drawdown": max_drawdown(rev), "current_drawdown": float(rev.iloc[-1] / rev.max() - 1),
        "concentration_first3": trend.iloc[:3].mean().to_dict(), "concentration_last3": trend.iloc[-3:].mean().to_dict(),
        "scenarios": sc.to_dict("records"), "cogs_sensitivity": cs.to_dict("records"),
    }
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    sc.to_csv(TABLE_DIR / "risk_scenarios.csv", index=False)
    cs.to_csv(TABLE_DIR / "risk_cogs_sensitivity.csv", index=False)
    trend.to_csv(TABLE_DIR / "risk_concentration_trend.csv", index_label="purchase_month")
    fig_scenarios(sc)
    fig_cogs(cs)
    (ROOT / "reports" / "risk_results.json").write_text(json.dumps(results, indent=2, default=_jsonable), encoding="utf-8")
    log.info("wrote risk tables, figures 10-11 and risk_results.json")
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run()
