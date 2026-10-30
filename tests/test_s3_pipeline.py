"""The same pipeline code, run against (mocked) S3 instead of a local folder."""
from datetime import date

from finpipe.generator import generate_day, write_day_csv
from finpipe.ingest import ingest_file
from finpipe.pipeline import run_transform

from .conftest import SALT

DAY = date(2026, 10, 1)


def test_full_run_and_idempotent_rerun_on_s3(s3_storage, tmp_path):
    path = write_day_csv(generate_day(DAY, 1000, seed=2), DAY, tmp_path)
    key, status = ingest_file(s3_storage, path)
    assert (key, status) == (f"raw/dt={DAY}/transactions_{DAY}.csv", "ingested")

    first = run_transform(s3_storage, DAY, run_id="r1", salt=SALT)
    keys = s3_storage.list_keys()
    for expected in (
        f"curated/fact_transactions/dt={DAY}/part-0.parquet",
        f"quarantine/transactions/dt={DAY}/part-0.parquet",
        f"clean/transactions/dt={DAY}/part-0.parquet",
        "curated/dim_account/part-0.parquet",
        "curated/dim_merchant/part-0.parquet",
        "curated/dim_date/part-0.parquet",
        f"audit/latest/dt={DAY}/summary.json",
    ):
        assert expected in keys

    second = run_transform(s3_storage, DAY, run_id="r2", salt=SALT)
    assert s3_storage.list_keys() == sorted(keys + [f"audit/runs/dt={DAY}/r2.json"])
    assert len(s3_storage.read_parquet(f"curated/fact_transactions/dt={DAY}/part-0.parquet")) == first.clean_rows == second.clean_rows
    assert s3_storage.read_json(f"audit/latest/dt={DAY}/summary.json")["run_id"] == "r2"


def test_ingest_is_idempotent_on_s3(s3_storage, tmp_path):
    path = write_day_csv(generate_day(DAY, 300, seed=2), DAY, tmp_path)
    assert ingest_file(s3_storage, path)[1] == "ingested"
    assert ingest_file(s3_storage, path)[1] == "duplicate_delivery"
    assert len(s3_storage.list_keys("raw/")) == 1
