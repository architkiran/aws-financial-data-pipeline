"""Row-level data quality validation.

Every rule returns a boolean mask that is True for rows that FAIL the rule. Failing rows are
never dropped silently: they go to the quarantine layer with every reason that applies, so
the data team can see exactly what was rejected and why.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from finpipe.config import Thresholds
from finpipe.schema import VALID_CHANNELS, VALID_CURRENCIES, VALID_TXN_TYPES

REASON_SEPARATOR = ";"

# Human-readable catalogue of rules (also used by the README and by tests).
RULE_DESCRIPTIONS = {
    "missing_transaction_id": "transaction_id is empty",
    "missing_account_number": "account_number is empty",
    "invalid_account_number": "account_number is not 12-19 digits",
    "missing_merchant_id": "merchant_id is empty (the fact table needs a merchant to join to)",
    "missing_amount": "amount is empty",
    "invalid_amount": "amount is present but not a finite number",
    "non_positive_amount": "amount is zero or negative (refunds are positive amounts with txn_type REFUND)",
    "missing_timestamp": "txn_timestamp is empty",
    "invalid_timestamp": "txn_timestamp does not match YYYY-MM-DD HH:MM:SS",
    "future_timestamp": "txn_timestamp is after the end of the batch date",
    "stale_timestamp": "txn_timestamp is older than the allowed late-arrival window",
    "invalid_currency": "currency is not in the supported set",
    "invalid_channel": "channel is not in the supported set",
    "invalid_txn_type": "txn_type is not in the supported set",
    "duplicate_transaction_id": "an earlier valid row already has this transaction_id",
}


@dataclass
class ValidationResult:
    valid: pd.DataFrame
    invalid: pd.DataFrame
    reasons: pd.Series  # index = invalid rows, value = 'rule_a;rule_b'
    rule_counts: dict  # rule -> number of rows that hit it (a row can hit several)
    primary_reason_counts: dict  # first reason per quarantined row -> number of rows


def _bool(series: pd.Series) -> pd.Series:
    return series.fillna(False).astype(bool)


def _row_rules(df: pd.DataFrame, batch_date, th: Thresholds) -> dict:
    day_start = pd.Timestamp(batch_date)
    account_ok = _bool(df["account_number"].str.fullmatch(r"\d{12,19}"))
    rules = {
        "missing_transaction_id": df["transaction_id"].isna(),
        "missing_account_number": df["account_number"].isna(),
        "invalid_account_number": df["account_number"].notna() & ~account_ok,
        "missing_merchant_id": df["merchant_id"].isna(),
        "missing_amount": df["amount_raw"].isna(),
        "invalid_amount": df["amount_raw"].notna() & df["amount"].isna(),
        "non_positive_amount": df["amount"].notna() & (df["amount"] <= 0),
        "missing_timestamp": df["txn_timestamp_raw"].isna(),
        "invalid_timestamp": df["txn_timestamp_raw"].notna() & df["txn_timestamp"].isna(),
        "future_timestamp": df["txn_timestamp"] >= day_start + pd.Timedelta(days=1),
        "stale_timestamp": df["txn_timestamp"] < day_start - pd.Timedelta(days=th.stale_days),
        "invalid_currency": ~_bool(df["currency"].isin(VALID_CURRENCIES)),
        "invalid_channel": ~_bool(df["channel"].isin(VALID_CHANNELS)),
        "invalid_txn_type": ~_bool(df["txn_type"].isin(VALID_TXN_TYPES)),
    }
    return {name: _bool(mask) for name, mask in rules.items()}


def validate(df: pd.DataFrame, batch_date, thresholds: Thresholds = Thresholds()) -> ValidationResult:
    """Split a cleaned frame into valid rows and quarantined rows (with reasons)."""
    masks = _row_rules(df, batch_date, thresholds)

    # Duplicates are judged only among rows that passed every other rule. Otherwise a bad
    # first copy would cause a perfectly good second copy to be thrown away as a "duplicate".
    failed_other = pd.Series(False, index=df.index)
    for mask in masks.values():
        failed_other |= mask
    candidates = df.loc[~failed_other]
    repeated = candidates.duplicated(subset=["transaction_id"], keep="first")
    duplicate_mask = pd.Series(False, index=df.index)
    duplicate_mask.loc[repeated[repeated].index] = True
    masks["duplicate_transaction_id"] = duplicate_mask

    reasons = pd.Series("", index=df.index, dtype=object)
    failed = pd.Series(False, index=df.index)
    for name, mask in masks.items():
        reasons = reasons + np.where(mask, name + REASON_SEPARATOR, "")
        failed |= mask
    reasons = reasons.str.rstrip(REASON_SEPARATOR)

    invalid_reasons = reasons[failed]
    primary = invalid_reasons.str.split(REASON_SEPARATOR).str[0]
    return ValidationResult(
        valid=df.loc[~failed],
        invalid=df.loc[failed],
        reasons=invalid_reasons,
        rule_counts={name: int(mask.sum()) for name, mask in masks.items()},
        primary_reason_counts={k: int(v) for k, v in primary.value_counts().items()},
    )
