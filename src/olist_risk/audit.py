"""Step 0 data audit: row counts, key integrity, date coverage, nulls.

Run from the project root:  python -m olist_risk.audit   (with src on PYTHONPATH)
Purpose: prove the dataset supports each business question before any modelling.
"""
from pathlib import Path

import pandas as pd

RAW = Path(__file__).resolve().parents[2] / "data" / "raw"

FILES = {
    "orders": "olist_orders_dataset.csv",
    "items": "olist_order_items_dataset.csv",
    "payments": "olist_order_payments_dataset.csv",
    "reviews": "olist_order_reviews_dataset.csv",
    "customers": "olist_customers_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "products": "olist_products_dataset.csv",
    "geo": "olist_geolocation_dataset.csv",
    "cat_tr": "product_category_name_translation.csv",
}

DATE_COLS = [
    "order_purchase_timestamp",
    "order_approved_at",
    "order_delivered_carrier_date",
    "order_delivered_customer_date",
    "order_estimated_delivery_date",
]


def load() -> dict[str, pd.DataFrame]:
    return {k: pd.read_csv(RAW / v) for k, v in FILES.items()}


def main() -> None:
    t = load()
    o = t["orders"].copy()
    o[DATE_COLS] = o[DATE_COLS].apply(pd.to_datetime)

    print("== ROW COUNTS ==")
    for k, df in t.items():
        print(f"{k:<10}{len(df):>10,}  cols={df.shape[1]}")

    print("\n== ORDER STATUS ==")
    print(o["order_status"].value_counts().to_string())

    print("\n== DATE COVERAGE (purchase month, orders) ==")
    m = o.groupby(o["order_purchase_timestamp"].dt.to_period("M")).size()
    print(m.to_string())
    print(f"\nrange: {o['order_purchase_timestamp'].min()} -> {o['order_purchase_timestamp'].max()}")

    print("\n== KEY INTEGRITY ==")
    print("orders unique:", o["order_id"].is_unique)
    print("items orphan order_id:", (~t["items"]["order_id"].isin(o["order_id"])).sum())
    print("orders w/o items:", (~o["order_id"].isin(t["items"]["order_id"])).sum())
    print("items orphan seller_id:", (~t["items"]["seller_id"].isin(t["sellers"]["seller_id"])).sum())
    print("items orphan product_id:", (~t["items"]["product_id"].isin(t["products"]["product_id"])).sum())
    print("orders orphan customer_id:", (~o["customer_id"].isin(t["customers"]["customer_id"])).sum())
    print("customers: rows", len(t["customers"]),
          "| unique customer_id", t["customers"]["customer_id"].nunique(),
          "| unique customer_unique_id", t["customers"]["customer_unique_id"].nunique())

    print("\n== REVIEWS ==")
    r = t["reviews"]
    print("review rows", len(r), "| unique orders", r["order_id"].nunique(),
          "| orders with >1 review", (r.groupby("order_id").size() > 1).sum())
    print(r["review_score"].value_counts(normalize=True).sort_index().round(3).to_string())

    print("\n== NULLS (orders) ==")
    print(o.isna().sum().to_string())

    print("\n== DELIVERY (delivered orders only) ==")
    d = o[o["order_status"] == "delivered"].dropna(subset=["order_delivered_customer_date"])
    late = d["order_delivered_customer_date"] > d["order_estimated_delivery_date"]
    print(f"delivered w/ date: {len(d):,} | late rate: {late.mean():.3%}")

    print("\n== PRODUCTS ==")
    p = t["products"]
    print("null category:", p["product_category_name"].isna().sum(),
          "| distinct categories:", p["product_category_name"].nunique())
    missing_tr = set(p["product_category_name"].dropna()) - set(t["cat_tr"]["product_category_name"])
    print("categories missing translation:", missing_tr)

    print("\n== REVENUE / SELLERS ==")
    it = t["items"]
    print(f"item revenue (price): {it['price'].sum():,.0f} | freight: {it['freight_value'].sum():,.0f}")
    print("sellers with items:", it["seller_id"].nunique(), "| rows in sellers:", len(t["sellers"]))
    print("multi-seller orders:", (it.groupby("order_id")["seller_id"].nunique() > 1).sum())
    print("payment rows:", len(t["payments"]), "| orders w/o payment:",
          (~o["order_id"].isin(t["payments"]["order_id"])).sum())


if __name__ == "__main__":
    main()
