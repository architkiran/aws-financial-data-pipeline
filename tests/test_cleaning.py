import pandas as pd

from finpipe.cleaning import clean

from .helpers import raw_frame


def test_cosmetic_problems_are_repaired():
    raw = raw_frame(
        {
            "currency": " usd ",
            "merchant_name": "  ACME   MARKET ",
            "account_number": "4111 1111 1111 1111",
            "customer_email": "A@Example.COM",
            "country": "us",
            "channel": "pos",
        }
    )
    row = clean(raw).iloc[0]
    assert row["currency"] == "USD"
    assert row["merchant_name"] == "Acme Market"
    assert row["account_number"] == "4111111111111111"
    assert row["customer_email"] == "a@example.com"
    assert row["country"] == "US"
    assert row["channel"] == "POS"


def test_unparseable_values_become_null_but_the_raw_value_is_kept():
    raw = raw_frame(
        {"amount": "N/A", "txn_timestamp": "not-a-date"},
        {"amount": "inf"},
        {"amount": "12.50"},
    )
    out = clean(raw)
    assert out["amount"].isna().tolist() == [True, True, False]
    assert out["amount_raw"].tolist()[:2] == ["N/A", "inf"]
    assert pd.isna(out.loc[0, "txn_timestamp"])
    assert out.loc[0, "txn_timestamp_raw"] == "not-a-date"
    assert out.loc[2, "amount"] == 12.5


def test_blank_strings_become_null():
    out = clean(raw_frame({"merchant_id": "   ", "country": ""}))
    assert pd.isna(out.loc[0, "merchant_id"])
    assert pd.isna(out.loc[0, "country"])


def test_cleaning_never_drops_rows_or_changes_the_index():
    raw = raw_frame({}, {"amount": None}, {"transaction_id": None})
    raw.index = [10, 20, 30]
    assert clean(raw).index.tolist() == [10, 20, 30]
