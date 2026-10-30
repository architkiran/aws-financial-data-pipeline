"""The transform stage: raw CSV -> clean -> validated -> curated star schema.

Runs unchanged on a laptop (LocalStorage) and inside an AWS Glue job (S3Storage).

Idempotency: every output is a deterministic file inside a ``dt=`` partition that is
replaced, never appended to. Running the same day twice leaves the lake identical, which is
what makes Step Functions retries and manual re-runs safe.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal

import numpy as np
import pandas as pd

from finpipe.cleaning import clean
from finpipe.config import Thresholds, get_pii_salt
from finpipe.fraud import add_fraud_flags
from finpipe.modeling import (
    DIM_ACCOUNT_SCHEMA,
    DIM_DATE_END,
    DIM_DATE_SCHEMA,
    DIM_DATE_START,
    DIM_MERCHANT_SCHEMA,
    FACT_SCHEMA,
    QUARANTINE_SCHEMA,
    build_dim_account,
    build_dim_date,
    build_dim_merchant,
    build_fact,
    merge_dim_account,
    merge_dim_merchant,
    to_money,
)
from finpipe.pii import hash_values, last4
from finpipe.reconciliation import ReconciliationError, reconcile
from finpipe.schema import FX_TO_USD, QUARANTINE_COLUMNS, RAW_COLUMNS, missing_columns
from finpipe.storage import Storage
from finpipe.validation import ValidationResult, validate

logger = logging.getLogger("finpipe.pipeline")

_FX_FLOAT = {currency: float(rate) for currency, rate in FX_TO_USD.items()}


@dataclass
class RunSummary:
    run_id: str
    dt: str
    status: str = "RUNNING"
    started_at: str = ""
    finished_at: str = ""
    duration_seconds: float = 0.0
    raw_files: list = field(default_factory=list)
    raw_rows: int = 0
    clean_rows: int = 0
    quarantined_rows: int = 0
    quarantine_rate: float = 0.0
    max_quarantine_rate: float = 0.0
    quality_warning: bool = False
    flagged_rows: int = 0
    gross_usd: str = "0.00"
    rule_counts: dict = field(default_factory=dict)
    primary_reason_counts: dict = field(default_factory=dict)
    reconciliation: dict = field(default_factory=dict)
    error: "str | None" = None

    def to_dict(self) -> dict:
        return asdict(self)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_raw(storage: Storage, dt: date) -> tuple:
    """Read every CSV delivered for this batch date, adding lineage columns."""
    keys = [k for k in storage.list_keys(f"raw/dt={dt.isoformat()}/") if k.endswith(".csv")]
    if not keys:
        raise FileNotFoundError(f"no raw files found under raw/dt={dt.isoformat()}/")
    frames = []
    for key in keys:
        frame = storage.read_csv(key)
        missing = missing_columns(list(frame.columns))
        if missing:
            raise ValueError(f"{key} is missing required columns: {missing}")
        frame = frame[RAW_COLUMNS].copy()
        frame["source_file"] = key.rsplit("/", 1)[-1]
        frame["source_row"] = np.arange(1, len(frame) + 1, dtype="int32")
        frames.append(frame)
    return pd.concat(frames, ignore_index=True), keys


def _build_quarantine(raw: pd.DataFrame, cleaned: pd.DataFrame, result: ValidationResult, run_id: str, salt: str) -> pd.DataFrame:
    """Rejected rows as received, with raw PII replaced by a keyed hash."""
    idx = result.invalid.index
    rows = raw.loc[idx].reset_index(drop=True)
    out = rows[
        [
            "transaction_id", "merchant_id", "merchant_name", "merchant_category", "txn_type",
            "amount", "currency", "txn_timestamp", "channel", "country", "source_file", "source_row",
        ]
    ].copy()
    out.insert(1, "account_key", hash_values(cleaned.loc[idx, "account_number"].reset_index(drop=True), salt))
    out["source_row"] = out["source_row"].astype("int32")
    out["primary_reason"] = result.reasons.str.split(";").str[0].reset_index(drop=True)
    out["all_reasons"] = result.reasons.reset_index(drop=True)
    out["run_id"] = run_id
    for col in out.columns:
        if col != "source_row":
            out[col] = out[col].astype("string")
    return out[[name for name, _ in QUARANTINE_COLUMNS]]


def _load_existing(storage: Storage, key: str):
    return storage.read_parquet(key) if storage.exists(key) else None


def run_transform(
    storage: Storage,
    dt: date,
    run_id: "str | None" = None,
    salt: "str | None" = None,
    thresholds: Thresholds = Thresholds(),
) -> RunSummary:
    """Transform one batch date end to end. Raises on failure (after recording the failure)."""
    if not (DIM_DATE_START <= dt <= DIM_DATE_END):
        raise ValueError(f"batch date {dt} is outside the dim_date range")

    run_id = run_id or f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
    summary = RunSummary(run_id=run_id, dt=dt.isoformat(), started_at=_now(), max_quarantine_rate=thresholds.max_quarantine_rate)
    clock = time.monotonic()
    log = {"run_id": run_id, "dt": dt.isoformat()}
    logger.info("run started", extra=log)

    try:
        salt = salt or get_pii_salt()

        raw, keys = read_raw(storage, dt)
        summary.raw_files = [k.rsplit("/", 1)[-1] for k in keys]
        summary.raw_rows = len(raw)
        logger.info("raw loaded", extra={**log, "rows": len(raw), "files": len(keys)})

        cleaned = clean(raw)
        result = validate(cleaned, dt, thresholds)
        summary.rule_counts = result.rule_counts
        summary.primary_reason_counts = result.primary_reason_counts
        logger.info("validation done", extra={**log, "valid": len(result.valid), "quarantined": len(result.invalid)})

        # PII leaves the pipeline here: raw account numbers are replaced by a keyed hash.
        valid = result.valid.copy()
        valid["account_key"] = hash_values(valid["account_number"], salt)
        valid["account_last4"] = last4(valid["account_number"])
        valid["amount_usd_f"] = valid["amount"] * valid["currency"].map(_FX_FLOAT).astype("float64")
        valid = add_fraud_flags(valid, thresholds)

        fact = build_fact(valid, run_id)
        quarantine = _build_quarantine(raw, cleaned, result, run_id, salt)

        clean_out = valid.drop(
            columns=["account_number", "customer_email", "amount", "txn_timestamp_raw", "amount_usd_f"]
        ).reset_index(drop=True)
        clean_out["amount"] = [to_money(v) for v in clean_out["amount_raw"]]
        clean_out = clean_out.drop(columns=["amount_raw"])
        clean_out["run_id"] = run_id

        dim_account = merge_dim_account(_load_existing(storage, "curated/dim_account/part-0.parquet"), build_dim_account(valid))
        dim_merchant = merge_dim_merchant(_load_existing(storage, "curated/dim_merchant/part-0.parquet"), build_dim_merchant(valid))
        dim_date = _load_existing(storage, "curated/dim_date/part-0.parquet")
        if dim_date is None:
            dim_date = build_dim_date()

        # Nothing is written to curated unless reconciliation passes.
        summary.reconciliation = reconcile(raw, valid, quarantine, fact, dim_account, dim_merchant, dim_date)

        part = f"dt={dt.isoformat()}/"
        storage.replace_partition(clean_out, f"clean/transactions/{part}")
        storage.replace_partition(fact, f"curated/fact_transactions/{part}", FACT_SCHEMA)
        storage.replace_partition(quarantine, f"quarantine/transactions/{part}", QUARANTINE_SCHEMA)
        storage.write_parquet(dim_account, "curated/dim_account/part-0.parquet", DIM_ACCOUNT_SCHEMA)
        storage.write_parquet(dim_merchant, "curated/dim_merchant/part-0.parquet", DIM_MERCHANT_SCHEMA)
        if not storage.exists("curated/dim_date/part-0.parquet"):
            storage.write_parquet(dim_date, "curated/dim_date/part-0.parquet", DIM_DATE_SCHEMA)

        summary.clean_rows = len(fact)
        summary.quarantined_rows = len(quarantine)
        summary.quarantine_rate = round(len(quarantine) / len(raw), 4) if len(raw) else 0.0
        summary.quality_warning = summary.quarantine_rate > thresholds.max_quarantine_rate
        summary.flagged_rows = int(fact["is_flagged"].sum())
        summary.gross_usd = str(
            sum((v for v, t in zip(fact["amount_usd"], fact["txn_type"]) if t != "REFUND"), Decimal("0.00"))
        )
        summary.status = "SUCCEEDED"
        return summary

    except ReconciliationError as err:
        summary.status = "FAILED_RECONCILIATION"
        summary.reconciliation = err.report
        summary.error = str(err)
        logger.error("reconciliation failed", extra={**log, "error": str(err)})
        raise
    except Exception as err:
        summary.status = "FAILED"
        summary.error = f"{type(err).__name__}: {err}"
        logger.exception("run failed", extra=log)
        raise
    finally:
        summary.finished_at = _now()
        summary.duration_seconds = round(time.monotonic() - clock, 2)
        # The audit record is written for failures too: it is the forensic trail.
        storage.write_json(summary.to_dict(), f"audit/runs/dt={dt.isoformat()}/{run_id}.json")
        storage.write_json(summary.to_dict(), f"audit/latest/dt={dt.isoformat()}/summary.json")
        logger.info("run finished", extra={**log, "status": summary.status, "seconds": summary.duration_seconds})
