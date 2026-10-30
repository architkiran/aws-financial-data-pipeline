"""Create or update the Athena views defined in sql/views/ (one statement per file).

Terraform owns the tables; views are SQL, so they are applied with the Athena API.
Re-running is safe because every file uses CREATE OR REPLACE VIEW.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Optional

import boto3

ROOT = Path(__file__).resolve().parents[1]


def run_query(athena, sql: str, database: str, workgroup: str, poll_seconds: float = 1.0, timeout_seconds: float = 120.0) -> str:
    query_id = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": database},
        WorkGroup=workgroup,
    )["QueryExecutionId"]
    deadline = time.monotonic() + timeout_seconds
    while True:
        state = athena.get_query_execution(QueryExecutionId=query_id)["QueryExecution"]["Status"]
        if state["State"] == "SUCCEEDED":
            return query_id
        if state["State"] in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"Athena query {state['State']}: {state.get('StateChangeReason', 'no reason given')}")
        if time.monotonic() > deadline:
            raise TimeoutError(f"Athena query {query_id} did not finish within {timeout_seconds}s")
        time.sleep(poll_seconds)


def apply_views(database: str, workgroup: str, views_dir: Path = ROOT / "sql" / "views", athena=None, poll_seconds: float = 1.0) -> list:
    athena = athena or boto3.client("athena")
    applied = []
    for path in sorted(Path(views_dir).glob("*.sql")):
        run_query(athena, path.read_text().strip().rstrip(";"), database, workgroup, poll_seconds)
        applied.append(path.name)
        print(f"applied {path.name}")
    return applied


def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--workgroup", required=True)
    args = parser.parse_args(argv)
    apply_views(args.database, args.workgroup)


if __name__ == "__main__":
    main()
