"""Feature engineering for the three risk models, built to be time-causal.

Leakage rules enforced here (and unit-tested in tests/test_features.py):
  1. History features use only events whose outcome was KNOWN strictly before the prediction time
     (a late flag is known at delivery, a review score at its answer timestamp).
  2. Seller snapshots use only orders purchased before the cutoff; labels use the window after it.
  3. Splits are chronological; priors for smoothing come from the training period only.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from olist_risk.build_db import DEFAULT_DB
from olist_risk.data import query

PRIOR_STRENGTH = 20  # pseudo-observations pulling a thin history toward the global rate

APPROVAL_NUMERIC = [
    "lead_days_est", "approval_delay_h", "n_items", "n_sellers", "n_products", "price_total",
    "freight_total", "freight_ratio", "weight_kg", "volume_l", "dist_km", "same_state",
    "purchase_dow", "purchase_hour",
    "seller_n_prior", "seller_late_rate_prior", "seller_low_rate_prior", "seller_avg_days_prior",
    "seller_orders_prev30d", "route_n_prior", "route_late_rate_prior",
]
APPROVAL_CATEGORICAL = ["category_en", "seller_state", "customer_state"]
DELIVERY_NUMERIC = ["delivery_days", "days_late", "is_late_known", "delivery_vs_est_ratio"]  # model (b), 'at delivery'


# --------------------------------------------------------------------------- generic helpers
def haversine_km(lat1: np.ndarray, lon1: np.ndarray, lat2: np.ndarray, lon2: np.ndarray) -> np.ndarray:
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


def smoothed_rate(n: pd.Series, total: pd.Series, prior: float, strength: float = PRIOR_STRENGTH) -> pd.Series:
    """Bayesian shrinkage: (successes + prior*strength) / (trials + strength)."""
    return (total + prior * strength) / (n + strength)


def asof_stats(events: pd.DataFrame, queries: pd.DataFrame, key: str, known_col: str,
               value_col: str, query_col: str) -> pd.DataFrame:
    """For every query row: count and sum of the same-key events whose `known_col` is STRICTLY
    earlier than the row's `query_col`. Strictness keeps an outcome that lands exactly at
    prediction time out of the feature. Returns columns n, total on the queries' index."""
    ev = events.dropna(subset=[known_col]).sort_values(known_col)
    stores = {k: (g[known_col].to_numpy("datetime64[ns]").astype("int64"), np.cumsum(g[value_col].to_numpy(float)))
              for k, g in ev.groupby(key, sort=False)}
    n = pd.Series(0, index=queries.index, dtype="int64")
    total = pd.Series(0.0, index=queries.index)
    for k, grp in queries.groupby(key, sort=False):
        if k not in stores:
            continue
        times, cum = stores[k]
        pos = np.searchsorted(times, grp[query_col].to_numpy("datetime64[ns]").astype("int64"), side="left")
        n.loc[grp.index] = pos
        total.loc[grp.index] = np.where(pos > 0, cum[np.maximum(pos - 1, 0)], 0.0)
    return pd.DataFrame({"n": n, "total": total})


