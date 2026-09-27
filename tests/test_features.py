"""Leakage tests for the feature layer. The core property: a feature computed at time t must not
change if any data with a known-time >= t is altered."""
import numpy as np
import pandas as pd
import pytest

from olist_risk import features as ft

T = pd.Timestamp


# ------------------------------------------------------------------ as-of statistics
@pytest.fixture
def events() -> pd.DataFrame:
    return pd.DataFrame({
        "seller": ["A", "A", "A", "B"],
        "known": [T("2018-01-10"), T("2018-01-20"), T("2018-02-01"), T("2018-01-05")],
        "value": [1.0, 0.0, 1.0, 1.0],
    })


def _queries(times, seller="A") -> pd.DataFrame:
    return pd.DataFrame({"seller": seller, "t": [T(x) for x in times]})


def test_asof_counts_only_strictly_earlier_events(events):
    q = _queries(["2018-01-01", "2018-01-10", "2018-01-15", "2018-03-01"])
    out = ft.asof_stats(events, q, key="seller", known_col="known", value_col="value", query_col="t")
    assert out["n"].tolist() == [0, 0, 1, 3]          # an event known AT the query time is excluded
    assert out["total"].tolist() == [0.0, 0.0, 1.0, 2.0]


def test_asof_keys_do_not_leak_across_groups(events):
    out = ft.asof_stats(events, _queries(["2018-03-01"], seller="B"), "seller", "known", "value", "t")
    assert out["n"].iloc[0] == 1 and out["total"].iloc[0] == 1.0


def test_asof_unknown_key_returns_zero(events):
    out = ft.asof_stats(events, _queries(["2018-03-01"], seller="Z"), "seller", "known", "value", "t")
    assert out["n"].iloc[0] == 0


def test_asof_ignores_events_known_after_query(events):
    q = _queries(["2018-01-25"])
    base = ft.asof_stats(events, q, "seller", "known", "value", "t")
    tampered = events.copy()
    tampered.loc[tampered["known"] > T("2018-01-25"), "value"] = 999.0   # only future events change
    assert ft.asof_stats(tampered, q, "seller", "known", "value", "t").equals(base)


def test_asof_preserves_query_index(events):
    q = _queries(["2018-03-01", "2018-01-01"])
    q.index = [50, 7]
    assert ft.asof_stats(events, q, "seller", "known", "value", "t").index.tolist() == [50, 7]


def test_smoothed_rate_shrinks_to_prior_when_no_history():
    assert ft.smoothed_rate(pd.Series([0.0]), pd.Series([0.0]), prior=0.07, strength=20).iloc[0] == pytest.approx(0.07)
    # with lots of history it converges to the empirical rate
    assert ft.smoothed_rate(pd.Series([10_000.0]), pd.Series([1_000.0]), 0.07, 20).iloc[0] == pytest.approx(0.1, abs=0.001)


def test_haversine_known_distance():
    # Sao Paulo -> Rio de Janeiro is roughly 360 km
    d = ft.haversine_km(np.array([-23.55]), np.array([-46.63]), np.array([-22.91]), np.array([-43.17]))
    assert 340 < d[0] < 380


# ------------------------------------------------------------------ seller snapshots
@pytest.fixture
def seller_orders() -> pd.DataFrame:
    rows = []
    def add(seller, ts, failed=0, review=5.0, late=0.0, price=100.0):
        rows.append({"seller_id": seller, "purchase_ts": T(ts), "is_failed": failed, "review_score": review,
                     "is_late": late, "delivered_ts": T(ts) + pd.Timedelta(days=8), "answered_ts": T(ts) + pd.Timedelta(days=10),
                     "price": price, "seller_state": "SP"})
    for d in range(6):                                   # S1: 6 orders before cutoff, silent afterwards (definition A)
        add("S1", f"2018-01-{10 + d}")
    for d in range(6):                                   # S2: 6 before, 6 after with bad reviews (definition B)
        add("S2", f"2018-01-{10 + d}")
        add("S2", f"2018-04-{10 + d}", review=1.0)
    for d in range(3):                                   # S3: too little history -> ineligible
        add("S3", f"2018-01-{10 + d}")
    for d in range(6):                                   # S4: healthy
        add("S4", f"2018-01-{10 + d}")
        add("S4", f"2018-04-{10 + d}", review=5.0)
    return pd.DataFrame(rows)


CUTOFF = T("2018-03-01")


def _snap(so, **kw):
    return ft.seller_snapshot(so, CUTOFF, horizon_days=90, min_orders=5, min_label_orders=5,
                              cancel_threshold=0.10, review_threshold=3.0, **kw).set_index("seller_id")


def test_snapshot_eligibility_needs_history(seller_orders):
    snap = _snap(seller_orders)
    assert "S3" not in snap.index and {"S1", "S2", "S4"} <= set(snap.index)


