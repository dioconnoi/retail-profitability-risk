"""Read-only access to the analysis database built by build_db.py."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pandas as pd

from olist_risk.build_db import DEFAULT_DB


@contextmanager
def connect(db_path: Path = DEFAULT_DB) -> Iterator[sqlite3.Connection]:
    if not db_path.exists():
        raise FileNotFoundError(f"{db_path} not found. Run: python -m olist_risk.build_db")
    # Read-only URI so analysis code can never modify the database
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        yield conn
    finally:
        conn.close()


def query(sql: str, db_path: Path = DEFAULT_DB) -> pd.DataFrame:
    with connect(db_path) as conn:
        return pd.read_sql_query(sql, conn)


def with_month_ts(df: pd.DataFrame, col: str = "purchase_month") -> pd.DataFrame:
    """Return a copy with `month` as a Timestamp (first of month) for plotting."""
    return df.assign(month=pd.to_datetime(df[col] + "-01"))
