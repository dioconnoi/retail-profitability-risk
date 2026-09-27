"""The Power BI export must be a valid star schema that reconciles to the SQL layer."""
import pandas as pd
import pytest

from olist_risk import export_powerbi as ex
from olist_risk.data import query


@pytest.fixture(scope="module")
def tables(built_db):
    return ex.build_tables(built_db)


def test_all_expected_tables_present(tables):
    expected = {"dim_date", "dim_category", "dim_seller", "dim_customer_state", "fact_sales", "fact_orders",
                "fact_forecast", "fact_seller_watchlist", "tbl_scenarios", "tbl_cogs_sensitivity",
                "tbl_concentration_trend", "tbl_model_scorecard", "tbl_risk_stats", "tbl_forecast_scoreboard"}
    assert expected <= set(tables)


@pytest.mark.parametrize("table,key", [("dim_date", "date"), ("dim_category", "category_en"),
                                       ("dim_seller", "seller_id"), ("dim_customer_state", "state")])
def test_dimension_keys_are_unique_and_not_null(tables, table, key):
    col = tables[table][key]
    assert col.notna().all() and col.is_unique, table


def test_date_dimension_is_contiguous_and_covers_forecast_horizon(tables):
    d = pd.to_datetime(tables["dim_date"]["date"])
    assert d.min() == pd.Timestamp("2017-01-01") and d.max() >= pd.Timestamp("2018-11-30")
    assert (d.diff().dropna() == pd.Timedelta(days=1)).all()


def test_fact_sales_reconciles_to_sql_layer(tables, built_db):
    sql = query("SELECT SUM(price) AS rev, SUM(freight_value) AS frt, COUNT(*) AS n FROM fact_sales", built_db).iloc[0]
    f = tables["fact_sales"]
    assert f["price"].sum() == pytest.approx(sql["rev"]) and f["freight_value"].sum() == pytest.approx(sql["frt"])
    assert len(f) == sql["n"]


@pytest.mark.parametrize("fk,dim,key", [("purchase_date", "dim_date", "date"), ("category_en", "dim_category", "category_en"),
                                        ("seller_id", "dim_seller", "seller_id"), ("customer_state", "dim_customer_state", "state")])
def test_fact_sales_referential_integrity(tables, fk, dim, key):
    assert tables["fact_sales"][fk].isin(tables[dim][key]).all(), fk


@pytest.mark.parametrize("fk,dim,key", [("purchase_date", "dim_date", "date"), ("customer_state", "dim_customer_state", "state")])
def test_fact_orders_referential_integrity(tables, fk, dim, key):
    assert tables["fact_orders"][fk].isin(tables[dim][key]).all(), fk


def test_fact_orders_grain_and_reconciliation(tables, built_db):
    o = tables["fact_orders"]
    assert o["order_id"].is_unique
    sql = query("SELECT orders FROM v_kpi_summary", built_db)["orders"][0]
    assert o["revenue"].notna().sum() == sql                                  # delivered orders with items
    risk = query("SELECT SUM(at_risk_revenue) AS v FROM v_risk_monthly", built_db)["v"][0]
    assert o.loc[o["at_risk"] == 1, "revenue"].sum() == pytest.approx(risk, abs=1.0)
    lost = query("SELECT SUM(lost_revenue) AS v FROM v_risk_monthly", built_db)["v"][0]
    assert o["failed_item_value"].sum() == pytest.approx(lost, abs=1.0)
    assert len(o) == query("SELECT COUNT(*) AS n FROM fact_orders WHERE in_window = 1", built_db)["n"][0]


def test_region_mapping_covers_every_state_in_the_data(tables):
    states = set(tables["fact_sales"]["customer_state"]) | set(tables["fact_orders"]["customer_state"])
    assert states <= set(tables["dim_customer_state"]["state"])
    assert tables["dim_customer_state"]["region"].notna().all()


def test_forecast_table_shape_and_intervals(tables):
    f = tables["fact_forecast"]
    assert (f["series"] == "Actual").sum() == 20 and (f["series"] == "Forecast").sum() == 3
    assert (f["series"] == "Black-Friday scenario").sum() == 1
    fc = f[f["series"] == "Forecast"]
    assert ((fc["lo80"] < fc["orders"]) & (fc["orders"] < fc["hi80"])).all()


def test_watchlist_is_active_sellers_ranked_and_joinable(tables):
    w = tables["fact_seller_watchlist"]
    assert w["seller_id"].isin(tables["dim_seller"]["seller_id"]).all()
    assert (w["n_orders_90d"] >= 1).all()
    assert w["expected_profit_at_risk"].is_monotonic_decreasing and w["rank"].tolist() == list(range(1, len(w) + 1))


def test_scorecard_covers_all_three_models(tables):
    s = tables["tbl_model_scorecard"]
    assert {"Late delivery", "Low review", "Seller inactivity", "Seller quality"} <= set(s["model"])
    assert {"roc_auc", "pr_auc", "base_rate", "precision_top10", "lift_top10"} <= set(s["metric"])
    assert s["value"].notna().all()


def test_forecast_scoreboard_lists_the_six_methods(tables):
    s = tables["tbl_forecast_scoreboard"]
    assert {"naive", "ma3", "drift6", "holt_damped", "snaive", "combo"} == set(s["model"]) and s["wape"].between(0, 1).all()


def test_promised_days_present_for_every_order(tables):
    o = tables["fact_orders"]
    assert o["promised_days"].notna().all() and (o["promised_days"] > 0).all()


def test_risk_stats_table_has_volatility_and_drawdown(tables):
    r = tables["tbl_risk_stats"]
    assert len(r) == 8 and r["value"].notna().all()
    assert (r.loc[r["metric"].str.contains("drawdown"), "value"] <= 0).all()


def test_csv_round_trip_preserves_row_counts(tables, tmp_path):
    ex.write(tables, tmp_path)
    for name, df in tables.items():
        assert len(pd.read_csv(tmp_path / f"{name}.csv")) == len(df), name
