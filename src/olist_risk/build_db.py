"""Build the analysis SQLite database from the raw Olist CSVs.

Pipeline:  raw CSV -> raw_* tables (as-is) -> sql/schema/*.sql (clean dims/facts)
           -> sql/views/*.sql (KPI + risk views).

Usage (from project root):  python -m olist_risk.build_db
"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"
SQL_DIR = ROOT / "sql"
DEFAULT_DB = ROOT / "data" / "processed" / "olist.db"

RAW_FILES = {
    "raw_orders": "olist_orders_dataset.csv",
    "raw_order_items": "olist_order_items_dataset.csv",
    "raw_order_payments": "olist_order_payments_dataset.csv",
    "raw_order_reviews": "olist_order_reviews_dataset.csv",
    "raw_customers": "olist_customers_dataset.csv",
    "raw_sellers": "olist_sellers_dataset.csv",
    "raw_products": "olist_products_dataset.csv",
    "raw_geolocation": "olist_geolocation_dataset.csv",
    "raw_category_translation": "product_category_name_translation.csv",
}

MIN_SQLITE = (3, 25, 0)  # window functions


def load_raw(conn: sqlite3.Connection, raw_dir: Path) -> None:
    """Load each CSV untouched (timestamps stay ISO text) so SQL owns all cleaning."""
    for table, filename in RAW_FILES.items():
        path = raw_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Missing raw file: {path}. See README for download steps.")
        pd.read_csv(path).to_sql(table, conn, if_exists="replace", index=False)


def run_sql_dir(conn: sqlite3.Connection, directory: Path) -> None:
    for path in sorted(directory.glob("*.sql")):
        conn.executescript(path.read_text(encoding="utf-8"))


def build(db_path: Path = DEFAULT_DB, raw_dir: Path = RAW_DIR) -> Path:
    if sqlite3.sqlite_version_info < MIN_SQLITE:
        raise RuntimeError(f"SQLite {sqlite3.sqlite_version} < 3.25 (window functions required)")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()  # always rebuild from scratch: reproducible, no stale state
    conn = sqlite3.connect(db_path)
    try:
        load_raw(conn, raw_dir)
        run_sql_dir(conn, SQL_DIR / "schema")
        run_sql_dir(conn, SQL_DIR / "views")
        conn.commit()
    finally:
        conn.close()
    return db_path


if __name__ == "__main__":
    out = build(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB)
    print(f"Built {out}")
