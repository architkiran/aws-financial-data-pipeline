"""Generate synthetic daily files and upload them to the landing/ prefix of the lake bucket.

Each upload triggers the deployed pipeline (S3 event -> Lambda -> Step Functions -> Glue).

Glue runs one job at a time (the dimension upsert is not safe to run concurrently), so a big
backfill queues up in Step Functions retries. For more than a handful of days, upload in
smaller batches or use --sleep to space the files out.
"""
from __future__ import annotations

import argparse
import time
from datetime import date, timedelta
from typing import Optional

import boto3

from finpipe.generator import generate_day


def upload(bucket: str, start: date, end: date, rows: int = 5000, seed: int = 7,
           s3=None, sleep: float = 0.0, prefix: str = "landing") -> list:
    s3 = s3 or boto3.client("s3")
    keys = []
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        body = generate_day(day, rows, seed).to_csv(index=False).encode("utf-8")
        key = f"{prefix}/transactions_{day.isoformat()}.csv"
        s3.put_object(Bucket=bucket, Key=key, Body=body)
        keys.append(key)
        print(f"uploaded s3://{bucket}/{key} ({len(body):,} bytes)")
        if sleep and day < end:
            time.sleep(sleep)
    return keys


def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--rows", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--sleep", type=float, default=0.0, help="seconds between uploads")
    args = parser.parse_args(argv)
    upload(args.bucket, args.start, args.end or args.start, args.rows, args.seed, sleep=args.sleep)


if __name__ == "__main__":
    main()
