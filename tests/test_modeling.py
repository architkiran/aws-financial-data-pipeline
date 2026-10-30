from datetime import date
from decimal import Decimal

import pandas as pd
import pyarrow.parquet as pq
import pytest

from finpipe.modeling import (
    FACT_SCHEMA,
    build_dim_account,
    build_dim_date,
    build_dim_merchant,
    convert_to_usd,
    merge_dim_account,
    merge_dim_merchant,
    to_money,
)
from finpipe.schema import TABLE_COLUMNS


@pytest.mark.parametrize(
    "value,expected",
    [("12.345", Decimal("12.35")), ("12.344", Decimal("12.34")), ("7", Decimal("7.00")), ("1e2", Decimal("100.00"))],
)
def test_to_money_rounds_half_up_to_cents(value, expected):
    assert to_money(value) == expected


@pytest.mark.parametrize("value", ["abc", "nan", "inf", None, pd.NA])
def test_to_money_rejects_non_numbers(value):
    assert to_money(value) is None


def test_usd_conversion_is_exact_decimal_arithmetic():
    assert convert_to_usd(Decimal("100.00"), "JPY") == Decimal("0.67")
    assert convert_to_usd(Decimal("10.00"), "EUR") == Decimal("10.80")
    assert convert_to_usd(Decimal("19.99"), "USD") == Decimal("19.99")


def test_dim_date_covers_the_range_with_correct_attributes():
    dim = build_dim_date(date(2024, 1, 1), date(2030, 12, 31))
    assert len(dim) == 2557
    assert dim["date_key"].is_unique
    monday = dim[dim["date_key"] == 20240101].iloc[0]
    assert (monday["day_name"], monday["day_of_week"], monday["is_weekend"]) == ("Monday", 1, False)
    saturday = dim[dim["date_key"] == 20240106].iloc[0]
    assert (saturday["day_name"], saturday["is_weekend"]) == ("Saturday", True)
    assert dim[dim["date_key"] == 20240229].iloc[0]["quarter"] == 1


def _accounts(rows):
    return pd.DataFrame(
        {
            "account_key": [r[0] for r in rows],
            "account_last4": [r[1] for r in rows],
            "txn_timestamp": pd.to_datetime([r[2] for r in rows]),
        }
    )


def test_dim_account_tracks_first_and_last_seen():
    dim = build_dim_account(_accounts([("k1", "1111", "2026-10-03 10:00:00"), ("k1", "1111", "2026-10-01 09:00:00"), ("k2", "2222", "2026-10-02 00:00:00")]))
    k1 = dim[dim["account_key"] == "k1"].iloc[0]
    assert (k1["first_seen_date"], k1["last_seen_date"]) == (date(2026, 10, 1), date(2026, 10, 3))


def test_merging_the_same_account_batch_twice_changes_nothing():
    batch = build_dim_account(_accounts([("k1", "1111", "2026-10-01 09:00:00"), ("k2", "2222", "2026-10-02 09:00:00")]))
    once = merge_dim_account(None, batch)
    twice = merge_dim_account(once, batch)
    pd.testing.assert_frame_equal(once, twice)


def test_merging_a_later_batch_extends_last_seen_but_keeps_first_seen():
    first = merge_dim_account(None, build_dim_account(_accounts([("k1", "1111", "2026-10-01 09:00:00")])))
    merged = merge_dim_account(first, build_dim_account(_accounts([("k1", "1111", "2026-10-05 09:00:00"), ("k3", "3333", "2026-10-05 10:00:00")])))
    k1 = merged[merged["account_key"] == "k1"].iloc[0]
    assert (k1["first_seen_date"], k1["last_seen_date"]) == (date(2026, 10, 1), date(2026, 10, 5))
    assert sorted(merged["account_key"]) == ["k1", "k3"]


def test_merchant_with_several_spellings_gets_the_most_common_one():
    clean = pd.DataFrame(
        {
            "merchant_id": ["M1", "M1", "M1", "M2"],
            "merchant_name": ["Acme Market", "Acme Market", "Acme Mkt", "Zed Cafe"],
            "merchant_category": ["GROCERY", "GROCERY", "GROCERY", "CAFE"],
        }
    )
    dim = build_dim_merchant(clean).set_index("merchant_id")
    assert dim.loc["M1", "merchant_name"] == "Acme Market"


def test_merchant_merge_is_scd_type_1_and_idempotent():
    old = pd.DataFrame({"merchant_id": ["M1"], "merchant_name": ["Old Name"], "merchant_category": ["RETAIL"]})
    new = pd.DataFrame({"merchant_id": ["M1", "M2"], "merchant_name": ["New Name", "Other"], "merchant_category": ["RETAIL", "FUEL"]})
    merged = merge_dim_merchant(old, new)
    assert merged.set_index("merchant_id").loc["M1", "merchant_name"] == "New Name"
    pd.testing.assert_frame_equal(merged, merge_dim_merchant(merged, new))


def test_written_fact_schema_matches_the_declared_contract(demo_lake):
    key = demo_lake.list_keys("curated/fact_transactions/")[0]
    import io

    actual = pq.read_schema(io.BytesIO(demo_lake.get_bytes(key)))
    for field in FACT_SCHEMA:
        assert actual.field(field.name).type == field.type, field.name
    assert actual.names == [name for name, _ in TABLE_COLUMNS["fact_transactions"]]


def test_curated_tables_satisfy_star_schema_integrity(demo_lake):
    fact = demo_lake.read_parquet_prefix("curated/fact_transactions/")
    accounts = demo_lake.read_parquet("curated/dim_account/part-0.parquet")
    merchants = demo_lake.read_parquet("curated/dim_merchant/part-0.parquet")
    dates = demo_lake.read_parquet("curated/dim_date/part-0.parquet")
    assert fact["transaction_id"].is_unique
    assert accounts["account_key"].is_unique and merchants["merchant_id"].is_unique
    assert fact["account_key"].isin(accounts["account_key"]).all()
    assert fact["merchant_id"].isin(merchants["merchant_id"]).all()
    assert fact["date_key"].isin(dates["date_key"]).all()
    assert all(isinstance(v, Decimal) for v in fact["amount"].head(50))
