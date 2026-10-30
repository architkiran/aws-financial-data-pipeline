"""Quality gate Lambda: decides whether a finished run is healthy enough to call "clean".

The Glue job writes an audit summary to audit/latest/dt=<date>/summary.json. This function
reads it and returns {passed: bool, ...} so the state machine can branch:
    passed -> finish quietly
    not passed -> send a "degraded" alert (the data loaded, but too much was quarantined)

It checks that the summary belongs to *this* execution (run_id), so a stale summary from an
earlier run can never make a failed run look healthy.

Dependencies: boto3 and the standard library only.
"""
from __future__ import annotations

import json
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context=None, *, s3=None):
    s3 = s3 or boto3.client("s3")
    bucket = os.environ["DATA_BUCKET"]
    dt = event["dt"]
    expected_run_id = event.get("run_id")

    key = f"audit/latest/dt={dt}/summary.json"
    summary = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())

    if expected_run_id and summary.get("run_id") != expected_run_id:
        raise RuntimeError(
            f"stale audit summary for {dt}: expected run {expected_run_id}, found {summary.get('run_id')}"
        )

    passed = summary.get("status") == "SUCCEEDED" and not summary.get("quality_warning", False)
    result = {
        "passed": passed,
        "dt": dt,
        "run_id": summary.get("run_id"),
        "status": summary.get("status"),
        "raw_rows": summary.get("raw_rows"),
        "quarantined_rows": summary.get("quarantined_rows"),
        "quarantine_rate": summary.get("quarantine_rate"),
        "max_quarantine_rate": summary.get("max_quarantine_rate"),
    }
    logger.info(json.dumps({"event": "quality_gate", **result}))
    return result
