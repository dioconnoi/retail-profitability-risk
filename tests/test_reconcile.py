"""Reconciliation tests: SQL views vs an independent pandas computation from the raw CSVs.

If SQL and pandas disagree, one of them has a join or filter bug. These tests are the
evidence that the KPI layer can be trusted.
"""
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from olist_risk.build_db import RAW_DIR, build  # missing raw data fails loudly, never skips

WINDOW_START, WINDOW_END = "2017-01-01", "2018-08-31"
COGS = 0.65

@pytest.fixture(scope="module")
def db(tmp_path_factory) -> sqlite3.Connection:
    path = build(tmp_path_factory.mktemp("db") / "olist_test.db")
    conn = sqlite3.connect(path)
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def raw() -> dict[str, pd.DataFrame]:
    def r(name, **kw):
        return pd.read_csv(RAW_DIR / name, **kw)

    return {
        "orders": r("olist_orders_dataset.csv", parse_dates=[
            "order_purchase_timestamp", "order_delivered_customer_date",
            "order_estimated_delivery_date"]),
        "items": r("olist_order_items_dataset.csv"),
        "reviews": r("olist_order_reviews_dataset.csv",
                     parse_dates=["review_answer_timestamp"]),
        "customers": r("olist_customers_dataset.csv"),
    }


@pytest.fixture(scope="module")
def delivered_items(raw) -> pd.DataFrame:
    """Independent definition of the revenue grain: delivered, in-window items."""
    o = raw["orders"]
    day = o["order_purchase_timestamp"].dt.strftime("%Y-%m-%d")
    keep = o[(o["order_status"] == "delivered") & day.between(WINDOW_START, WINDOW_END)]
    return raw["items"].merge(keep[["order_id", "customer_id"]], on="order_id")


def q(db, sql):
    return pd.read_sql_query(sql, db)


def test_gross_revenue_matches_pandas(db, delivered_items):
    sql_total = q(db, "SELECT SUM(gross_revenue) AS v FROM v_kpi_monthly")["v"][0]
    assert sql_total == pytest.approx(delivered_items["price"].sum(), abs=1.0)


def test_p1_and_p2_match_pandas(db, delivered_items):
    p1 = (delivered_items["price"] - delivered_items["freight_value"]).sum()
    p2 = (delivered_items["price"] * (1 - COGS) - delivered_items["freight_value"]).sum()
    row = q(db, "SELECT p1_net_of_freight AS p1, p2_assumed_margin AS p2 FROM v_kpi_summary").iloc[0]
    assert row["p1"] == pytest.approx(p1, abs=1.0)
    assert row["p2"] == pytest.approx(p2, abs=1.0)


def test_order_count_matches_pandas(db, delivered_items):
    n = q(db, "SELECT orders AS v FROM v_kpi_summary")["v"][0]
    assert n == delivered_items["order_id"].nunique()


@pytest.mark.parametrize("view", ["v_category_perf", "v_seller_perf"])
def test_breakdowns_sum_to_total(db, view):
    total = q(db, "SELECT gross_revenue AS v FROM v_kpi_summary")["v"][0]
    parts = q(db, f"SELECT SUM(gross_revenue) AS v FROM {view}")["v"][0]
    assert parts == pytest.approx(total, abs=1.0)


@pytest.mark.parametrize("lens", ["customer", "seller"])
def test_state_lens_sums_to_total(db, lens):
    total = q(db, "SELECT gross_revenue AS v FROM v_kpi_summary")["v"][0]
    parts = q(db, f"SELECT SUM(gross_revenue) AS v FROM v_state_perf WHERE lens='{lens}'")["v"][0]
    assert parts == pytest.approx(total, abs=1.0)


def test_category_shares_sum_to_one_and_cum_share_ends_at_one(db):
    c = q(db, "SELECT revenue_share, cum_share FROM v_category_perf")
    assert c["revenue_share"].sum() == pytest.approx(1.0, abs=1e-3)
    assert c["cum_share"].max() == pytest.approx(1.0, abs=1e-3)


@pytest.mark.parametrize("lens", ["customer", "seller"])
def test_state_shares_sum_to_one_per_lens(db, lens):
    s = q(db, f"SELECT SUM(revenue_share) AS v FROM v_state_perf WHERE lens='{lens}'")["v"][0]
    assert s == pytest.approx(1.0, abs=1e-3)


def test_hhi_bounds_and_effective_n(db):
    c = q(db, "SELECT * FROM v_concentration")
    assert ((c["hhi"] > 0) & (c["hhi"] <= 10000)).all()
    # HHI x effective_N = 10,000 by definition (tolerance covers 1-decimal rounding)
    assert ((c["hhi"] * c["effective_n"] / 10000 - 1).abs() < 0.01).all()
    assert (c["top10_share"] <= 1).all()


def test_reviews_deduplicated_to_one_per_order(db, raw):
    n_sql = q(db, "SELECT COUNT(*) AS v FROM fact_reviews")["v"][0]
    assert n_sql == raw["reviews"]["order_id"].nunique()
    assert q(db, "SELECT COUNT(*) - COUNT(DISTINCT order_id) AS v FROM fact_reviews")["v"][0] == 0


