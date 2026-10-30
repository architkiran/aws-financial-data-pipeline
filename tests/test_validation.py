from datetime import date

import pytest

from finpipe.cleaning import clean
from finpipe.config import Thresholds
from finpipe.generator import generate_day
from finpipe.validation import RULE_DESCRIPTIONS, validate

from .helpers import raw_frame

BATCH = date(2026, 10, 1)

CASES = [
    ("missing_transaction_id", {"transaction_id": None}),
    ("missing_account_number", {"account_number": None}),
    ("invalid_account_number", {"account_number": "12345"}),
    ("missing_merchant_id", {"merchant_id": None}),
    ("missing_amount", {"amount": None}),
    ("invalid_amount", {"amount": "N/A"}),
    ("non_positive_amount", {"amount": "-5.00"}),
    ("non_positive_amount", {"amount": "0.00"}),
    ("missing_timestamp", {"txn_timestamp": None}),
    ("invalid_timestamp", {"txn_timestamp": "not-a-date"}),
    ("future_timestamp", {"txn_timestamp": "2026-10-03 09:00:00"}),
    ("stale_timestamp", {"txn_timestamp": "2026-09-01 09:00:00"}),
    ("invalid_currency", {"currency": "XXX"}),
    ("invalid_channel", {"channel": "FAX"}),
    ("invalid_txn_type", {"txn_type": "GIFT"}),
]


@pytest.mark.parametrize("rule,override", CASES, ids=[f"{r}-{list(o.values())[0]}" for r, o in CASES])
def test_each_rule_quarantines_exactly_the_bad_row(rule, override):
    df = clean(raw_frame({"transaction_id": "OK-1"}, {"transaction_id": "BAD-1", **override}))
    result = validate(df, BATCH)
    assert result.valid["transaction_id"].tolist() == ["OK-1"]
    assert len(result.invalid) == 1
    assert rule in result.reasons.iloc[0].split(";")


def test_every_rule_in_the_catalogue_is_tested():
    tested = {rule for rule, _ in CASES} | {"duplicate_transaction_id"}
    assert tested == set(RULE_DESCRIPTIONS)


def test_timestamp_window_boundaries():
    df = clean(
        raw_frame(
            {"transaction_id": "last-second-of-batch-day", "txn_timestamp": "2026-10-01 23:59:59"},
            {"transaction_id": "first-second-of-next-day", "txn_timestamp": "2026-10-02 00:00:00"},
            {"transaction_id": "exactly-7-days-back", "txn_timestamp": "2026-09-24 00:00:00"},
            {"transaction_id": "just-over-7-days-back", "txn_timestamp": "2026-09-23 23:59:59"},
        )
    )
    result = validate(df, BATCH, Thresholds(stale_days=7))
    assert result.valid["transaction_id"].tolist() == ["last-second-of-batch-day", "exactly-7-days-back"]
    assert dict(zip(result.invalid["transaction_id"], result.reasons)) == {
        "first-second-of-next-day": "future_timestamp",
        "just-over-7-days-back": "stale_timestamp",
    }


def test_late_arriving_rows_inside_the_window_are_valid():
    df = clean(raw_frame({"txn_timestamp": "2026-09-30 08:00:00"}))
    assert len(validate(df, BATCH).invalid) == 0


def test_duplicate_keeps_the_first_row_and_quarantines_the_repeat():
    df = clean(raw_frame({"transaction_id": "T"}, {"transaction_id": "T"}))
    result = validate(df, BATCH)
    assert result.valid.index.tolist() == [0]
    assert result.reasons.tolist() == ["duplicate_transaction_id"]


def test_a_valid_copy_survives_when_the_first_copy_is_bad():
    df = clean(raw_frame({"transaction_id": "T", "amount": "-1"}, {"transaction_id": "T", "amount": "10.00"}))
    result = validate(df, BATCH)
    assert result.valid.index.tolist() == [1]
    assert result.reasons.tolist() == ["non_positive_amount"]


def test_all_applicable_reasons_are_recorded_in_rule_order():
    df = clean(raw_frame({"amount": None, "currency": "XXX"}))
    result = validate(df, BATCH)
    assert result.reasons.tolist() == ["missing_amount;invalid_currency"]
    assert result.primary_reason_counts == {"missing_amount": 1}


def test_valid_and_invalid_partition_the_input_without_overlap():
    df = clean(generate_day(BATCH, 1500, seed=5))
    result = validate(df, BATCH)
    assert len(result.valid) + len(result.invalid) == len(df)
    assert set(result.valid.index).isdisjoint(result.invalid.index)
    assert result.valid["transaction_id"].is_unique


def test_a_clean_batch_has_no_quarantined_rows():
    df = clean(raw_frame({"transaction_id": "A"}, {"transaction_id": "B"}))
    result = validate(df, BATCH)
    assert result.invalid.empty
    assert result.rule_counts and not any(result.rule_counts.values())
