"""Tests for the order-of-operations in split_with_history: the leakage-critical part of the pipeline."""
import numpy as np
import pandas as pd
import pytest

from olist_risk import features as ft

T = pd.Timestamp
TRAIN_END, TEST_START, TEST_END = T("2017-12-31"), T("2018-01-01"), T("2018-03-31")
HIST_COLS = ["seller_n_prior", "seller_late_rate_prior", "seller_low_rate_prior", "route_late_rate_prior", "seller_orders_prev30d"]


@pytest.fixture
def orders() -> pd.DataFrame:
    rng = np.random.default_rng(11)
    n = 900
    minutes = np.sort(rng.integers(0, 700_000, n))          # ~16 months of minutes after 2017-01-01
    purchase = pd.Timestamp("2017-01-01") + pd.to_timedelta(minutes, unit="m")
    delivered = purchase + pd.to_timedelta(rng.integers(4, 25, n), unit="D")
    return pd.DataFrame({
        "order_id": np.arange(n), "seller_id": rng.choice(["S1", "S2", "S3"], n), "route": rng.choice(["SP>SP", "SP>RJ"], n),
        "purchase_ts": purchase, "delivered_ts": delivered, "delivery_days": (delivered - purchase) / pd.Timedelta(days=1),
        "is_late": rng.integers(0, 2, n).astype(float), "is_low": rng.integers(0, 2, n).astype(float),
        "answered_ts": delivered + pd.Timedelta(days=3), "price_total": 100.0})


def _split(df, eligible=None, known="delivered_ts"):
    eligible = df["is_late"].notna() if eligible is None else eligible
    return ft.split_with_history(df, eligible, TRAIN_END, TEST_START, TEST_END, known_col=known)


def test_priors_and_train_features_ignore_test_period_outcomes(orders):
    tr, _, pri = _split(orders)
    tampered = orders.copy()
    after = tampered["purchase_ts"] >= TEST_START
    tampered.loc[after, "is_late"] = 1.0 - tampered.loc[after, "is_late"]
    tampered.loc[after, "is_low"] = 1.0 - tampered.loc[after, "is_low"]
    tr2, _, pri2 = _split(tampered)
    assert pri == pri2                                            # priors come from the training period only
    pd.testing.assert_frame_equal(tr[HIST_COLS].reset_index(drop=True), tr2[HIST_COLS].reset_index(drop=True))


def test_embargo_drops_train_rows_whose_label_lands_after_test_start(orders):
    tr, _, pri = _split(orders)
    assert (tr["delivered_ts"] < TEST_START).all()
    in_train_window = orders[orders["purchase_ts"] < TRAIN_END + pd.Timedelta(days=1)]
    assert len(tr) == (in_train_window["delivered_ts"] < TEST_START).sum()
    assert pri["n_embargoed"] == len(in_train_window) - len(tr) > 0


def test_test_window_bounds_and_no_overlap(orders):
    tr, te, _ = _split(orders)
    assert tr["purchase_ts"].max() < te["purchase_ts"].min()
    assert te["purchase_ts"].min() >= TEST_START and te["purchase_ts"].max() < TEST_END + pd.Timedelta(days=1)


def test_ineligible_rows_still_count_toward_seller_load(orders):
    """History is built on ALL orders, so whether an order gets a label cannot influence anyone's features."""
    all_rows, _, _ = _split(orders)
    dropped = orders.copy()
    dropped.loc[dropped["order_id"] % 5 == 0, "is_late"] = np.nan   # 20% of orders lose their label
    part, _, _ = _split(dropped)
    common = part.merge(all_rows[["order_id", "seller_orders_prev30d"]], on="order_id", suffixes=("", "_full"))
    assert len(common) > 100
    assert (common["seller_orders_prev30d"] == common["seller_orders_prev30d_full"]).all()


def test_eligibility_mask_selects_examples_only(orders):
    eligible = orders["order_id"] % 2 == 0
    tr, te, _ = _split(orders, eligible=eligible)
    assert (tr["order_id"] % 2 == 0).all() and (te["order_id"] % 2 == 0).all()


def test_cancellation_seasoning_excludes_orders_inside_the_window():
    """cancel_rate only measures orders older than seasoning_days (status assumed final by then)."""
    cutoff = T("2018-03-01")
    base = {"seller_id": "S", "is_failed": 0, "review_score": 5.0, "is_late": 0.0, "delivered_ts": T("2018-01-20"),
            "answered_ts": T("2018-01-25"), "price": 100.0, "seller_state": "SP"}
    rows = [{**base, "purchase_ts": T("2018-01-10") + pd.Timedelta(days=i)} for i in range(6)]
    rows.append({**base, "purchase_ts": cutoff - pd.Timedelta(days=5), "is_failed": 1})       # inside the 14-day seasoning
    so = pd.DataFrame(rows)
    assert ft.seller_snapshot(so, cutoff, seasoning_days=14).set_index("seller_id").loc["S", "cancel_rate"] == 0.0
    assert ft.seller_snapshot(so, cutoff, seasoning_days=3).set_index("seller_id").loc["S", "cancel_rate"] == pytest.approx(1 / 7)