def time_split(df: pd.DataFrame, col: str, train_end: pd.Timestamp, test_start: pd.Timestamp,
               test_end: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Chronological split on whole calendar days: train <= train_end, test in [test_start, test_end]."""
    day = pd.Timedelta(days=1)
    train = df[df[col] < train_end + day]
    test = df[(df[col] >= test_start) & (df[col] < test_end + day)]
    return train, test


# --------------------------------------------------------------------------- order-level frame
_ORDER_SQL = """
SELECT o.order_id, o.purchase_ts, o.approved_ts, o.delivered_ts, o.estimated_ts, o.purchase_month,
       o.is_late, o.days_late, o.delivery_days,
       c.state AS customer_state, c.zip_prefix AS customer_zip,
       r.review_score, r.is_low, r.answered_ts
FROM fact_orders o
JOIN dim_customer c      ON c.customer_id = o.customer_id
LEFT JOIN fact_reviews r ON r.order_id    = o.order_id
WHERE o.in_window = 1 AND o.is_delivered = 1 AND o.has_items = 1"""

_ITEM_SQL = """
SELECT i.order_id, i.seller_id, i.product_id, i.price, i.freight_value, p.category_en, p.weight_g,
       p.length_cm * p.height_cm * p.width_cm AS volume_cm3,
       s.state AS seller_state, s.zip_prefix AS seller_zip
FROM fact_order_items i
JOIN fact_orders  o ON o.order_id  = i.order_id
JOIN dim_product  p ON p.product_id = i.product_id
JOIN dim_seller   s ON s.seller_id  = i.seller_id
WHERE o.in_window = 1 AND o.is_delivered = 1"""

_TS_COLS = ["purchase_ts", "approved_ts", "delivered_ts", "estimated_ts", "answered_ts"]


def load_orders(db_path: Path = DEFAULT_DB) -> pd.DataFrame:
    """One row per delivered in-window order with items: static (approval-time) features, the
    delivery outcome columns, and the labels. History features are added separately."""
    orders = query(_ORDER_SQL, db_path)
    orders[_TS_COLS] = orders[_TS_COLS].apply(pd.to_datetime)
    items = query(_ITEM_SQL, db_path)
    geo = query("SELECT zip_prefix, lat, lng FROM geo_zip", db_path).set_index("zip_prefix")

    agg = items.groupby("order_id").agg(
        n_items=("product_id", "size"), n_sellers=("seller_id", "nunique"), n_products=("product_id", "nunique"),
        price_total=("price", "sum"), freight_total=("freight_value", "sum"),
        weight_kg=("weight_g", lambda w: w.sum() / 1000), volume_l=("volume_cm3", lambda v: v.max() / 1000))
    primary = (items.sort_values(["order_id", "price", "seller_id"], ascending=[True, False, True])
               .drop_duplicates("order_id")[["order_id", "seller_id", "category_en", "seller_state", "seller_zip"]])
    df = orders.merge(agg, on="order_id").merge(primary, on="order_id")

    s_geo, c_geo = geo.reindex(df["seller_zip"]), geo.reindex(df["customer_zip"])
    df["dist_km"] = haversine_km(s_geo["lat"].to_numpy(), s_geo["lng"].to_numpy(), c_geo["lat"].to_numpy(), c_geo["lng"].to_numpy())
    day = pd.Timedelta(days=1)
    df["lead_days_est"] = (df["estimated_ts"] - df["purchase_ts"]) / day
    df["approval_delay_h"] = (df["approved_ts"] - df["purchase_ts"]) / pd.Timedelta(hours=1)
    df["freight_ratio"] = df["freight_total"] / df["price_total"]
    df["same_state"] = (df["seller_state"] == df["customer_state"]).astype(int)
    df["purchase_dow"], df["purchase_hour"] = df["purchase_ts"].dt.dayofweek, df["purchase_ts"].dt.hour
    df["route"] = df["seller_state"] + ">" + df["customer_state"]
    # delivery-outcome features (model (b) 'at delivery' set only)
    df["is_late_known"] = df["is_late"]
    df["delivery_vs_est_ratio"] = df["delivery_days"] / df["lead_days_est"]
    return df.sort_values("purchase_ts").reset_index(drop=True)


def add_history_features(df: pd.DataFrame, late_prior: float, low_prior: float) -> pd.DataFrame:
    """Add as-of seller and route history. `late_prior` / `low_prior` must come from the TRAINING
    period only. Every statistic counts only outcomes known strictly before the order's purchase."""
    out = df.copy()
    late_ev = out.dropna(subset=["delivered_ts", "is_late"])
    seller_late = asof_stats(late_ev, out, "seller_id", "delivered_ts", "is_late", "purchase_ts")
    seller_days = asof_stats(late_ev, out, "seller_id", "delivered_ts", "delivery_days", "purchase_ts")
    route_late = asof_stats(late_ev, out, "route", "delivered_ts", "is_late", "purchase_ts")
    seller_low = asof_stats(out.dropna(subset=["answered_ts", "is_low"]), out, "seller_id", "answered_ts", "is_low", "purchase_ts")

    out["seller_n_prior"] = seller_late["n"]
    out["seller_late_rate_prior"] = smoothed_rate(seller_late["n"], seller_late["total"], late_prior)
    out["seller_low_rate_prior"] = smoothed_rate(seller_low["n"], seller_low["total"], low_prior)
    out["seller_avg_days_prior"] = (seller_days["total"] / seller_days["n"]).where(seller_days["n"] > 0)
    out["route_n_prior"] = route_late["n"]
    out["route_late_rate_prior"] = smoothed_rate(route_late["n"], route_late["total"], late_prior)

    # seller load: purchases in the 30 days before this order (purchase time is known at purchase)
    marks = out.assign(one=1.0)
    now = asof_stats(marks, out, "seller_id", "purchase_ts", "one", "purchase_ts")["n"]
    back = out.assign(back_ts=out["purchase_ts"] - pd.Timedelta(days=30))
    then = asof_stats(marks, back, "seller_id", "purchase_ts", "one", "back_ts")["n"]
    out["seller_orders_prev30d"] = now - then
    return out


def split_with_history(df: pd.DataFrame, eligible: pd.Series, train_end: pd.Timestamp, test_start: pd.Timestamp,
                       test_end: pd.Timestamp, known_col: str) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """The leakage-critical order of operations, in one tested place:
      1. Priors come from eligible TRAIN rows only, after the embargo below.
      2. History features are built on ALL orders (not only eligible ones), so whether an order gets a label
         can never influence anyone's features.
      3. Only then are examples filtered to `eligible` and split chronologically.
      4. Embargo: train rows whose label became known (`known_col`) on/after `test_start` are dropped, because a
         model trained the day before the test period could not have seen those labels."""
    day = pd.Timedelta(days=1)
    in_train = eligible & (df["purchase_ts"] < train_end + day) & (df[known_col] < test_start)
    priors = {"late_prior": float(df.loc[in_train, "is_late"].mean()),
              "low_prior": float(df.loc[in_train, "is_low"].dropna().mean())}
    full = add_history_features(df, priors["late_prior"], priors["low_prior"])
    train, test = time_split(full[eligible], "purchase_ts", train_end, test_start, test_end)
    n_before = len(train)
    train = train[train[known_col] < test_start]
    priors["n_embargoed"] = int(n_before - len(train))
    return train, test, priors


# --------------------------------------------------------------------------- seller snapshots
def load_seller_orders(db_path: Path = DEFAULT_DB) -> pd.DataFrame:
    """(order, seller) rows over ALL in-window orders with items, with outcome known-times."""
    so = query("""
        SELECT so.order_id, so.seller_id, so.purchase_ts, so.is_failed, so.price, so.is_late, so.review_score,
               o.delivered_ts, r.answered_ts, s.state AS seller_state
        FROM fact_seller_orders so
        JOIN fact_orders o       ON o.order_id  = so.order_id
        JOIN dim_seller s        ON s.seller_id = so.seller_id
        LEFT JOIN fact_reviews r ON r.order_id  = so.order_id""", db_path)
    so[["purchase_ts", "delivered_ts", "answered_ts"]] = so[["purchase_ts", "delivered_ts", "answered_ts"]].apply(pd.to_datetime)
    return so


def _count_since(hist: pd.DataFrame, since: pd.Timestamp, index: pd.Index, col: str | None = None) -> pd.Series:
    sub = hist[hist["purchase_ts"] >= since].groupby("seller_id")
    return (sub.size() if col is None else sub[col].sum()).reindex(index, fill_value=0)


def seller_snapshot(so: pd.DataFrame, cutoff: pd.Timestamp, horizon_days: int = 90, min_orders: int = 5,
                    min_label_orders: int = 5, cancel_threshold: float = 0.05, review_threshold: float = 3.0,
                    seasoning_days: int = 14) -> pd.DataFrame:
    """One row per eligible seller (>= min_orders before `cutoff`): features from the past, labels from
    [cutoff, cutoff + horizon).  fail_a = inactivity (no label-window orders).  fail_b = quality
    (cancellation rate above / avg review below threshold), NaN when the seller had too few label-window
    orders to judge.  Cancellation is only measured on orders older than `seasoning_days` (assumed final)."""
    day = pd.Timedelta(days=1)
    hist = so[so["purchase_ts"] < cutoff]
    label = so[(so["purchase_ts"] >= cutoff) & (so["purchase_ts"] < cutoff + horizon_days * day)]
    g = hist.groupby("seller_id")
    f = pd.DataFrame({"n_orders_total": g.size(), "revenue_total": g["price"].sum(),
                      "first_ts": g["purchase_ts"].min(), "last_ts": g["purchase_ts"].max(),
                      "seller_state": g["seller_state"].first()})
    f = f[f["n_orders_total"] >= min_orders].copy()
    idx = f.index
    for d in (30, 60, 90):
        f[f"n_orders_{d}d"] = _count_since(hist, cutoff - d * day, idx)
    f["revenue_90d"] = _count_since(hist, cutoff - 90 * day, idx, col="price")
    f["days_since_last"] = (cutoff - f["last_ts"]) / day
    f["tenure_days"] = (cutoff - f["first_ts"]) / day
    f["order_trend"] = f["n_orders_30d"] / ((f["n_orders_90d"] - f["n_orders_30d"]) / 2 + 1)
    f["aov"] = f["revenue_total"] / f["n_orders_total"]
    seasoned = hist[hist["purchase_ts"] < cutoff - seasoning_days * day]
    f["cancel_rate"] = seasoned.groupby("seller_id")["is_failed"].mean().reindex(idx)
    f["late_rate"] = hist[hist["delivered_ts"] < cutoff].groupby("seller_id")["is_late"].mean().reindex(idx)
    f["avg_review"] = hist[hist["answered_ts"] < cutoff].groupby("seller_id")["review_score"].mean().reindex(idx)
    f = f.drop(columns=["first_ts", "last_ts"])

    lg = label.groupby("seller_id")
    n_label = lg.size().reindex(idx, fill_value=0)
    label_cancel, label_review = lg["is_failed"].mean().reindex(idx), lg["review_score"].mean().reindex(idx)
    f["n_orders_label"] = n_label
    f["fail_a"] = (n_label == 0).astype(int)
    bad = (label_cancel > cancel_threshold) | (label_review < review_threshold)
    f["fail_b"] = np.where(n_label >= min_label_orders, bad.astype(float), np.nan)
    f["cutoff"] = cutoff
    return f.reset_index().rename(columns={"index": "seller_id"})