def test_late_rate_matches_pandas_date_level(db, raw):
    o = raw["orders"]
    day = o["order_purchase_timestamp"].dt.strftime("%Y-%m-%d")
    d = o[(o["order_status"] == "delivered") & day.between(WINDOW_START, WINDOW_END)
          & o["order_delivered_customer_date"].notna()]
    late = d["order_delivered_customer_date"].dt.normalize() > d["order_estimated_delivery_date"].dt.normalize()
    sql = q(db, "SELECT SUM(is_late) AS late, COUNT(is_late) AS n FROM fact_orders "
                "WHERE in_window=1 AND is_delivered=1")
    assert sql["n"][0] == len(d)
    assert sql["late"][0] == late.sum()


def test_at_risk_revenue_is_union_not_sum(db):
    r = q(db, "SELECT SUM(late_revenue) AS l, SUM(low_review_revenue) AS w, "
              "SUM(at_risk_revenue) AS u FROM v_risk_monthly").iloc[0]
    assert max(r["l"], r["w"]) <= r["u"] < r["l"] + r["w"]  # strict: overlap must be removed


def test_seller_revenue_not_fanned_out(db):
    """A join fan-out would inflate revenue; seller table must equal item-level revenue."""
    total = q(db, "SELECT SUM(price) AS v FROM fact_sales")["v"][0]
    sellers = q(db, "SELECT SUM(gross_revenue) AS v FROM v_seller_perf")["v"][0]
    assert sellers == pytest.approx(total, abs=1.0)


def test_window_excludes_sparse_months(db):
    months = q(db, "SELECT purchase_month FROM v_kpi_monthly")["purchase_month"]
    assert months.min() == "2017-01" and months.max() == "2018-08" and len(months) == 20


def test_latest_review_wins(db, raw):
    r = raw["reviews"].sort_values(["order_id", "review_answer_timestamp", "review_id"])
    expected = r.groupby("order_id").tail(1).set_index("order_id")["review_id"]
    got = q(db, "SELECT order_id, review_id FROM fact_reviews").set_index("order_id")["review_id"]
    multi = raw["reviews"].groupby("order_id").size()
    multi = multi[multi > 1].index
    assert (got.loc[multi] == expected.loc[multi]).mean() > 0.99  # ties on timestamp are rare


def test_at_risk_union_matches_pandas(db, raw):
    o = raw["orders"]
    day = o["order_purchase_timestamp"].dt.strftime("%Y-%m-%d")
    d = o[(o["order_status"] == "delivered") & day.between(WINDOW_START, WINDOW_END)].copy()
    d["late"] = (d["order_delivered_customer_date"].dt.normalize()
                 > d["order_estimated_delivery_date"].dt.normalize()).fillna(False)
    rev = raw["items"].groupby("order_id")["price"].sum()
    latest = raw["reviews"].sort_values(["order_id", "review_answer_timestamp", "review_id"]) \
        .groupby("order_id").tail(1).set_index("order_id")["review_score"]
    d = d.join(rev.rename("rev"), on="order_id").dropna(subset=["rev"])
    d["low"] = d["order_id"].map(latest).le(2)
    expected = d.loc[d["late"] | d["low"], "rev"].sum()
    got = q(db, "SELECT SUM(at_risk_revenue) AS v FROM v_risk_monthly")["v"][0]
    assert got == pytest.approx(expected, rel=1e-4)


def test_monthly_cancellation_rate_matches_pandas(db, raw):
    o = raw["orders"]
    day = o["order_purchase_timestamp"].dt.strftime("%Y-%m-%d")
    w = o[day.between(WINDOW_START, WINDOW_END)]
    expected = w["order_status"].isin(["canceled", "unavailable"]).mean()
    v = q(db, "SELECT SUM(failed_orders) AS f, SUM(all_orders) AS a FROM v_risk_monthly").iloc[0]
    assert v["f"] / v["a"] == pytest.approx(expected, abs=1e-6)


def test_seller_rates_are_bounded_and_reconcile(db):
    s = q(db, "SELECT * FROM v_seller_perf")
    for col in ("cancellation_rate", "late_rate", "low_review_rate"):
        assert s[col].dropna().between(0, 1).all()
    assert s["orders_delivered"].sum() >= q(db, "SELECT orders AS v FROM v_kpi_summary")["v"][0]  # multi-seller orders count per seller


def test_censoring_adjusted_late_rate_is_higher(db):
    r = q(db, "SELECT * FROM v_late_rates").iloc[0]
    assert r["overdue_open_orders"] > 1000
    assert r["late_rate_censoring_adjusted"] > r["late_rate_delivered"]


def test_moving_average_null_until_three_months(db):
    m = q(db, "SELECT purchase_month, revenue_ma3 FROM v_kpi_monthly ORDER BY purchase_month")
    assert m["revenue_ma3"].iloc[:2].isna().all() and m["revenue_ma3"].iloc[2:].notna().all()
