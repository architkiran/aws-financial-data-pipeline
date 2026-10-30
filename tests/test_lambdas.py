import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from urllib.parse import quote_plus

import boto3
import ingest_handler
import pytest
import quality_gate
from botocore.exceptions import ClientError
from moto import mock_aws

from finpipe.generator import generate_day

BUCKET = "lake-test"
DAY = date(2026, 10, 1)
STATE_MACHINE = "arn:aws:states:us-east-1:123456789012:stateMachine:finpipe"


class FakeSfn:
    """Records start_execution calls and mimics Step Functions' duplicate-name behaviour."""

    def __init__(self):
        self.calls = []

    def start_execution(self, **kwargs):
        if any(call["name"] == kwargs["name"] for call in self.calls):
            raise ClientError({"Error": {"Code": "ExecutionAlreadyExists", "Message": "exists"}}, "StartExecution")
        self.calls.append(kwargs)
        return {"executionArn": "arn:fake"}


class FakeSns:
    def __init__(self):
        self.published = []

    def publish(self, **kwargs):
        self.published.append(kwargs)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("STATE_MACHINE_ARN", STATE_MACHINE)
    monkeypatch.setenv("ALERT_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789012:alerts")
    monkeypatch.setenv("DATA_BUCKET", BUCKET)


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


def put(s3, key, body):
    s3.put_object(Bucket=BUCKET, Key=key, Body=body)


def event_for(key):
    return {"Records": [{"s3": {"bucket": {"name": BUCKET}, "object": {"key": quote_plus(key)}}}]}


def csv_bytes(seed=1, rows=100):
    return generate_day(DAY, rows, seed).to_csv(index=False).encode()


def invoke(s3, key, sfn=None, sns=None):
    sfn, sns = sfn or FakeSfn(), sns or FakeSns()
    result = ingest_handler.handler(event_for(key), None, s3=s3, sfn=sfn, sns=sns)["results"][0]
    return result, sfn, sns


# ---------------------------------------------------------------- ingest handler
def test_a_valid_file_is_copied_to_raw_and_starts_the_pipeline(s3, env):
    body = csv_bytes()
    put(s3, "landing/transactions_2026-10-01.csv", body)
    result, sfn, _ = invoke(s3, "landing/transactions_2026-10-01.csv")

    assert result["status"] == "started" and result["dt"] == "2026-10-01"
    raw = s3.get_object(Bucket=BUCKET, Key="raw/dt=2026-10-01/transactions_2026-10-01.csv")["Body"].read()
    assert raw == body
    (call,) = sfn.calls
    assert call["stateMachineArn"] == STATE_MACHINE
    payload = json.loads(call["input"])
    assert payload["dt"] == "2026-10-01" and payload["raw_key"] == result["raw_key"]
    assert call["name"].startswith("2026-10-01-")


def test_a_repeated_s3_event_does_not_duplicate_anything(s3, env):
    put(s3, "landing/transactions_2026-10-01.csv", csv_bytes())
    sfn = FakeSfn()
    first, _, _ = invoke(s3, "landing/transactions_2026-10-01.csv", sfn)
    second, _, _ = invoke(s3, "landing/transactions_2026-10-01.csv", sfn)
    assert (first["status"], second["status"]) == ("started", "already_started")
    assert len(sfn.calls) == 1
    assert len(s3.list_objects_v2(Bucket=BUCKET, Prefix="raw/")["Contents"]) == 1


def test_a_corrected_file_for_the_same_date_is_kept_beside_the_original(s3, env):
    put(s3, "landing/transactions_2026-10-01.csv", csv_bytes(seed=1))
    sfn = FakeSfn()
    invoke(s3, "landing/transactions_2026-10-01.csv", sfn)
    put(s3, "landing/transactions_2026-10-01.csv", csv_bytes(seed=2))
    result, _, _ = invoke(s3, "landing/transactions_2026-10-01.csv", sfn)

    assert result["status"] == "started" and "__v" in result["raw_key"]
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET, Prefix="raw/")["Contents"]]
    assert len(keys) == 2
    assert len(sfn.calls) == 2  # new content -> new ETag -> new execution


@pytest.mark.parametrize(
    "key,body,reason",
    [
        ("landing/not-a-valid-name.csv", b"a,b\n1,2\n", "file name"),
        ("landing/transactions_2026-10-01.csv", b"", "empty"),
        ("landing/transactions_2026-10-01.csv", b"transaction_id,amount\nT1,5\n", "missing required columns"),
    ],
)
def test_unusable_files_are_rejected_alerted_and_never_start_the_pipeline(s3, env, key, body, reason):
    put(s3, key, body)
    result, sfn, sns = invoke(s3, key)
    assert result["status"] == "rejected" and reason in result["reason"]
    assert sfn.calls == []
    assert s3.list_objects_v2(Bucket=BUCKET, Prefix="raw/").get("Contents") is None
    s3.head_object(Bucket=BUCKET, Key=result["rejected_copy"])
    assert len(sns.published) == 1 and reason in sns.published[0]["Message"]


def test_an_event_for_a_vanished_object_is_skipped(s3, env):
    result, sfn, _ = invoke(s3, "landing/transactions_2026-10-01.csv")
    assert result["status"] == "skipped" and sfn.calls == []


def test_unexpected_errors_propagate_so_lambda_retries_and_dead_letters(s3, env):
    put(s3, "landing/transactions_2026-10-01.csv", csv_bytes())

    class Broken:
        def start_execution(self, **kwargs):
            raise ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "StartExecution")

    with pytest.raises(ClientError):
        ingest_handler.handler(event_for("landing/transactions_2026-10-01.csv"), None, s3=s3, sfn=Broken(), sns=FakeSns())


# ---------------------------------------------------------------- quality gate
def put_summary(s3, **overrides):
    summary = {"run_id": "exec-1", "status": "SUCCEEDED", "quality_warning": False, "raw_rows": 1000, "quarantined_rows": 50, "quarantine_rate": 0.05, "max_quarantine_rate": 0.15}
    summary.update(overrides)
    put(s3, "audit/latest/dt=2026-10-01/summary.json", json.dumps(summary).encode())


def gate(s3, run_id="exec-1"):
    return quality_gate.handler({"dt": "2026-10-01", "run_id": run_id}, None, s3=s3)


def test_gate_passes_a_healthy_run(s3, env):
    put_summary(s3)
    assert gate(s3)["passed"] is True


def test_gate_flags_a_run_with_too_much_quarantined(s3, env):
    put_summary(s3, quality_warning=True, quarantine_rate=0.4)
    result = gate(s3)
    assert result["passed"] is False and result["quarantine_rate"] == 0.4


def test_gate_fails_a_run_that_did_not_succeed(s3, env):
    put_summary(s3, status="FAILED")
    assert gate(s3)["passed"] is False


def test_gate_refuses_a_stale_summary_from_a_different_run(s3, env):
    put_summary(s3, run_id="older-run")
    with pytest.raises(RuntimeError, match="stale"):
        gate(s3)


def test_gate_raises_when_no_summary_exists(s3, env):
    with pytest.raises(ClientError):
        gate(s3)


# ---------------------------------------------------------------- packaging
def test_lambda_modules_import_without_pandas_numpy_or_pyarrow():
    lambdas_dir = Path(__file__).resolve().parents[1] / "lambdas"
    code = (
        "import sys; sys.path.insert(0, %r); import ingest_handler, quality_gate; "
        "bad = [m for m in ('pandas', 'numpy', 'pyarrow') if m in sys.modules]; "
        "assert not bad, bad"
    ) % str(lambdas_dir)
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
