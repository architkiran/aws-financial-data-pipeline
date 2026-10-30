from datetime import date

import pandas as pd
import pyarrow.parquet as pq
import pytest

from finpipe import pipeline
from finpipe.generator import _pools, generate_day, write_day_csv
from finpipe.ingest import ingest_file
from finpipe.pipeline import run_transform
from finpipe.reconciliation import ReconciliationError

from .conftest import SALT
from .helpers import raw_frame

DAY = date(2026, 10, 1)
ROWS = 2000


def land(lake, tmp_path, day=DAY, rows=ROWS, defect_rate=0.04, seed=3):
    path = write_day_csv(generate_day(day, rows, seed, defect_rate), day, tmp_path / "landing")
    ingest_file(lake, path)


def fact_of(lake, day=DAY):
    return lake.read_parquet(f"curated/fact_transactions/dt={day}/part-0.parquet")


def test_pipeline_quarantines_exactly_the_injected_defects(lake, tmp_path):
    """Ground truth: the generator injects int(n*4%) defects and int(n*1%) duplicates."""
    land(lake, tmp_path)
    summary = run_transform(lake, DAY, salt=SALT)
    defects, duplicates = int(ROWS * 0.04), int(ROWS * 0.01)

    assert summary.status == "SUCCEEDED"
    assert summary.raw_rows == ROWS + duplicates
    assert summary.quarantined_rows == defects + duplicates
    assert summary.clean_rows == ROWS - defects
    assert summary.primary_reason_counts["duplicate_transaction_id"] <= duplicates
    assert all(c["passed"] for c in summary.reconciliation["checks"])


def test_cosmetic_mess_is_repaired_not_quarantined(lake, tmp_path):
    land(lake, tmp_path, defect_rate=0.0)
    summary = run_transform(lake, DAY, salt=SALT)
    assert summary.quarantined_rows == int(ROWS * 0.01)  # only the duplicate deliveries
    assert set(summary.primary_reason_counts) == {"duplicate_transaction_id"}


def test_rerunning_a_day_is_idempotent(lake, tmp_path):
    land(lake, tmp_path)
    run_transform(lake, DAY, salt=SALT)
    keys_before = lake.list_keys("curated/") + lake.list_keys("clean/") + lake.list_keys("quarantine/")
    fact_before = fact_of(lake).drop(columns="run_id")
    dims_before = lake.read_parquet("curated/dim_account/part-0.parquet")

    run_transform(lake, DAY, salt=SALT)
    run_transform(lake, DAY, salt=SALT)

    keys_after = lake.list_keys("curated/") + lake.list_keys("clean/") + lake.list_keys("quarantine/")
    assert keys_after == keys_before
    pd.testing.assert_frame_equal(fact_of(lake).drop(columns="run_id"), fact_before)
    pd.testing.assert_frame_equal(lake.read_parquet("curated/dim_account/part-0.parquet"), dims_before)


def test_reprocessing_one_day_leaves_other_partitions_untouched(lake, tmp_path):
    other = date(2026, 10, 2)
    land(lake, tmp_path)
    land(lake, tmp_path, day=other)
    run_transform(lake, DAY, salt=SALT)
    run_transform(lake, other, salt=SALT)
    untouched = lake.get_bytes(f"curated/fact_transactions/dt={DAY}/part-0.parquet")
    run_transform(lake, other, salt=SALT)
    assert lake.get_bytes(f"curated/fact_transactions/dt={DAY}/part-0.parquet") == untouched


def test_pii_never_reaches_clean_curated_or_quarantine(lake, tmp_path):
    land(lake, tmp_path)
    run_transform(lake, DAY, salt=SALT)
    accounts = set(_pools()[0])
    layers = ["clean/transactions/", "curated/fact_transactions/", "curated/dim_account/", "quarantine/transactions/"]
    for prefix in layers:
        frame = lake.read_parquet_prefix(prefix)
        assert not {"account_number", "customer_email"} & set(frame.columns), prefix
        for column in frame.select_dtypes(include=["object", "string"]).columns:
            assert not frame[column].isin(accounts).any(), f"{prefix}{column} leaks an account number"


def test_the_fraud_rules_catch_the_injected_large_and_night_transfers(lake, tmp_path):
    land(lake, tmp_path)
    run_transform(lake, DAY, salt=SALT)
    fact = fact_of(lake)
    sequence = fact["transaction_id"].str.split("-").str[-1].astype(int)
    injected_transfers = fact[(sequence >= 10_000_000) & (fact["txn_type"] == "TRANSFER")]
    assert len(injected_transfers) > 0
    assert injected_transfers["is_flagged"].all()
    assert fact["flag_velocity"].any()  # card-testing bursts are detected
    assert fact["is_flagged"].mean() < 0.05  # ... without flagging normal traffic wholesale


