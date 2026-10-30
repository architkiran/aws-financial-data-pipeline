from __future__ import annotations

from datetime import date, timedelta

import boto3
import pytest
from moto import mock_aws

from finpipe.generator import generate_day, write_day_csv
from finpipe.ingest import ingest_file
from finpipe.pipeline import run_transform
from finpipe.storage import LocalStorage, S3Storage

SALT = "test-salt"
DEMO_DAYS = [date(2026, 10, 1) + timedelta(days=i) for i in range(4)]
DEMO_ROWS = 2000


@pytest.fixture(autouse=True)
def _environment(monkeypatch):
    monkeypatch.setenv("PII_SALT", SALT)
    for name, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SECURITY_TOKEN": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": "us-east-1",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("AWS_PROFILE", raising=False)


@pytest.fixture
def lake(tmp_path) -> LocalStorage:
    return LocalStorage(tmp_path / "lake")


@pytest.fixture
def s3_storage():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="lake-test")
        yield S3Storage("lake-test", client=client)


@pytest.fixture(scope="session")
def demo_lake(tmp_path_factory) -> LocalStorage:
    """A lake with four processed days, shared by read-only tests."""
    root = tmp_path_factory.mktemp("demo")
    storage = LocalStorage(root / "lake")
    for day in DEMO_DAYS:
        path = write_day_csv(generate_day(day, DEMO_ROWS, seed=11), day, root / "landing")
        ingest_file(storage, path)
        run_transform(storage, day, salt=SALT)
    return storage
