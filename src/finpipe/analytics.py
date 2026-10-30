"""Run the Athena SQL locally with DuckDB over the same Parquet layout.

The queries in sql/analytics use only portable SQL, so the same text runs on Athena (Trino)
and on DuckDB. Locally this gives a fast feedback loop and lets tests execute every query.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from finpipe.schema import TABLES


def connect(lake_root: "str | Path"):
    """DuckDB connection with one view per curated table (skipping tables with no files)."""
    import duckdb

    con = duckdb.connect(":memory:")
    root = Path(lake_root)
    for table, (prefix, _partitioned) in TABLES.items():
        folder = root / prefix
        if folder.exists() and any(folder.rglob("*.parquet")):
            pattern = f"{folder.as_posix()}/**/*.parquet"
            con.execute(
                f"CREATE OR REPLACE VIEW {table} AS "
                f"SELECT * FROM read_parquet('{pattern}', hive_partitioning=true, union_by_name=true)"
            )
    return con


def run_sql(con, sql: str) -> pd.DataFrame:
    return con.execute(sql.strip().rstrip(";")).df()


def apply_views(con, views_dir: "str | Path") -> list:
    """Create every view in sql/views (one statement per file). Returns the file names."""
    applied = []
    for path in sorted(Path(views_dir).glob("*.sql")):
        con.execute(path.read_text().strip().rstrip(";"))
        applied.append(path.name)
    return applied
