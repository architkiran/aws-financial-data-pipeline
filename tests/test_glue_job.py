from datetime import date

import boto3
import pytest
from moto import mock_aws

import transform_job
from finpipe.config import get_pii_salt
from finpipe.generator import generate_day, write_day_csv
from finpipe.ingest import ingest_file
from finpipe.storage import S3Storage

DAY = date(2026, 10, 1)


@pytest.fixture
def bucket(tmp_path):
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="lake-test")
        path = write_day_csv(generate_day(DAY, 600, seed=4), DAY, tmp_path)
        for prefix in ("a", "b"):
            ingest_file(S3Storage("lake-test", prefix=prefix, client=client), path)
        yield client


def test_job_runs_with_glue_style_arguments_and_ignores_unknown_ones(bucket):
    code = transform_job.main(
        ["--JOB_NAME", "finpipe", "--dt", "2026-10-01", "--run_id", "exec-1", "--lake_uri", "s3://lake-test/a",
         "--TempDir", "s3://somewhere/tmp", "--enable-metrics", ""]
    )
    assert code == 0
    summary = S3Storage("lake-test", prefix="a", client=bucket).read_json("audit/latest/dt=2026-10-01/summary.json")
    assert summary["run_id"] == "exec-1" and summary["status"] == "SUCCEEDED"


def test_a_failing_transform_makes_the_job_fail(bucket):
    with pytest.raises(FileNotFoundError):
        transform_job.main(["--dt", "2026-10-09", "--run_id", "exec-2", "--lake_uri", "s3://lake-test/a"])


def test_the_pii_key_can_come_from_ssm_instead_of_the_environment(bucket):
    boto3.client("ssm", region_name="us-east-1").put_parameter(Name="/finpipe/pii_salt", Value="salt-from-ssm", Type="SecureString")
    assert get_pii_salt("/finpipe/pii_salt") == "salt-from-ssm"

    transform_job.main(["--dt", "2026-10-01", "--run_id", "r-env", "--lake_uri", "s3://lake-test/a"])
    transform_job.main(["--dt", "2026-10-01", "--run_id", "r-ssm", "--lake_uri", "s3://lake-test/b", "--pii_salt_ssm_param", "/finpipe/pii_salt"])
    keys_env = set(S3Storage("lake-test", prefix="a", client=bucket).read_parquet("curated/dim_account/part-0.parquet")["account_key"])
    keys_ssm = set(S3Storage("lake-test", prefix="b", client=bucket).read_parquet("curated/dim_account/part-0.parquet")["account_key"])
    assert keys_env and keys_ssm and keys_env.isdisjoint(keys_ssm)


def test_an_unreachable_secret_fails_the_run_instead_of_falling_back(bucket):
    with pytest.raises(Exception):
        transform_job.main(["--dt", "2026-10-01", "--run_id", "r", "--lake_uri", "s3://lake-test/a", "--pii_salt_ssm_param", "/does/not/exist"])