def test_definition_a_flags_inactive_sellers(seller_orders):
    snap = _snap(seller_orders)
    assert snap.loc["S1", "fail_a"] == 1 and snap.loc["S2", "fail_a"] == 0 and snap.loc["S4", "fail_a"] == 0


def test_definition_b_flags_quality_and_leaves_inactive_undefined(seller_orders):
    snap = _snap(seller_orders)
    assert snap.loc["S2", "fail_b"] == 1 and snap.loc["S4", "fail_b"] == 0
    assert pd.isna(snap.loc["S1", "fail_b"])             # no label-window orders -> B undefined, not 0


def test_snapshot_features_ignore_post_cutoff_data(seller_orders):
    base = _snap(seller_orders)
    tampered = seller_orders.copy()
    after = tampered["purchase_ts"] >= CUTOFF
    tampered.loc[after, ["price", "review_score", "is_failed"]] = [9999.0, 1.0, 1]
    tampered = pd.concat([tampered, tampered[after].assign(seller_id="S9")])   # brand-new post-cutoff seller
    new = _snap(tampered)
    feature_cols = [c for c in base.columns if c not in ("fail_a", "fail_b", "n_orders_label")]
    pd.testing.assert_frame_equal(base[feature_cols], new.loc[base.index, feature_cols])
    assert "S9" not in new.index


def test_quality_features_respect_outcome_known_time(seller_orders):
    """A review answered after the cutoff cannot be part of avg_review at the cutoff."""
    so = seller_orders.copy()
    late_review = (so["seller_id"] == "S4") & (so["purchase_ts"] == T("2018-01-15"))
    so.loc[late_review, "answered_ts"] = T("2018-06-01")     # answered long after the cutoff
    so.loc[late_review, "review_score"] = 1.0
    snap = _snap(so)
    assert snap.loc["S4", "avg_review"] == pytest.approx(5.0)   # the 1-star review is invisible at cutoff


# ------------------------------------------------------------------ splits
def test_time_split_never_overlaps():
    df = pd.DataFrame({"purchase_ts": pd.date_range("2017-01-01", periods=600, freq="D")})
    tr, te = ft.time_split(df, "purchase_ts", train_end=T("2018-03-31"), test_start=T("2018-04-01"), test_end=T("2018-05-31"))
    assert tr["purchase_ts"].max() < te["purchase_ts"].min()
    assert te["purchase_ts"].max() <= T("2018-05-31 23:59:59")


# ------------------------------------------------------------------ integration (real data)
@pytest.fixture(scope="module")
def orders_with_history(built_db):
    df = ft.load_orders(built_db)
    return ft.add_history_features(df, late_prior=0.07, low_prior=0.13)


def test_order_frame_is_one_row_per_order_with_expected_target_rate(built_db):
    from olist_risk.data import query
    df = ft.load_orders(built_db)
    assert df["order_id"].is_unique
    sql = query("SELECT COUNT(*) AS n, AVG(is_late) AS late FROM fact_order_risk WHERE is_late IS NOT NULL", built_db).iloc[0]
    assert df["is_late"].notna().sum() == sql["n"] and df["is_late"].mean() == pytest.approx(sql["late"])


def test_asof_history_matches_brute_force_on_a_sample(orders_with_history):
    df = orders_with_history
    sample = df.sample(60, random_state=7)
    for row in sample.itertuples():
        prior = df[(df["seller_id"] == row.seller_id) & (df["delivered_ts"] < row.purchase_ts) & df["is_late"].notna()]
        assert row.seller_n_prior == len(prior)
        expected = (prior["is_late"].sum() + 0.07 * ft.PRIOR_STRENGTH) / (len(prior) + ft.PRIOR_STRENGTH)
        assert row.seller_late_rate_prior == pytest.approx(expected)


def test_history_never_counts_the_order_itself(orders_with_history):
    """An order is delivered after it is purchased, so it can never be in its own history."""
    df = orders_with_history
    known = df.dropna(subset=["delivered_ts"])          # 8 'delivered' orders carry no delivery timestamp
    assert (known["delivered_ts"] > known["purchase_ts"]).all()
    first = df.groupby("seller_id").head(1)
    assert (first["seller_n_prior"] == 0).all()


def test_seller_load_feature_counts_recent_purchases(orders_with_history):
    df = orders_with_history
    row = df[df["seller_orders_prev30d"] > 3].iloc[0]
    window = df[(df["seller_id"] == row["seller_id"]) & (df["purchase_ts"] < row["purchase_ts"])
                & (df["purchase_ts"] >= row["purchase_ts"] - pd.Timedelta(days=30))]
    assert row["seller_orders_prev30d"] == len(window)
