import io
from decimal import Decimal

import boto3
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from moto import mock_aws

from finpipe.storage import LocalStorage, S3Storage, storage_from_uri


@pytest.fixture(params=["local", "s3"])
def storage(request, tmp_path):
    if request.param == "local":
        yield LocalStorage(tmp_path / "store")
    else:
        with mock_aws():
            client = boto3.client("s3", region_name="us-east-1")
            client.create_bucket(Bucket="bucket")
            yield S3Storage("bucket", prefix="some/prefix", client=client)


def test_bytes_roundtrip_and_exists(storage):
    assert not storage.exists("a/b.bin")
    storage.put_bytes("a/b.bin", b"hello")
    assert storage.exists("a/b.bin")
    assert storage.get_bytes("a/b.bin") == b"hello"
    storage.put_bytes("a/b.bin", b"overwritten")
    assert storage.get_bytes("a/b.bin") == b"overwritten"


def test_list_keys_filters_by_prefix_and_returns_relative_sorted_keys(storage):
    for key in ("raw/dt=2026-10-02/b.csv", "raw/dt=2026-10-01/a.csv", "clean/x.parquet"):
        storage.put_bytes(key, b"x")
    assert storage.list_keys("raw/") == ["raw/dt=2026-10-01/a.csv", "raw/dt=2026-10-02/b.csv"]
    assert storage.list_keys("raw/dt=2026-10-01/") == ["raw/dt=2026-10-01/a.csv"]
    assert storage.list_keys("nothing/") == []


def test_delete_keys_and_prefix(storage):
    for key in ("p/1", "p/2", "q/1"):
        storage.put_bytes(key, b"x")
    storage.delete_keys(["p/1", "does/not/exist"])
    assert storage.list_keys("p/") == ["p/2"]
    storage.delete_prefix("p/")
    assert storage.list_keys() == ["q/1"]


def test_json_roundtrip(storage):
    storage.write_json({"a": 1, "b": [1, 2]}, "audit/x.json")
    assert storage.read_json("audit/x.json") == {"a": 1, "b": [1, 2]}


def test_csv_reader_keeps_na_strings_but_treats_empty_cells_as_null(storage):
    storage.put_bytes("f.csv", b"a,b\nN/A,\nNaN,2\n")
    frame = storage.read_csv("f.csv")
    assert frame["a"].tolist() == ["N/A", "NaN"]
    assert pd.isna(frame.loc[0, "b"]) and frame.loc[1, "b"] == "2"


def test_parquet_roundtrip_preserves_decimals_and_stores_microsecond_timestamps(storage):
    schema = pa.schema([("amount", pa.decimal128(18, 2)), ("ts", pa.timestamp("us"))])
    frame = pd.DataFrame({"amount": [Decimal("1.10"), Decimal("2.25")], "ts": pd.to_datetime(["2026-10-01 01:02:03", "2026-10-01 04:05:06"])})
    storage.write_parquet(frame, "t/part-0.parquet", schema)
    back = storage.read_parquet("t/part-0.parquet")
    assert back["amount"].tolist() == [Decimal("1.10"), Decimal("2.25")]
    assert str(pq.read_schema(io.BytesIO(storage.get_bytes("t/part-0.parquet"))).field("ts").type) == "timestamp[us]"


def test_replace_partition_replaces_instead_of_appending_and_removes_stale_files(storage):
    storage.put_bytes("t/dt=1/old-file.parquet", b"stale")
    storage.replace_partition(pd.DataFrame({"x": [1, 2]}), "t/dt=1/")
    storage.replace_partition(pd.DataFrame({"x": [3]}), "t/dt=1")
    assert storage.list_keys("t/dt=1/") == ["t/dt=1/part-0.parquet"]
    assert storage.read_parquet("t/dt=1/part-0.parquet")["x"].tolist() == [3]


def test_read_parquet_prefix_concatenates_partitions_or_returns_none(storage):
    assert storage.read_parquet_prefix("t/") is None
    storage.replace_partition(pd.DataFrame({"x": [1]}), "t/dt=1/")
    storage.replace_partition(pd.DataFrame({"x": [2, 3]}), "t/dt=2/")
    assert sorted(storage.read_parquet_prefix("t/")["x"]) == [1, 2, 3]


def test_storage_from_uri(tmp_path):
    assert isinstance(storage_from_uri(str(tmp_path)), LocalStorage)
    with mock_aws():
        s3 = storage_from_uri("s3://my-bucket/some/prefix/")
        assert isinstance(s3, S3Storage)
        assert (s3.bucket, s3.prefix) == ("my-bucket", "some/prefix/")
