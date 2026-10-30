"""PII handling: account numbers are replaced by a keyed hash before the clean layer.

HMAC-SHA256 with a secret salt (not a bare hash): account numbers have little entropy, so an
unsalted hash could be reversed by simply hashing every possible number. The same account
always maps to the same key, so joins and per-account analytics still work.
"""
from __future__ import annotations

import hashlib
import hmac

import pandas as pd

KEY_LENGTH = 24  # hex characters kept


def hash_value(value: str, salt: str) -> str:
    digest = hmac.new(salt.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest[:KEY_LENGTH]


def hash_values(series: pd.Series, salt: str) -> pd.Series:
    """Hash each distinct value once; nulls stay null."""
    mapping = {value: hash_value(str(value), salt) for value in series.dropna().unique()}
    return series.map(mapping).astype("string")


def last4(series: pd.Series) -> pd.Series:
    return series.str[-4:]