def test_late_arriving_rows_are_kept_with_their_original_event_date(lake, tmp_path):
    land(lake, tmp_path)
    run_transform(lake, DAY, salt=SALT)
    assert (fact_of(lake)["date_key"] < 20261001).any()


def test_parquet_timestamps_are_microseconds_for_athena(lake, tmp_path):
    land(lake, tmp_path)
    run_transform(lake, DAY, salt=SALT)
    import io

    schema = pq.read_schema(io.BytesIO(lake.get_bytes(f"curated/fact_transactions/dt={DAY}/part-0.parquet")))
    assert str(schema.field("txn_timestamp").type) == "timestamp[us]"
    assert str(schema.field("amount").type) == "decimal128(18, 2)"


def test_audit_records_exist_for_every_run(lake, tmp_path):
    land(lake, tmp_path)
    summary = run_transform(lake, DAY, run_id="run-abc", salt=SALT)
    assert lake.exists(f"audit/runs/dt={DAY}/run-abc.json")
    latest = lake.read_json(f"audit/latest/dt={DAY}/summary.json")
    assert latest["run_id"] == "run-abc" and latest["status"] == "SUCCEEDED"
    assert latest["raw_rows"] == summary.raw_rows


def test_missing_raw_data_fails_loudly_and_is_recorded(lake):
    with pytest.raises(FileNotFoundError):
        run_transform(lake, DAY, run_id="r1", salt=SALT)
    latest = lake.read_json(f"audit/latest/dt={DAY}/summary.json")
    assert latest["status"] == "FAILED" and "FileNotFoundError" in latest["error"]
    assert lake.list_keys("curated/") == []


def test_a_raw_file_missing_required_columns_fails(lake, tmp_path):
    bad = tmp_path / "transactions_2026-10-01.csv"
    raw_frame().drop(columns=["amount"]).to_csv(bad, index=False)
    lake.put_bytes(f"raw/dt={DAY}/{bad.name}", bad.read_bytes())
    with pytest.raises(ValueError, match="missing required columns"):
        run_transform(lake, DAY, salt=SALT)


def test_failed_reconciliation_blocks_every_curated_write(lake, tmp_path, monkeypatch):
    land(lake, tmp_path)
    real = pipeline.build_fact
    monkeypatch.setattr(pipeline, "build_fact", lambda clean, run_id: real(clean, run_id).iloc[:-1])
    with pytest.raises(ReconciliationError):
        run_transform(lake, DAY, run_id="bad-run", salt=SALT)
    assert lake.list_keys("curated/") == [] and lake.list_keys("clean/") == []
    latest = lake.read_json(f"audit/latest/dt={DAY}/summary.json")
    assert latest["status"] == "FAILED_RECONCILIATION"
    assert any(not c["passed"] for c in latest["reconciliation"]["checks"])


def test_a_batch_where_every_row_is_invalid_still_completes(lake, tmp_path):
    path = tmp_path / "transactions_2026-10-01.csv"
    raw_frame({"amount": "-1"}, {"transaction_id": "B", "currency": "XXX"}, {"transaction_id": "C", "amount": None}).to_csv(path, index=False)
    ingest_file(lake, path)
    summary = run_transform(lake, DAY, salt=SALT)
    assert (summary.clean_rows, summary.quarantined_rows) == (0, 3)
    assert summary.quality_warning
    assert len(fact_of(lake)) == 0

    # The next day (with real data) must still work on top of the empty dimensions.
    nxt = date(2026, 10, 2)
    land(lake, tmp_path, day=nxt, rows=500)
    assert run_transform(lake, nxt, salt=SALT).status == "SUCCEEDED"


def test_quality_warning_follows_the_threshold(lake, tmp_path):
    land(lake, tmp_path)
    from finpipe.config import Thresholds

    assert not run_transform(lake, DAY, salt=SALT, thresholds=Thresholds(max_quarantine_rate=0.10)).quality_warning
    assert run_transform(lake, DAY, salt=SALT, thresholds=Thresholds(max_quarantine_rate=0.01)).quality_warning


def test_dimensions_accumulate_across_days(lake, tmp_path):
    for day in (DAY, date(2026, 10, 2), date(2026, 10, 3)):
        land(lake, tmp_path, day=day, rows=800)
        run_transform(lake, day, salt=SALT)
    accounts = lake.read_parquet("curated/dim_account/part-0.parquet")
    assert (accounts["last_seen_date"] > accounts["first_seen_date"]).any()
    assert accounts["account_key"].is_unique


def test_dates_outside_the_calendar_dimension_are_rejected(lake):
    with pytest.raises(ValueError, match="dim_date"):
        run_transform(lake, date(2023, 1, 1), salt=SALT)
