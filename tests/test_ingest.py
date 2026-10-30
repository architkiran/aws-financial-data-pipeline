from datetime import date

import pytest

from finpipe.generator import generate_day, write_day_csv
from finpipe.ingest import ingest_file
from finpipe.pipeline import read_raw
from finpipe.schema import parse_batch_date

from .helpers import raw_frame

DAY = date(2026, 10, 1)


def test_new_file_lands_in_a_date_partition(lake, tmp_path):
    path = write_day_csv(generate_day(DAY, 200, seed=1), DAY, tmp_path)
    key, status = ingest_file(lake, path)
    assert (key, status) == ("raw/dt=2026-10-01/transactions_2026-10-01.csv", "ingested")
    assert lake.get_bytes(key) == path.read_bytes()


def test_redelivering_identical_bytes_is_a_noop(lake, tmp_path):
    path = write_day_csv(generate_day(DAY, 200, seed=1), DAY, tmp_path)
    ingest_file(lake, path)
    assert ingest_file(lake, path)[1] == "duplicate_delivery"
    assert len(lake.list_keys("raw/")) == 1


def test_a_different_file_for_the_same_date_never_overwrites_raw(lake, tmp_path):
    first = write_day_csv(generate_day(DAY, 200, seed=1), DAY, tmp_path / "a")
    second = write_day_csv(generate_day(DAY, 200, seed=2), DAY, tmp_path / "b")
    original = first.read_bytes()
    ingest_file(lake, first)
    key, status = ingest_file(lake, second)
    assert status == "ingested" and "__v" in key
    assert lake.get_bytes("raw/dt=2026-10-01/transactions_2026-10-01.csv") == original
    assert len(lake.list_keys("raw/")) == 2
    assert ingest_file(lake, second)[1] == "duplicate_delivery"

    raw, keys = read_raw(lake, DAY)
    assert len(keys) == 2 and raw["source_file"].nunique() == 2


@pytest.mark.parametrize("name", ["data.csv", "transactions_2026-13-45.csv", "transactions_2026-10-01.txt"])
def test_badly_named_files_are_refused(lake, tmp_path, name):
    path = tmp_path / name
    path.write_text("x")
    with pytest.raises(ValueError, match="expected a file named"):
        ingest_file(lake, path)


def test_files_with_a_missing_column_are_refused(lake, tmp_path):
    path = tmp_path / "transactions_2026-10-01.csv"
    raw_frame().drop(columns=["currency"]).to_csv(path, index=False)
    with pytest.raises(ValueError, match="currency"):
        ingest_file(lake, path)
    assert lake.list_keys("raw/") == []


def test_empty_files_are_refused(lake, tmp_path):
    path = tmp_path / "transactions_2026-10-01.csv"
    path.write_bytes(b"")
    with pytest.raises(ValueError, match="empty"):
        ingest_file(lake, path)


def test_parse_batch_date():
    assert parse_batch_date("transactions_2026-10-01.csv") == "2026-10-01"
    assert parse_batch_date("transactions_2026-02-30.csv") is None
    assert parse_batch_date("x/transactions_2026-10-01.csv") is None
