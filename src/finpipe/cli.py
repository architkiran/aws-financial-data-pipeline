"""Command line interface: generate data, ingest, transform, backfill, inspect, query."""
from __future__ import annotations

import argparse
import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from finpipe.config import Thresholds
from finpipe.generator import generate_day, write_day_csv
from finpipe.ingest import ingest_file
from finpipe.logging_setup import configure_logging
from finpipe.pipeline import run_transform
from finpipe.storage import storage_from_uri

DEFAULT_LAKE = os.environ.get("FINPIPE_LAKE", "data/lake")
DEFAULT_LANDING = os.environ.get("FINPIPE_LANDING", "data/landing")


def _date(value: str) -> date:
    return date.fromisoformat(value)


def _days(start: date, end: date):
    for offset in range((end - start).days + 1):
        yield start + timedelta(days=offset)


def cmd_generate(args) -> None:
    for day in _days(args.start, args.end or args.start):
        path = write_day_csv(generate_day(day, args.rows, args.seed), day, args.landing)
        print(f"generated {path}")


def cmd_ingest(args) -> None:
    storage = storage_from_uri(args.lake)
    for path in args.files:
        key, status = ingest_file(storage, path)
        print(f"{status}: {key}")


def cmd_run(args) -> None:
    storage = storage_from_uri(args.lake)
    summary = run_transform(storage, args.date, thresholds=Thresholds(max_quarantine_rate=args.max_quarantine_rate))
    _print_summary(summary.to_dict())


def cmd_backfill(args) -> None:
    storage = storage_from_uri(args.lake)
    for day in _days(args.start, args.end):
        path = write_day_csv(generate_day(day, args.rows, args.seed), day, args.landing)
        ingest_file(storage, path)
        summary = run_transform(storage, day)
        print(
            f"{day}  raw={summary.raw_rows:>6}  clean={summary.clean_rows:>6}  "
            f"quarantined={summary.quarantined_rows:>5} ({summary.quarantine_rate:.1%})  "
            f"flagged={summary.flagged_rows:>4}  {summary.duration_seconds}s"
        )


def _print_summary(s: dict) -> None:
    print(f"run {s['run_id']}  dt={s['dt']}  status={s['status']}")
    print(f"  raw={s['raw_rows']}  clean={s['clean_rows']}  quarantined={s['quarantined_rows']} ({s['quarantine_rate']:.1%})")
    print(f"  flagged={s['flagged_rows']}  gross_usd={s['gross_usd']}  duration={s['duration_seconds']}s")
    if s["quality_warning"]:
        print(f"  WARNING: quarantine rate above {s['max_quarantine_rate']:.0%}")
    if s["primary_reason_counts"]:
        print("  quarantine reasons:")
        for reason, count in sorted(s["primary_reason_counts"].items(), key=lambda kv: -kv[1]):
            print(f"    {reason:<26}{count}")
    for check in s["reconciliation"].get("checks", []):
        print(f"  [{'ok' if check['passed'] else 'FAIL'}] {check['name']}")


def cmd_summary(args) -> None:
    storage = storage_from_uri(args.lake)
    rows = [storage.read_json(k) for k in storage.list_keys("audit/latest/") if k.endswith(".json")]
    if not rows:
        print("no runs recorded yet")
        return
    frame = pd.DataFrame(rows)[["dt", "status", "raw_rows", "clean_rows", "quarantined_rows", "quarantine_rate", "flagged_rows", "gross_usd"]]
    print(frame.sort_values("dt").to_string(index=False))


def cmd_query(args) -> None:
    from finpipe.analytics import connect, run_sql

    con = connect(args.lake)
    target = Path(args.sql)
    if not target.exists():
        matches = sorted(Path(args.sql_dir).glob(f"{args.sql}*.sql"))
        if not matches:
            raise SystemExit(f"no SQL file matching {args.sql!r} in {args.sql_dir}")
        target = matches[0]
    print(f"-- {target}")
    with pd.option_context("display.width", 200, "display.max_columns", 30):
        print(run_sql(con, target.read_text()).to_string(index=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="finpipe", description=__doc__)
    parser.add_argument("--lake", default=DEFAULT_LAKE, help="local folder or s3://bucket/prefix (default: %(default)s)")
    parser.add_argument("--verbose", action="store_true", help="log pipeline steps")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("generate", help="write synthetic daily CSVs to the landing folder")
    p.add_argument("--start", type=_date, required=True)
    p.add_argument("--end", type=_date)
    p.add_argument("--rows", type=int, default=5000)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--landing", default=DEFAULT_LANDING)
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("ingest", help="copy source files into the immutable raw layer")
    p.add_argument("files", nargs="+")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("run", help="transform one batch date (raw -> clean -> curated)")
    p.add_argument("--date", type=_date, required=True)
    p.add_argument("--max-quarantine-rate", type=float, default=Thresholds().max_quarantine_rate)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("backfill", help="generate, ingest and transform a range of days")
    p.add_argument("--start", type=_date, required=True)
    p.add_argument("--end", type=_date, required=True)
    p.add_argument("--rows", type=int, default=5000)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--landing", default=DEFAULT_LANDING)
    p.set_defaults(func=cmd_backfill)

    p = sub.add_parser("summary", help="show the latest run per batch date")
    p.set_defaults(func=cmd_summary)

    p = sub.add_parser("query", help="run an analytics SQL file locally with DuckDB")
    p.add_argument("sql", help="path to a .sql file, or a prefix such as 01 or 02_top")
    p.add_argument("--sql-dir", default="sql/analytics")
    p.set_defaults(func=cmd_query)
    return parser


def main(argv: "list | None" = None) -> None:
    args = build_parser().parse_args(argv)
    configure_logging("INFO" if args.verbose else "WARNING")
    args.func(args)


if __name__ == "__main__":
    main()
