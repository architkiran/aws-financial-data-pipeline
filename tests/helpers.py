"""Small builders shared by the tests."""
from __future__ import annotations

import pandas as pd

from finpipe.schema import RAW_COLUMNS

VALID_ROW = {
    "transaction_id": "TXN-1",
    "account_number": "4111111111111111",
    "customer_email": "customer@example.com",
    "merchant_id": "M0001",
    "merchant_name": "Acme Market",
    "merchant_category": "GROCERY",
    "txn_type": "PURCHASE",
    "amount": "25.00",
    "currency": "USD",
    "txn_timestamp": "2026-10-01 10:00:00",
    "channel": "POS",
    "country": "US",
}


def raw_frame(*overrides: dict) -> pd.DataFrame:
    """Raw (string) frame: one valid row per override dict, with the overrides applied."""
    rows = [{**VALID_ROW, **override} for override in (overrides or ({},))]
    return pd.DataFrame(rows, columns=RAW_COLUMNS).astype(object)
