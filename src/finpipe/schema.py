"""Shared schema constants and tiny helpers.

This module must stay free of pandas/pyarrow imports: the Lambda functions import it,
and they ship with only boto3 and the standard library.
"""
from __future__ import annotations

import re
from datetime import date

# ---------------------------------------------------------------------------
# Raw (source) contract
# ---------------------------------------------------------------------------
RAW_COLUMNS = [
    "transaction_id",
    "account_number",
    "customer_email",
    "merchant_id",
    "merchant_name",
    "merchant_category",
    "txn_type",
    "amount",
    "currency",
    "txn_timestamp",
    "channel",
    "country",
]

RAW_FILE_RE = re.compile(r"^transactions_(\d{4}-\d{2}-\d{2})\.csv$")

VALID_CURRENCIES = ("USD", "EUR", "GBP", "CAD", "INR", "JPY")
VALID_CHANNELS = ("POS", "ONLINE", "ATM", "MOBILE", "WIRE")
VALID_TXN_TYPES = ("PURCHASE", "REFUND", "ATM_WITHDRAWAL", "TRANSFER")

# Static reference rates (strings so they can be used as exact Decimals).
# A production pipeline would join a dated FX rates table instead.
FX_TO_USD = {
    "USD": "1.00",
    "EUR": "1.08",
    "GBP": "1.27",
    "CAD": "0.73",
    "INR": "0.012",
    "JPY": "0.0067",
}


def parse_batch_date(filename: str) -> str | None:
    """Return 'YYYY-MM-DD' for a file named transactions_YYYY-MM-DD.csv, else None."""
    match = RAW_FILE_RE.match(filename)
    if not match:
        return None
    try:
        date.fromisoformat(match.group(1))
    except ValueError:
        return None
    return match.group(1)


def parse_header(sample_text: str) -> list[str]:
    """Parse the header row out of the first bytes of a CSV file."""
    lines = sample_text.lstrip("﻿").splitlines()
    if not lines:
        return []
    return [col.strip().strip('"') for col in lines[0].split(",")]


def missing_columns(header: list[str]) -> list[str]:
    """Columns required by the raw contract that are absent from the header."""
    present = set(header)
    return [col for col in RAW_COLUMNS if col not in present]


# ---------------------------------------------------------------------------
# Curated table definitions: (column name, logical type).
# Logical types double as Athena types and map to Arrow types in modeling.py.
# A test keeps infra/terraform/tables.tf in sync with these lists.
# ---------------------------------------------------------------------------
FACT_COLUMNS = [
    ("transaction_id", "string"),
    ("date_key", "int"),
    ("account_key", "string"),
    ("merchant_id", "string"),
    ("txn_type", "string"),
    ("channel", "string"),
    ("country", "string"),
    ("currency", "string"),
    ("amount", "decimal(18,2)"),
    ("amount_usd", "decimal(18,2)"),
    ("txn_timestamp", "timestamp"),
    ("flag_high_amount", "boolean"),
    ("flag_velocity", "boolean"),
    ("flag_night_high_value", "boolean"),
    ("fraud_score", "int"),
    ("is_flagged", "boolean"),
    ("run_id", "string"),
]

DIM_ACCOUNT_COLUMNS = [
    ("account_key", "string"),
    ("account_last4", "string"),
    ("first_seen_date", "date"),
    ("last_seen_date", "date"),
]

DIM_MERCHANT_COLUMNS = [
    ("merchant_id", "string"),
    ("merchant_name", "string"),
    ("merchant_category", "string"),
]

DIM_DATE_COLUMNS = [
    ("date_key", "int"),
    ("full_date", "date"),
    ("year", "int"),
    ("quarter", "int"),
    ("month", "int"),
    ("month_name", "string"),
    ("day", "int"),
    ("day_of_week", "int"),
    ("day_name", "string"),
    ("is_weekend", "boolean"),
]

# Quarantine rows keep the values as received (strings) but never the raw PII:
# the account number is replaced by its keyed hash and the email is dropped.
QUARANTINE_COLUMNS = [
    ("transaction_id", "string"),
    ("account_key", "string"),
    ("merchant_id", "string"),
    ("merchant_name", "string"),
    ("merchant_category", "string"),
    ("txn_type", "string"),
    ("amount", "string"),
    ("currency", "string"),
    ("txn_timestamp", "string"),
    ("channel", "string"),
    ("country", "string"),
    ("source_file", "string"),
    ("source_row", "int"),
    ("primary_reason", "string"),
    ("all_reasons", "string"),
    ("run_id", "string"),
]

# Table name -> (S3 prefix, partitioned by dt?)
TABLES = {
    "fact_transactions": ("curated/fact_transactions/", True),
    "dim_account": ("curated/dim_account/", False),
    "dim_merchant": ("curated/dim_merchant/", False),
    "dim_date": ("curated/dim_date/", False),
    "quarantine_transactions": ("quarantine/transactions/", True),
}

TABLE_COLUMNS = {
    "fact_transactions": FACT_COLUMNS,
    "dim_account": DIM_ACCOUNT_COLUMNS,
    "dim_merchant": DIM_MERCHANT_COLUMNS,
    "dim_date": DIM_DATE_COLUMNS,
    "quarantine_transactions": QUARANTINE_COLUMNS,
}
