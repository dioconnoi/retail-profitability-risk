"""Step 3: exploratory analysis. Reads the SQLite KPI layer, writes figures and a findings file.

Usage (from project root):  python -m olist_risk.eda
Every chart headline is computed from the data, so text can never drift from the numbers.
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

FIG_DIR = ROOT / "reports" / "figures"
FINDINGS_PATH = ROOT / "reports" / "eda_findings.json"
log = logging.getLogger("olist_risk.eda")


def _fmt_m(v: float) -> str:
    return f"R${v / 1e6:.2f}M"


# --------------------------------------------------------------------------- data
def load_monthly() -> pd.DataFrame:
    kpi = query("SELECT * FROM v_kpi_monthly")
    risk = query("SELECT purchase_month, late_rate, low_review_rate, cancellation_rate FROM v_risk_monthly")
    return with_month_ts(kpi.merge(risk, on="purchase_month"))


def load_drift() -> pd.DataFrame:
    return with_month_ts(query("""
        SELECT purchase_month,
               AVG(julianday(estimated_ts) - julianday(purchase_ts)) AS promised_days,
               AVG(delivery_days)                                    AS actual_days
        FROM fact_orders
        WHERE in_window = 1 AND is_delivered = 1 AND delivered_ts IS NOT NULL
        GROUP BY purchase_month"""))


def load_lateness_buckets() -> pd.DataFrame:
    return query("""
        SELECT CASE WHEN days_late = 0  THEN '1_on_time'
                    WHEN days_late <= 3 THEN '2_1-3 days late'
                    WHEN days_late <= 7 THEN '3_4-7 days late'
                    ELSE                     '4_8+ days late' END AS bucket,
               COUNT(*)                     AS orders,
               AVG(is_low)                  AS low_review_rate,
               AVG(review_score)            AS avg_review
        FROM fact_order_risk
        WHERE is_late IS NOT NULL AND review_score IS NOT NULL
        GROUP BY bucket ORDER BY bucket""")


# --------------------------------------------------------------------------- figures
def fig_revenue_profit(m: pd.DataFrame) -> tuple[Path, dict]:
    fig, ax = viz.new_axes()
    ax.bar(m["month"], m["gross_revenue"], width=22, color=viz.BLUE, zorder=2)
    ax.plot(m["month"], m["p2_assumed_margin"], color=viz.ORANGE, marker="o", ms=4, zorder=3)
    viz.money_k(ax)
    ax.set_ylim(0, m["gross_revenue"].max() * 1.12)
    ax.set_xlim(m["month"].min() - pd.Timedelta(days=25), m["month"].max() + pd.Timedelta(days=120))
    viz.end_label(ax, m["month"].iloc[-1], m["p2_assumed_margin"].iloc[-1], "Profit proxy (P2)", viz.ORANGE)
    ax.text(m["month"].iloc[1], m["gross_revenue"].max() * 1.02, "Revenue (delivered orders)",
            color=viz.BLUE, fontsize=9, fontweight="bold")
    avg_2017 = m.loc[m["month"].dt.year == 2017, "gross_revenue"].mean()
    last3 = m["gross_revenue"].iloc[-3:].mean()
    spring = m.loc[m["purchase_month"].isin(["2018-03", "2018-04", "2018-05"]), "gross_revenue"].mean()
    stats = {"avg_monthly_revenue_2017": avg_2017, "avg_monthly_revenue_last3": last3,
             "avg_monthly_revenue_mar_may_2018": spring, "last3_vs_spring": last3 / spring - 1,
             "peak_month": m.loc[m["gross_revenue"].idxmax(), "purchase_month"],
             "peak_revenue": m["gross_revenue"].max()}
    viz.title_block(fig, f"Revenue is {last3 / avg_2017:.1f}x its 2017 level but has slipped {abs(last3 / spring - 1) * 100:.0f}% since spring 2018",
                    f"Gross revenue and assumed-margin profit (65% COGS, less freight), R$ per month. Jun-Aug 2018 avg R${last3 / 1000:,.0f}k vs Mar-May R${spring / 1000:,.0f}k")
    viz.source_note(fig)
    return viz.save(fig, FIG_DIR / "01_revenue_profit_monthly.png"), stats


def fig_category_margin(cat: pd.DataFrame, platform_margin: float) -> tuple[Path, dict]:
    top = cat.nsmallest(12, "revenue_rank").sort_values("gross_revenue")
    fig, ax = viz.new_axes()
    colors = [viz.ORANGE if v < platform_margin else viz.BLUE for v in top["p2_margin_pct"]]
    ax.barh(top["category_en"], top["gross_revenue"], color=colors, height=0.68, zorder=2)
    ax.grid(axis="y", visible=False)
    viz.money_k(ax, "x")
    for y, (rev, mar) in enumerate(zip(top["gross_revenue"], top["p2_margin_pct"])):
        viz.bar_label(ax, rev + top["gross_revenue"].max() * 0.01, y, f"{_fmt_m(rev)}  |  margin {mar * 100:.1f}%")
    ax.set_xlim(0, top["gross_revenue"].max() * 1.35)
    ax.plot([], [], "s", color=viz.BLUE, label=f"Margin at or above platform ({platform_margin * 100:.1f}%)")
    ax.plot([], [], "s", color=viz.ORANGE, label="Margin below platform")
    ax.legend(loc="lower right", fontsize=9)
    low = top[top["p2_margin_pct"] < platform_margin]
    top_share = cat.nsmallest(12, "revenue_rank")["revenue_share"].sum()
    viz.title_block(fig, f"Twelve categories earn {top_share * 100:.0f}% of revenue, and {len(low)} of them earn below-average margin",
                    "Top 12 categories by revenue; label shows revenue and profit-proxy margin (after freight)", left=0.19)
    viz.source_note(fig)
    return viz.save(fig, FIG_DIR / "02_category_revenue_margin.png"), {
        "top12_revenue_share": top_share, "below_avg_margin_top12": low["category_en"].tolist()}


def fig_seller_concentration() -> tuple[Path, dict]:
    s = query("SELECT gross_revenue FROM v_seller_perf WHERE gross_revenue > 0 ORDER BY gross_revenue DESC")
    rev = s["gross_revenue"].to_numpy()
    cum = np.cumsum(rev) / rev.sum()
    x = np.arange(1, len(rev) + 1) / len(rev)
    top20 = float(cum[int(np.ceil(0.2 * len(rev))) - 1])
    top1pct = float(cum[int(np.ceil(0.01 * len(rev))) - 1])
    fig, ax = viz.new_axes(8.6, 6.0)
    ax.plot([0, 1], [0, 1], color=viz.MUTED, ls="--", lw=1.2, zorder=1)
    ax.plot(np.r_[0, x], np.r_[0, cum], color=viz.BLUE, zorder=3)
    ax.scatter([0.2], [top20], color=viz.BLUE, s=42, zorder=4, edgecolor=viz.SURFACE, linewidth=1.5)
    ax.annotate(f"Top 20% of sellers\n= {top20 * 100:.0f}% of revenue", (0.2, top20), xytext=(0.30, top20 - 0.16),
                fontsize=10, fontweight="bold", color=viz.INK,
                arrowprops={"arrowstyle": "-", "color": viz.MUTED, "lw": 1})
    ax.text(0.62, 0.50, "Equal split", color=viz.MUTED, fontsize=9, rotation=32)
    viz.pct(ax), viz.pct(ax, "x")
    ax.set_xlim(0, 1), ax.set_ylim(0, 1.02)
    ax.set_xlabel("Cumulative share of sellers (largest first)")
    ax.set_ylabel("Cumulative share of revenue")
    hhi = query("SELECT hhi FROM v_concentration WHERE dimension='seller'")["hhi"][0]
    viz.title_block(fig, "Seller revenue is skewed, yet no single seller matters",
                    f"Concentration curve, {len(rev):,} sellers. Seller HHI = {hhi:,.0f} (unconcentrated < 1,500)")
    viz.source_note(fig)
    return viz.save(fig, FIG_DIR / "03_seller_concentration.png"), {
        "top20pct_seller_share": top20, "top1pct_seller_share": top1pct, "n_sellers": int(len(rev))}


def fig_delivery_drift(d: pd.DataFrame) -> tuple[Path, dict]:
    fig, ax = viz.new_axes()
    ax.plot(d["month"], d["promised_days"], color=viz.ORANGE, marker="o", ms=4)
    ax.plot(d["month"], d["actual_days"], color=viz.BLUE, marker="o", ms=4)
    ax.set_ylim(0, d["promised_days"].max() * 1.12)
    ax.set_ylabel("Days from purchase")
    viz.shade_excluded(ax)
    viz.end_label(ax, d["month"].iloc[-1], d["promised_days"].iloc[-1], "Promised", viz.ORANGE)
    viz.end_label(ax, d["month"].iloc[-1], d["actual_days"].iloc[-1], "Actual", viz.BLUE)
    hist = d[d["month"] < pd.Timestamp("2018-06-01")]
    late = d[d["month"] >= pd.Timestamp("2018-06-01")]
    viz.title_block(fig, f"Delivery times dropped from {hist['actual_days'].mean():.0f} to {late['actual_days'].mean():.0f} days in mid-2018, a regime shift",
                    "Mean days from purchase to delivery (actual) and to the promised date, by purchase month, delivered orders")
    viz.source_note(fig)
    return viz.save(fig, FIG_DIR / "04_delivery_time_drift.png"), {
        "actual_days_before_jun18": hist["actual_days"].mean(), "actual_days_jun_aug18": late["actual_days"].mean(),
        "promised_days_before_jun18": hist["promised_days"].mean(), "promised_days_jun_aug18": late["promised_days"].mean()}


def fig_risk_rates(m: pd.DataFrame) -> tuple[Path, dict]:
    fig, ax = viz.new_axes()
    ax.plot(m["month"], m["late_rate"], color=viz.ORANGE, marker="o", ms=4)
    ax.plot(m["month"], m["low_review_rate"], color=viz.BLUE, marker="o", ms=4)
    viz.pct(ax)
    ax.set_ylim(0, max(m["late_rate"].max(), m["low_review_rate"].max()) * 1.15)
    viz.shade_excluded(ax)
    viz.end_label(ax, m["month"].iloc[-1], m["late_rate"].iloc[-1], "Late", viz.ORANGE, dy=-4)
    viz.end_label(ax, m["month"].iloc[-1], m["low_review_rate"].iloc[-1], "1-2 star", viz.BLUE, dy=4)
    worst = m.loc[m["late_rate"].idxmax()]
    viz.title_block(fig, f"Late deliveries peaked at {worst['late_rate'] * 100:.0f}% in {worst['purchase_month']}, the same month 1-2 star reviews peaked at {m['low_review_rate'].max() * 100:.0f}%",
                    "Share of delivered orders that arrived late, and share of reviewed orders scoring 1-2 stars, by purchase month")
    viz.source_note(fig, "Source: Olist public dataset. Late = delivered on a later calendar day than promised; excludes never-delivered orders.")
    return viz.save(fig, FIG_DIR / "05_late_and_low_review_rates.png"), {
        "worst_late_month": worst["purchase_month"], "worst_late_rate": worst["late_rate"]}


def fig_lateness_cost(b: pd.DataFrame) -> tuple[Path, dict]:
    labels = [s.split("_", 1)[1].replace("on_time", "on time") for s in b["bucket"]]
    fig, ax = viz.new_axes(9.0, 5.6)
    colors = [viz.BLUE] + [viz.ORANGE] * (len(b) - 1)
    ax.bar(labels, b["low_review_rate"], color=colors, width=0.62, zorder=2)
    viz.pct(ax)
    ax.grid(axis="x", visible=False)
    for i, (r, n) in enumerate(zip(b["low_review_rate"], b["orders"])):
        ax.text(i, r + 0.012, f"{r * 100:.0f}%", ha="center", fontweight="bold", fontsize=11)
        ax.text(i, 0.012, f"n = {n:,}", ha="center", fontsize=8.5, color="white")
    ax.set_ylim(0, b["low_review_rate"].max() * 1.15)
    on_time, worst = b["low_review_rate"].iloc[0], b["low_review_rate"].iloc[-1]
    viz.title_block(fig, f"An order 8+ days late is {worst / on_time:.0f}x as likely to get a 1-2 star review",
                    "Share of reviewed delivered orders scoring 1-2 stars, by days late vs promised date")
    viz.source_note(fig)
    return viz.save(fig, FIG_DIR / "06_lateness_vs_low_review.png"), {
        "low_review_rate_on_time": on_time, "low_review_rate_8plus_late": worst,
        "lateness_multiplier": worst / on_time}


def fig_states() -> tuple[Path, dict]:
    st = query("""SELECT state, gross_revenue, freight_ratio, revenue_share FROM v_state_perf
                  WHERE lens='customer' ORDER BY gross_revenue DESC LIMIT 10""").iloc[::-1]
    fig, ax = viz.new_axes()
    ax.barh(st["state"], st["revenue_share"], color=viz.BLUE, height=0.66, zorder=2)
    ax.grid(axis="y", visible=False)
    viz.pct(ax, "x")
    for y, (sh, fr) in enumerate(zip(st["revenue_share"], st["freight_ratio"])):
        viz.bar_label(ax, sh + 0.005, y, f"{sh * 100:.1f}%  |  freight {fr * 100:.0f}% of price")
    ax.set_xlim(0, st["revenue_share"].max() * 1.5)
    sp = st.iloc[-1]
    viz.title_block(fig, f"{sp['state']} alone is {sp['revenue_share'] * 100:.0f}% of revenue; other states pay more freight",
                    "Share of revenue by customer state (top 10), with freight as a percentage of item price")
    viz.source_note(fig)
    return viz.save(fig, FIG_DIR / "07_customer_state_revenue.png"), {
        "top_state": sp["state"], "top_state_share": sp["revenue_share"]}


# --------------------------------------------------------------------------- driver
def _jsonable(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    raise TypeError(f"not serialisable: {type(o)}")


def run() -> dict:
    monthly, drift = load_monthly(), load_drift()
    cat = query("SELECT * FROM v_category_perf")
    summary = query("SELECT * FROM v_kpi_summary").iloc[0]
    platform_margin = summary["p2_assumed_margin"] / summary["gross_revenue"]
    findings: dict = {"summary": {k: float(v) for k, v in summary.items()},
                      "platform_p2_margin": float(platform_margin),
                      "late_rates": query("SELECT * FROM v_late_rates").iloc[0].to_dict(),
                      "concentration": query("SELECT * FROM v_concentration").to_dict("records")}
    for name, (path, stats) in {
        "revenue": fig_revenue_profit(monthly), "category": fig_category_margin(cat, platform_margin),
        "sellers": fig_seller_concentration(), "drift": fig_delivery_drift(drift),
        "risk": fig_risk_rates(monthly), "lateness": fig_lateness_cost(load_lateness_buckets()),
        "states": fig_states(),
    }.items():
        findings[name] = stats
        log.info("wrote %s", path.name)
    FINDINGS_PATH.write_text(json.dumps(findings, indent=2, default=_jsonable), encoding="utf-8")
    return findings


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run()
