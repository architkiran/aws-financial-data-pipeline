"""Local ingestion: land a source file in the immutable raw layer.

Mirrors what the S3-triggered Lambda does in AWS (lambdas/ingest_handler.py), so the local
and cloud flows behave the same way.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from finpipe.schema import missing_columns, parse_batch_date, parse_header
from finpipe.storage import Storage

logger = logging.getLogger("finpipe.ingest")


def ingest_file(storage: Storage, path: "str | Path") -> tuple:
    """Copy a source file into raw/dt=YYYY-MM-DD/. Returns (key, status).

    status is 'ingested' or 'duplicate_delivery'. Raw is immutable: a re-delivery of identical
    bytes is a no-op, and a *different* file for the same date is stored next to the original
    (suffix __v<hash>) instead of overwriting it.
    """
    path = Path(path)
    dt = parse_batch_date(path.name)
    if dt is None:
        raise ValueError(f"{path.name}: expected a file named transactions_YYYY-MM-DD.csv")

    data = path.read_bytes()
    if not data:
        raise ValueError(f"{path.name}: file is empty")
    missing = missing_columns(parse_header(data[:65536].decode("utf-8-sig", errors="replace")))
    if missing:
        raise ValueError(f"{path.name}: missing required columns {missing}")

    digest = hashlib.md5(data).hexdigest()  # noqa: S324 - content fingerprint, not security
    key = f"raw/dt={dt}/{path.name}"
    if storage.exists(key):
        if hashlib.md5(storage.get_bytes(key)).hexdigest() == digest:  # noqa: S324
            logger.info("duplicate delivery ignored", extra={"key": key})
            return key, "duplicate_delivery"
        key = f"raw/dt={dt}/{path.stem}__v{digest[:8]}.csv"
        if storage.exists(key):
            return key, "duplicate_delivery"
    storage.put_bytes(key, data)
    logger.info("file ingested", extra={"key": key, "bytes": len(data)})
    return key, "ingested"
