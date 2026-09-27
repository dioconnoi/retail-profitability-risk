"""Step 6a: export the Power BI data model (star schema CSVs) from the SQL layer and the model outputs.

Design: facts stay at their natural grain (order item, order); every dimension has a unique key;
P2 profit is NOT exported - it is a DAX measure driven by a COGS what-if parameter, so the sensitivity
analysis is interactive. Model outputs (forecast, seller watchlist, scorecard) come from reports/.

Usage (from project root):  python -m olist_risk.export_powerbi   ->  powerbi/model/*.csv
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from olist_risk.build_db import DEFAULT_DB, ROOT
from olist_risk.data import query

log = logging.getLogger("olist_risk.export_powerbi")
REPORTS = ROOT / "reports"
OUT_DIR = ROOT / "powerbi" / "model"

DATE_START, DATE_END = "2017-01-01", "2018-12-31"     # covers the Sep-Nov 2018 forecast horizon
FULL_MONTHS = ("2017-01-01", "2018-08-31")              # D2 analysis window
LATE_MODEL_WINDOW = ("2017-01-01", "2018-05-31")        # D6

STATES = {  # code -> (name, macro-region)
    "AC": ("Acre", "North"), "AL": ("Alagoas", "Northeast"), "AP": ("Amapa", "North"), "AM": ("Amazonas", "North"),
    "BA": ("Bahia", "Northeast"), "CE": ("Ceara", "Northeast"), "DF": ("Distrito Federal", "Center-West"),
    "ES": ("Espirito Santo", "Southeast"), "GO": ("Goias", "Center-West"), "MA": ("Maranhao", "Northeast"),
    "MT": ("Mato Grosso", "Center-West"), "MS": ("Mato Grosso do Sul", "Center-West"), "MG": ("Minas Gerais", "Southeast"),
    "PA": ("Para", "North"), "PB": ("Paraiba", "Northeast"), "PR": ("Parana", "South"), "PE": ("Pernambuco", "Northeast"),
    "PI": ("Piaui", "Northeast"), "RJ": ("Rio de Janeiro", "Southeast"), "RN": ("Rio Grande do Norte", "Northeast"),
    "RS": ("Rio Grande do Sul", "South"), "RO": ("Rondonia", "North"), "RR": ("Roraima", "North"),
    "SC": ("Santa Catarina", "South"), "SP": ("Sao Paulo", "Southeast"), "SE": ("Sergipe", "Northeast"),
    "TO": ("Tocantins", "North"),
}


# --------------------------------------------------------------------------- dimensions
def build_dim_date() -> pd.DataFrame:
    d = pd.DataFrame({"date": pd.date_range(DATE_START, DATE_END, freq="D")})
    d["year"], d["month_num"], d["quarter"] = d["date"].dt.year, d["date"].dt.month, d["date"].dt.quarter
    d["year_quarter"] = d["year"].astype(str) + "-Q" + d["quarter"].astype(str)
    d["month_start"] = d["date"].dt.to_period("M").dt.to_timestamp()
    d["year_month"] = d["date"].dt.strftime("%Y-%m")
    d["month_name"] = d["date"].dt.strftime("%b")
    d["is_full_month"] = d["date"].between(*map(pd.Timestamp, FULL_MONTHS)).astype(int)
    d["in_late_model_window"] = d["date"].between(*map(pd.Timestamp, LATE_MODEL_WINDOW)).astype(int)
    d["period"] = np.where(d["date"] >= pd.Timestamp("2018-06-01"), "Jun-Aug 2018 (regime shift)", "Before Jun 2018")
    d["date"] = d["date"].dt.strftime("%Y-%m-%d")
    d["month_start"] = pd.to_datetime(d["month_start"]).dt.strftime("%Y-%m-%d")
    return d


def build_dim_category(db: Path) -> pd.DataFrame:
    return query("SELECT DISTINCT category_en FROM dim_product ORDER BY category_en", db)


def build_dim_seller(db: Path) -> pd.DataFrame:
    """Seller attributes plus a revenue tier from the trailing 6 months (a slicer for concentration analysis)."""
    s = query("SELECT seller_id, city AS seller_city, state AS seller_state FROM dim_seller", db)
    rev = query("""SELECT seller_id, SUM(price) AS rev FROM fact_sales
                   WHERE purchase_month IN (SELECT purchase_month FROM v_kpi_monthly ORDER BY purchase_month DESC LIMIT 6)
                   GROUP BY seller_id""", db).sort_values("rev", ascending=False).reset_index(drop=True)
    n = len(rev)
    rank = pd.Series(np.arange(1, n + 1), index=rev["seller_id"])
    tier = pd.Series(np.select([rank <= 10, rank <= 0.05 * n, rank <= 0.20 * n], ["Top 10", "Top 5%", "Top 20%"], "Long tail"),
                     index=rev["seller_id"])
    s["revenue_tier"] = s["seller_id"].map(tier).fillna("No sales in last 6 months")
    return s


def build_dim_customer_state() -> pd.DataFrame:
    return pd.DataFrame([{"state": k, "state_name": v[0], "region": v[1]} for k, v in STATES.items()])


# --------------------------------------------------------------------------- facts
def build_fact_sales(db: Path) -> pd.DataFrame:
    return query("""
        SELECT fs.order_id, fs.order_item_id, date(o.purchase_ts) AS purchase_date, fs.seller_id, fs.category_en,
               fs.customer_state, fs.customer_unique_id, fs.price, fs.freight_value
        FROM fact_sales fs JOIN fact_orders o ON o.order_id = fs.order_id""", db)


def build_fact_orders(db: Path) -> pd.DataFrame:
    """All in-window orders (any status). Risk columns are NULL where they do not apply (e.g. no delivery yet)."""
    return query("""
        SELECT o.order_id, date(o.purchase_ts) AS purchase_date, c.state AS customer_state, o.order_status,
               o.is_delivered, o.is_failed, r.revenue, r.freight_value AS freight, o.is_late, o.days_late, o.delivery_days,
               r.review_score, r.is_low, r.at_risk,
               ROUND(julianday(o.estimated_ts) - julianday(o.purchase_ts), 2) AS promised_days,
               CASE WHEN o.is_failed = 1 THEN COALESCE(f.item_value, 0) ELSE 0 END AS failed_item_value
        FROM fact_orders o
        JOIN dim_customer c        ON c.customer_id = o.customer_id
        LEFT JOIN fact_order_risk r ON r.order_id = o.order_id
        LEFT JOIN (SELECT order_id, SUM(price) AS item_value FROM fact_order_items GROUP BY order_id) f ON f.order_id = o.order_id
        WHERE o.in_window = 1""", db)


# --------------------------------------------------------------------------- model outputs
def build_fact_forecast(db: Path, reports: Path) -> pd.DataFrame:
    actual = query("SELECT purchase_month, orders, gross_revenue AS revenue FROM v_kpi_monthly", db)
    actual = pd.DataFrame({"month_start": actual["purchase_month"] + "-01", "series": "Actual", "orders": actual["orders"].astype(float),
                           "lo80": np.nan, "hi80": np.nan, "revenue": actual["revenue"]})
    nxt = pd.read_csv(reports / "tables" / "forecast_next3.csv")
    fc = pd.DataFrame({"month_start": pd.to_datetime(nxt["month"]).dt.strftime("%Y-%m-%d"), "series": "Forecast",
                       "orders": nxt["point"], "lo80": nxt["lo80"], "hi80": nxt["hi80"], "revenue": nxt["revenue_point"]})
    res = json.loads((reports / "forecast_results.json").read_text(encoding="utf-8"))
    sc = res["november_scenario"]
    scenario = pd.DataFrame([{"month_start": "2018-11-01", "series": "Black-Friday scenario", "orders": sc["scenario_nov_2018"],
                              "lo80": np.nan, "hi80": np.nan, "revenue": sc["scenario_nov_2018"] * res["trailing6_aov"]}])
    return pd.concat([actual, fc, scenario], ignore_index=True)


def build_watchlist(reports: Path) -> pd.DataFrame:
    w = pd.read_csv(reports / "tables" / "seller_watchlist_top100.csv").sort_values("expected_profit_at_risk", ascending=False)
    w = w.drop(columns=["seller_state"], errors="ignore").reset_index(drop=True)
    w.insert(0, "rank", np.arange(1, len(w) + 1))
    return w


def build_scorecard(reports: Path) -> pd.DataFrame:
    rows: list[dict] = []

    def add(model: str, variant: str, m: dict) -> None:
        a = m["at_shares"]["0.10"]
        for metric, value in {"roc_auc": m["roc_auc"], "pr_auc": m["pr_auc"], "brier": m["brier"], "base_rate": m["base_rate"],
                              "precision_top10": a["precision_top"], "recall_top10": a["recall_top"], "lift_top10": a["lift_top"],
                              "n_test": m["n"]}.items():
            rows.append({"model": model, "variant": variant, "metric": metric, "value": float(value)})

    late = json.loads((reports / "late_model_results.json").read_text(encoding="utf-8"))
    for algo, m in late["models"].items():
        add("Late delivery", algo, m)
    review = json.loads((reports / "review_model_results.json").read_text(encoding="utf-8"))
    for feature_set, block in review["models"].items():
        for algo, m in block["metrics"].items():
            add("Low review", f"{feature_set} / {algo}", m)
    seller = json.loads((reports / "seller_model_results.json").read_text(encoding="utf-8"))
    for key, name in (("A", "Seller inactivity"), ("A_all_eligible", "Seller inactivity (all sellers, inflated)"), ("B", "Seller quality")):
        for algo, m in seller[key]["metrics"].items():
            add(name, algo, m)
    return pd.DataFrame(rows)


def build_risk_stats(reports: Path) -> pd.DataFrame:
    """Risk statistics computed in Python (volatility, drawdown, baseline), shown as cards on the risk page."""
    r = json.loads((reports / "risk_results.json").read_text(encoding="utf-8"))
    vol, dd, base = r["volatility"], r["max_drawdown"], r["baseline_monthly"]
    rows = [
        ("Std dev of monthly log growth, full window", vol["log_growth_vol_full"], "Inflated by the 2017 scale-up"),
        ("Std dev of monthly log growth, last 12 months", vol["log_growth_vol_last12"], "Includes the Nov-2017 spike"),
        ("Coefficient of variation, Jan-Aug 2018", vol["cv_2018"], "Stability of the 2018 plateau"),
        ("Max drawdown of monthly revenue", dd["depth"], f"Peak {dd['peak']} to trough {dd['trough']} (holiday reversal)"),
        ("Current drawdown from peak", r["current_drawdown"], "Aug 2018 vs the Nov-2017 peak"),
        ("Baseline monthly revenue (Jun-Aug 2018)", base["revenue"], "R$ per month, scenario baseline"),
        ("Baseline monthly freight (Jun-Aug 2018)", base["freight"], "R$ per month"),
        ("Baseline monthly profit proxy (Jun-Aug 2018)", base["p2"], "R$ per month at 65% COGS"),
    ]
    return pd.DataFrame(rows, columns=["metric", "value", "note"])


def build_tables(db: Path = DEFAULT_DB, reports: Path = REPORTS) -> dict[str, pd.DataFrame]:
    trend = pd.read_csv(reports / "tables" / "risk_concentration_trend.csv")
    trend.insert(1, "month_start", trend["purchase_month"] + "-01")
    return {
        "dim_date": build_dim_date(), "dim_category": build_dim_category(db), "dim_seller": build_dim_seller(db),
        "dim_customer_state": build_dim_customer_state(), "fact_sales": build_fact_sales(db), "fact_orders": build_fact_orders(db),
        "fact_forecast": build_fact_forecast(db, reports), "fact_seller_watchlist": build_watchlist(reports),
        "tbl_scenarios": pd.read_csv(reports / "tables" / "risk_scenarios.csv"),
        "tbl_cogs_sensitivity": pd.read_csv(reports / "tables" / "risk_cogs_sensitivity.csv"),
        "tbl_concentration_trend": trend, "tbl_model_scorecard": build_scorecard(reports),
        "tbl_risk_stats": build_risk_stats(reports),
        "tbl_forecast_scoreboard": pd.read_csv(reports / "tables" / "forecast_scoreboard.csv"),
    }


def write(tables: dict[str, pd.DataFrame], out_dir: Path = OUT_DIR) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_csv(out_dir / f"{name}.csv", index=False, encoding="utf-8-sig")  # BOM: Excel and Power BI read UTF-8 cleanly


def reconciliation_totals(tables: dict[str, pd.DataFrame]) -> dict:
    """Numbers the Power BI measures must reproduce. They feed the spec's reconciliation checklist."""
    fs, fo = tables["fact_sales"], tables["fact_orders"]
    return {"gross_revenue": round(float(fs["price"].sum()), 2), "freight": round(float(fs["freight_value"].sum()), 2),
            "delivered_orders": int(fs["order_id"].nunique()), "items": int(len(fs)),
            "customers": int(fs["customer_unique_id"].nunique()),
            "p2_profit_65pct_cogs": round(float(fs["price"].sum() * 0.35 - fs["freight_value"].sum()), 2),
            "all_orders_in_window": int(len(fo)), "failed_orders": int(fo["is_failed"].sum()),
            "at_risk_revenue": round(float(fo.loc[fo["at_risk"] == 1, "revenue"].sum()), 2),
            "late_orders": int(fo["is_late"].sum()), "late_evaluable": int(fo["is_late"].notna().sum()),
            "low_review_orders": int(fo["is_low"].sum()), "reviewed_orders": int(fo["review_score"].notna().sum()),
            "lost_revenue_failed_orders": round(float(fo["failed_item_value"].sum()), 2)}


def run() -> dict:
    tables = build_tables()
    write(tables)
    totals = reconciliation_totals(tables)
    (OUT_DIR / "reconciliation_expected.json").write_text(json.dumps(totals, indent=2), encoding="utf-8")
    for name, df in tables.items():
        log.info("%-26s %8d rows", name, len(df))
    return totals


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run()
