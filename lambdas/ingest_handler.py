"""S3-triggered ingestion Lambda.

    landing/transactions_YYYY-MM-DD.csv
        -> raw/dt=YYYY-MM-DD/<file>      (immutable copy, the source of truth)
        -> start the Step Functions pipeline for that batch date

Design points:
  * Idempotent. S3 delivers events at least once. A repeated event for identical bytes is a
    no-op, and the Step Functions execution name is derived from (date, object ETag), so a
    second start request for the same file is rejected by Step Functions itself.
  * Cheap and fast. Only the header is read here (a ranged GET); the heavy work happens in Glue.
  * Bad files are not retried forever. Permanent problems (bad name, bad header, empty file)
    are copied to quarantine/rejected_files/ and alerted on. Transient errors are raised, so
    Lambda retries them and finally parks the event in the dead-letter queue.

Dependencies: boto3 and the standard library only, plus finpipe.schema (dependency free).
"""
from __future__ import annotations

import json
import logging
import os
import re
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError

from finpipe.schema import missing_columns, parse_batch_date, parse_header

logger = logging.getLogger()
logger.setLevel(logging.INFO)

HEADER_BYTES = 65536


def _log(event: str, **fields) -> None:
    logger.info(json.dumps({"event": event, **fields}, default=str))


def _head(s3, bucket: str, key: str):
    """HeadObject, or None if the key does not exist."""
    try:
        return s3.head_object(Bucket=bucket, Key=key)
    except ClientError as err:
        if err.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return None
        raise


def _reject(s3, sns, bucket: str, key: str, etag: str, reason: str) -> dict:
    copy_key = f"quarantine/rejected_files/{key.rsplit('/', 1)[-1]}.{etag[:8]}"
    s3.copy_object(Bucket=bucket, Key=copy_key, CopySource={"Bucket": bucket, "Key": key})
    topic = os.environ.get("ALERT_TOPIC_ARN")
    if topic:
        sns.publish(
            TopicArn=topic,
            Subject="finpipe: source file rejected",
            Message=json.dumps({"key": key, "reason": reason, "rejected_copy": copy_key}),
        )
    _log("file_rejected", key=key, reason=reason, rejected_copy=copy_key)
    return {"key": key, "status": "rejected", "reason": reason, "rejected_copy": copy_key}


def _copy_to_raw(s3, bucket: str, source_key: str, dt: str, filename: str, etag: str):
    """Copy into raw/ without ever overwriting. Returns (raw_key, was_duplicate)."""
    raw_key = f"raw/dt={dt}/{filename}"
    existing = _head(s3, bucket, raw_key)
    if existing is not None:
        if existing["ETag"].strip('"') == etag:
            return raw_key, True
        # Same date, different content: keep both. Raw is immutable.
        stem = filename[: -len(".csv")]
        raw_key = f"raw/dt={dt}/{stem}__v{etag[:8]}.csv"
        if _head(s3, bucket, raw_key) is not None:
            return raw_key, True
    s3.copy_object(Bucket=bucket, Key=raw_key, CopySource={"Bucket": bucket, "Key": source_key})
    return raw_key, False


def process_record(record: dict, s3, sfn, sns) -> dict:
    bucket = record["s3"]["bucket"]["name"]
    key = unquote_plus(record["s3"]["object"]["key"])
    filename = key.rsplit("/", 1)[-1]

    head = _head(s3, bucket, key)
    if head is None:
        # Deleted between the event and now (e.g. lifecycle rule). Nothing to do.
        _log("object_gone", key=key)
        return {"key": key, "status": "skipped", "reason": "object_not_found"}
    etag = head["ETag"].strip('"')

    dt = parse_batch_date(filename)
    if dt is None:
        return _reject(s3, sns, bucket, key, etag, "file name must be transactions_YYYY-MM-DD.csv")
    if head["ContentLength"] == 0:
        return _reject(s3, sns, bucket, key, etag, "file is empty")

    sample = s3.get_object(Bucket=bucket, Key=key, Range=f"bytes=0-{HEADER_BYTES - 1}")["Body"].read()
    missing = missing_columns(parse_header(sample.decode("utf-8-sig", errors="replace")))
    if missing:
        return _reject(s3, sns, bucket, key, etag, f"missing required columns: {missing}")

    raw_key, duplicate = _copy_to_raw(s3, bucket, key, dt, filename, etag)
    if duplicate:
        _log("duplicate_delivery", key=key, raw_key=raw_key)

    execution_name = re.sub(r"[^A-Za-z0-9_-]", "", f"{dt}-{etag[:24]}")
    payload = {"dt": dt, "raw_key": raw_key, "source_key": key, "source_etag": etag, "trigger": "s3_event"}
    try:
        sfn.start_execution(
            stateMachineArn=os.environ["STATE_MACHINE_ARN"],
            name=execution_name,
            input=json.dumps(payload),
        )
        status = "started"
    except ClientError as err:
        if err.response["Error"]["Code"] != "ExecutionAlreadyExists":
            raise
        status = "already_started"
    _log("pipeline_triggered", key=key, raw_key=raw_key, execution=execution_name, status=status)
    return {"key": key, "raw_key": raw_key, "dt": dt, "status": status, "execution": execution_name}


def handler(event, context=None, *, s3=None, sfn=None, sns=None):
    s3 = s3 or boto3.client("s3")
    sfn = sfn or boto3.client("stepfunctions")
    sns = sns or boto3.client("sns")
    return {"results": [process_record(r, s3, sfn, sns) for r in event.get("Records", [])]}
