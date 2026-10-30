import hashlib
import zipfile
from datetime import date
from pathlib import Path

import apply_athena_views
import boto3
import build_artifacts
import pytest
import upload_sample_data
from moto import mock_aws

ROOT = Path(__file__).resolve().parents[1]


def test_build_produces_a_glue_zip_and_a_minimal_lambda_bundle():
    archive = build_artifacts.build(ROOT)
    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
    assert {"finpipe/__init__.py", "finpipe/pipeline.py", "finpipe/storage.py"} <= names
    assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)

    bundle = ROOT / "build" / "lambda"
    assert {p.name for p in bundle.glob("*.py")} == {"ingest_handler.py", "quality_gate.py"}
    assert {p.name for p in (bundle / "finpipe").iterdir()} == {"__init__.py", "schema.py"}


def test_build_is_reproducible_so_terraform_sees_no_phantom_changes():
    first = hashlib.sha256(build_artifacts.build(ROOT).read_bytes()).hexdigest()
    second = hashlib.sha256(build_artifacts.build(ROOT).read_bytes()).hexdigest()
    assert first == second


def test_upload_sample_data_puts_one_file_per_day_in_landing():
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="lake")
        keys = upload_sample_data.upload("lake", date(2026, 10, 1), date(2026, 10, 3), rows=200, s3=s3)
        listed = [o["Key"] for o in s3.list_objects_v2(Bucket="lake", Prefix="landing/")["Contents"]]
    assert keys == listed == [f"landing/transactions_2026-10-0{d}.csv" for d in (1, 2, 3)]


class FakeAthena:
    def __init__(self, fail_on=None):
        self.queries, self.fail_on, self.state = [], fail_on, {}

    def start_query_execution(self, QueryString, QueryExecutionContext, WorkGroup):
        qid = f"q{len(self.queries)}"
        self.queries.append((QueryString, QueryExecutionContext["Database"], WorkGroup))
        self.state[qid] = "FAILED" if self.fail_on and self.fail_on in QueryString else "SUCCEEDED"
        return {"QueryExecutionId": qid}

    def get_query_execution(self, QueryExecutionId):
        return {"QueryExecution": {"Status": {"State": self.state[QueryExecutionId], "StateChangeReason": "boom"}}}


def test_views_are_applied_in_order_against_the_right_database():
    athena = FakeAthena()
    applied = apply_athena_views.apply_views("finpipe_db", "finpipe-wg", athena=athena, poll_seconds=0)
    assert applied == sorted(applied) and len(applied) == 3
    assert {(db, wg) for _, db, wg in athena.queries} == {("finpipe_db", "finpipe-wg")}
    assert all(q.startswith("CREATE OR REPLACE VIEW") for q, _, _ in athena.queries)


def test_a_failed_view_statement_stops_the_run_with_the_reason():
    with pytest.raises(RuntimeError, match="boom"):
        apply_athena_views.apply_views("db", "wg", athena=FakeAthena(fail_on="v_fraud_daily"), poll_seconds=0)
