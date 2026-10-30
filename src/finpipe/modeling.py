"""Star schema: fact_transactions plus dim_account, dim_merchant and dim_date.

Money is stored as DECIMAL(18,2), never float, so sums reconcile to the cent.
Dimensions are maintained as SCD type 1 (latest values win) and upserts are idempotent:
re-running a day never changes first/last-seen dates or duplicates a dimension row.
"""
from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

import pandas as pd
import pyarrow as pa

from finpipe.schema import (
    DIM_ACCOUNT_COLUMNS,
    DIM_DATE_COLUMNS,
    DIM_MERCHANT_COLUMNS,
    FACT_COLUMNS,
    FX_TO_USD,
    QUARANTINE_COLUMNS,
)

DIM_DATE_START = date(2024, 1, 1)
DIM_DATE_END = date(2030, 12, 31)

_CENT = Decimal("0.01")
_FX = {currency: Decimal(rate) for currency, rate in FX_TO_USD.items()}

_ARROW_TYPES = {
    "string": pa.string(),
    "int": pa.int32(),
    "bigint": pa.int64(),
    "boolean": pa.bool_(),
    "date": pa.date32(),
    "timestamp": pa.timestamp("us"),
    "decimal(18,2)": pa.decimal128(18, 2),
}


def arrow_schema(columns) -> pa.Schema:
    return pa.schema([(name, _ARROW_TYPES[kind]) for name, kind in columns])


FACT_SCHEMA = arrow_schema(FACT_COLUMNS)
DIM_ACCOUNT_SCHEMA = arrow_schema(DIM_ACCOUNT_COLUMNS)
DIM_MERCHANT_SCHEMA = arrow_schema(DIM_MERCHANT_COLUMNS)
DIM_DATE_SCHEMA = arrow_schema(DIM_DATE_COLUMNS)
QUARANTINE_SCHEMA = arrow_schema(QUARANTINE_COLUMNS)


def to_money(value) -> Optional[Decimal]:
    """Parse an amount string into an exact 2-decimal Decimal (None if not parseable)."""
    if value is None or value is pd.NA:
        return None
    try:
        parsed = Decimal(str(value).strip())
    except Exception:
        return None
    if not parsed.is_finite():
        return None
    return parsed.quantize(_CENT, rounding=ROUND_HALF_UP)


def convert_to_usd(amount: Decimal, currency: str) -> Decimal:
    return (amount * _FX[currency]).quantize(_CENT, rounding=ROUND_HALF_UP)


def build_fact(clean: pd.DataFrame, run_id: str) -> pd.DataFrame:
    """Fact table from validated rows (needs account_key and the fraud flag columns)."""
    c = clean.reset_index(drop=True)
    amounts = [to_money(v) for v in c["amount_raw"]]
    usd = [convert_to_usd(a, cur) for a, cur in zip(amounts, c["currency"])]
    fact = pd.DataFrame(
        {
            "transaction_id": c["transaction_id"].astype("string"),
            "date_key": c["txn_timestamp"].dt.strftime("%Y%m%d").astype("int32"),
            "account_key": c["account_key"].astype("string"),
            "merchant_id": c["merchant_id"].astype("string"),
            "txn_type": c["txn_type"].astype("string"),
            "channel": c["channel"].astype("string"),
            "country": c["country"].astype("string"),
            "currency": c["currency"].astype("string"),
            "amount": pd.Series(amounts, dtype=object),
            "amount_usd": pd.Series(usd, dtype=object),
            "txn_timestamp": c["txn_timestamp"],
            "flag_high_amount": c["flag_high_amount"].astype(bool),
            "flag_velocity": c["flag_velocity"].astype(bool),
            "flag_night_high_value": c["flag_night_high_value"].astype(bool),
            "fraud_score": c["fraud_score"].astype("int32"),
            "is_flagged": c["is_flagged"].astype(bool),
            "run_id": run_id,
        }
    )
    return fact[[name for name, _ in FACT_COLUMNS]]


def build_dim_date(start: date = DIM_DATE_START, end: date = DIM_DATE_END) -> pd.DataFrame:
    days = pd.Series(pd.date_range(start, end, freq="D"))
    dim = pd.DataFrame(
        {
            "date_key": days.dt.strftime("%Y%m%d").astype("int32"),
            "full_date": days.dt.date,
            "year": days.dt.year.astype("int32"),
            "quarter": days.dt.quarter.astype("int32"),
            "month": days.dt.month.astype("int32"),
            "month_name": days.dt.month_name().astype("string"),
            "day": days.dt.day.astype("int32"),
            "day_of_week": (days.dt.dayofweek + 1).astype("int32"),  # ISO: Monday = 1
            "day_name": days.dt.day_name().astype("string"),
            "is_weekend": (days.dt.dayofweek >= 5).astype(bool),
        }
    )
    return dim[[name for name, _ in DIM_DATE_COLUMNS]]


def _most_common(series: pd.Series):
    counts = series.dropna().value_counts()
    return counts.index[0] if len(counts) else pd.NA


def build_dim_merchant(clean: pd.DataFrame) -> pd.DataFrame:
    """One row per merchant. If a merchant appears with several spellings, the most common wins."""
    grouped = clean.groupby("merchant_id")
    dim = pd.DataFrame(
        {
            "merchant_name": grouped["merchant_name"].agg(_most_common),
            "merchant_category": grouped["merchant_category"].agg(_most_common),
        }
    ).reset_index()
    for col in ("merchant_id", "merchant_name", "merchant_category"):
        dim[col] = dim[col].astype("string")
    return dim[[name for name, _ in DIM_MERCHANT_COLUMNS]]


def build_dim_account(clean: pd.DataFrame) -> pd.DataFrame:
    ordered = clean.sort_values("txn_timestamp")
    grouped = ordered.groupby("account_key")
    dim = pd.DataFrame(
        {
            "account_last4": grouped["account_last4"].last(),
            "first_seen_date": grouped["txn_timestamp"].min().dt.date,
            "last_seen_date": grouped["txn_timestamp"].max().dt.date,
        }
    ).reset_index()
    for col in ("account_key", "account_last4"):
        dim[col] = dim[col].astype("string")
    return dim[[name for name, _ in DIM_ACCOUNT_COLUMNS]]


def merge_dim_merchant(existing: Optional[pd.DataFrame], new: pd.DataFrame) -> pd.DataFrame:
    if existing is None or existing.empty:
        return new.sort_values("merchant_id").reset_index(drop=True)
    merged = pd.concat([existing, new], ignore_index=True)
    merged = merged.drop_duplicates(subset="merchant_id", keep="last")  # SCD type 1
    return merged.sort_values("merchant_id").reset_index(drop=True)


def merge_dim_account(existing: Optional[pd.DataFrame], new: pd.DataFrame) -> pd.DataFrame:
    if existing is None or existing.empty:
        return new.sort_values("account_key").reset_index(drop=True)
    merged = pd.concat([existing, new], ignore_index=True).sort_values("last_seen_date")
    out = (
        merged.groupby("account_key")
        .agg(
            account_last4=("account_last4", "last"),
            first_seen_date=("first_seen_date", "min"),
            last_seen_date=("last_seen_date", "max"),
        )
        .reset_index()
    )
    out["account_key"] = out["account_key"].astype("string")
    out["account_last4"] = out["account_last4"].astype("string")
    return out.sort_values("account_key").reset_index(drop=True)
