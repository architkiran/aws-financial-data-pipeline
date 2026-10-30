"""Cleaning: repair *cosmetic* problems and parse types. It never drops rows.

Cleaning and validation are deliberately separate stages:
  * cleaning fixes what is unambiguous (whitespace, casing, formatting of account numbers)
  * validation decides which rows are not trustworthy and quarantines them with a reason
Anything that cleaning cannot parse becomes null here, and validation then explains why.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from finpipe.schema import RAW_COLUMNS

_TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def _normalize_text(series: pd.Series) -> pd.Series:
    out = series.astype("string").str.strip().str.replace(r"\s+", " ", regex=True)
    return out.mask(out.fillna("") == "", pd.NA)


def clean(raw: pd.DataFrame) -> pd.DataFrame:
    """Return a typed copy of ``raw`` (same index, same number of rows).

    Adds ``amount_raw`` and ``txn_timestamp_raw`` so validation can tell "missing" apart
    from "present but unparseable".
    """
    df = raw.copy()
    for col in RAW_COLUMNS:
        df[col] = _normalize_text(df[col])

    for col in ("currency", "channel", "txn_type", "country", "merchant_category"):
        df[col] = df[col].str.upper()
    df["customer_email"] = df["customer_email"].str.lower()

    # "4111 1111 1111 1111" and "4111-1111-1111-1111" are the same account number.
    df["account_number"] = df["account_number"].str.replace(r"[\s-]", "", regex=True)

    # ALL-CAPS merchant names are re-cased so one merchant has one spelling.
    name = df["merchant_name"]
    shouting = name.str.isupper().fillna(False).astype(bool)
    df["merchant_name"] = name.where(~shouting, name.str.title())

    df["amount_raw"] = df["amount"]
    numeric = pd.to_numeric(df["amount_raw"], errors="coerce")
    values = numeric.to_numpy(dtype="float64", na_value=np.nan)
    values[~np.isfinite(values)] = np.nan  # "inf" is not an amount
    df["amount"] = pd.Series(values, index=df.index)

    df["txn_timestamp_raw"] = df["txn_timestamp"]
    df["txn_timestamp"] = pd.to_datetime(df["txn_timestamp_raw"], format=_TS_FORMAT, errors="coerce")
    return df
