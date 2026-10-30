"""Static checks on the Terraform code.

`terraform validate` runs in CI (it needs the provider plugins). These tests need nothing but a
HCL parser, so they run everywhere and catch the mistakes that are easy to make by hand:
dangling references, wildcard IAM grants, and drift between Terraform and the Python code.
"""
import re
from pathlib import Path

import hcl2
import pytest

ROOT = Path(__file__).resolve().parents[1]
TF_DIR = ROOT / "infra" / "terraform"


def unquote(value):
    if isinstance(value, str) and len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def load(path: Path) -> dict:
    with path.open() as handle:
        return hcl2.load(handle)


def documents() -> dict:
    return {p.name: load(p) for p in sorted(TF_DIR.glob("*.tf"))}


def labelled_blocks(docs: dict, kind: str):
    """Yield (type, name, body) for resource/data blocks."""
    for doc in docs.values():
        for item in doc.get(kind, []):
            for type_key, named in item.items():
                if type_key.startswith("__"):
                    continue
                for name_key, body in named.items():
                    if not name_key.startswith("__"):
                        yield unquote(type_key), unquote(name_key), body


def single_label_blocks(docs: dict, kind: str):
    for doc in docs.values():
        for item in doc.get(kind, []):
            for name_key, body in item.items():
                if not name_key.startswith("__"):
                    yield unquote(name_key), body


def declared(docs: dict) -> dict:
    local_names = set()
    for doc in docs.values():
        for block in doc.get("locals", []):
            local_names |= {k for k in block if not k.startswith("__")}
    return {
        "resources": {(t, n) for t, n, _ in labelled_blocks(docs, "resource")},
        "data": {(t, n) for t, n, _ in labelled_blocks(docs, "data")},
        "variables": {n for n, _ in single_label_blocks(docs, "variable")},
        "locals": local_names,
    }


def source_text() -> str:
    return "\n".join(re.sub(r"(?m)^\s*#.*$", "", p.read_text()) for p in sorted(TF_DIR.glob("*.tf")))


DOCS = documents()
DECLARED = declared(DOCS)
TEXT = source_text()


def test_every_terraform_file_parses_and_declares_something():
    assert len(DOCS) >= 10
    assert len(DECLARED["resources"]) >= 30


def test_every_reference_resolves_to_a_declaration():
    data_refs = set(re.findall(r"\bdata\.(\w+)\.(\w+)", TEXT))
    without_data = re.sub(r"\bdata\.\w+\.\w+", "", TEXT)
    resource_refs = set(re.findall(r"\b((?:aws|random)_\w+)\.(\w+)", without_data))
    var_refs = set(re.findall(r"\bvar\.(\w+)", TEXT))
    local_refs = set(re.findall(r"\blocal\.(\w+)", TEXT))

    assert data_refs - DECLARED["data"] == set()
    assert resource_refs - DECLARED["resources"] == set()
    assert var_refs - DECLARED["variables"] == set()
    assert local_refs - DECLARED["locals"] == set()


def test_no_variable_or_local_is_dead_code():
    used_vars = set(re.findall(r"\bvar\.(\w+)", TEXT))
    used_locals = set(re.findall(r"\blocal\.(\w+)", TEXT))
    assert DECLARED["variables"] - used_vars == set()
    assert DECLARED["locals"] - used_locals - {"table_columns"} == set()


def test_only_the_alert_email_must_be_supplied():
    required = {n for n, body in single_label_blocks(DOCS, "variable") if "default" not in body}
    assert required == {"alert_email"}


# ---------------------------------------------------------------- IAM
def policy_documents():
    return {name: body for t, name, body in labelled_blocks(DOCS, "data") if t == "aws_iam_policy_document"}


def statements(body):
    return body.get("statement", [])


def values(statement, key):
    raw = statement.get(key, [])
    return [unquote(v) for v in (raw if isinstance(raw, list) else [raw])]


def test_no_allow_statement_uses_wildcard_actions():
    for name, body in policy_documents().items():
        for statement in statements(body):
            if unquote(statement.get("effect", '"Allow"')) == "Deny":
                continue
            for action in values(statement, "actions"):
                assert action != "*" and not action.endswith(":*"), f"{name}: {action}"


def test_resource_wildcards_are_limited_to_the_log_delivery_statement():
    for name, body in policy_documents().items():
        for statement in statements(body):
            if unquote(statement.get("effect", '"Allow"')) == "Deny":
                continue
            if "*" in values(statement, "resources"):
                assert unquote(statement["sid"]) == "DeliverExecutionLogs", f"{name}: {statement.get('sid')}"


def grants(doc_name, action):
    """Resources on which `doc_name` allows `action`."""
    out = []
    for statement in statements(policy_documents()[doc_name]):
        if action in values(statement, "actions"):
            out += values(statement, "resources")
    return out


def test_glue_job_can_never_modify_raw_data():
    assert not any("/raw/" in r for r in grants("glue", "s3:PutObject"))
    assert not any("/raw/" in r for r in grants("glue", "s3:DeleteObject"))
    assert any("/raw/" in r for r in grants("glue", "s3:GetObject"))


def test_ingest_lambda_cannot_write_curated_or_clean_data():
    written = grants("ingest", "s3:PutObject")
    assert written and not any(("curated" in r or "clean" in r or "audit" in r) for r in written)


def test_quality_gate_is_read_only_and_limited_to_audit_summaries():
    gate = policy_documents()["quality_gate"]
    all_actions = [a for s in statements(gate) for a in values(s, "actions")]
    assert all_actions == ["s3:GetObject"]
    assert all(r.endswith("/audit/latest/*") for s in statements(gate) for r in values(s, "resources"))


# ---------------------------------------------------------------- storage and ops hygiene
def resources_of(kind):
    return {n: b for t, n, b in labelled_blocks(DOCS, "resource") if t == kind}


def test_the_bucket_is_private_encrypted_versioned_and_tls_only():
    block = next(iter(resources_of("aws_s3_bucket_public_access_block").values()))
    for setting in ("block_public_acls", "block_public_policy", "ignore_public_acls", "restrict_public_buckets"):
        assert str(block[setting]).lower() == "true", setting
    assert resources_of("aws_s3_bucket_server_side_encryption_configuration")
    assert resources_of("aws_s3_bucket_versioning")
    assert "aws:SecureTransport" in TEXT


def test_every_log_group_has_a_retention_period():
    groups = resources_of("aws_cloudwatch_log_group")
    assert len(groups) == 3
    assert all("retention_in_days" in body for body in groups.values())


def test_every_alarm_notifies_someone():
    alarms = resources_of("aws_cloudwatch_metric_alarm")
    assert len(alarms) >= 4
    assert all("alarm_actions" in body for body in alarms.values())


def test_glue_job_invariants_the_design_depends_on():
    glue = (TF_DIR / "glue.tf").read_text()
    assert re.search(r"max_concurrent_runs\s*=\s*1\b", glue), "dimension upserts require serialized runs"
    assert re.search(r"max_retries\s*=\s*0\b", glue), "retries belong to Step Functions"


def test_the_ingest_lambda_has_a_dead_letter_queue_and_bounded_retries():
    ingest = resources_of("aws_lambda_function")["ingest"]
    assert "dead_letter_config" in ingest
    assert re.search(r"maximum_retry_attempts\s*=\s*2\b", (TF_DIR / "lambda.tf").read_text())


def test_athena_workgroup_caps_the_bytes_scanned_per_query():
    assert re.search(r"bytes_scanned_cutoff_per_query\s*=\s*\d+", (TF_DIR / "athena.tf").read_text())


# ---------------------------------------------------------------- Terraform <-> code agreement
def lambda_env_names(function_key: str) -> set:
    text = (TF_DIR / "lambda.tf").read_text()
    block = re.search(rf'resource "aws_lambda_function" "{function_key}" \{{(.*?)\n\}}\n', text, re.DOTALL).group(1)
    env = re.search(r"environment \{\s*variables = \{(.*?)\}", block, re.DOTALL).group(1)
    return set(re.findall(r"^\s*([A-Z_]+)\s*=", env, re.MULTILINE))


@pytest.mark.parametrize("function_key,module", [("ingest", "ingest_handler"), ("quality_gate", "quality_gate")])
def test_lambda_environment_variables_match_what_the_code_reads(function_key, module):
    code = (ROOT / "lambdas" / f"{module}.py").read_text()
    read = set(re.findall(r'os\.environ(?:\.get\(|\[)\s*"([A-Z_]+)"', code))
    assert read and read <= lambda_env_names(function_key)


def test_lambda_handler_paths_point_at_real_functions():
    text = (TF_DIR / "lambda.tf").read_text()
    for module, function in re.findall(r'handler\s*=\s*"(\w+)\.(\w+)"', text):
        assert re.search(rf"^def {function}\(", (ROOT / "lambdas" / f"{module}.py").read_text(), re.MULTILINE)


def test_glue_job_arguments_are_accepted_by_the_job_script(capsys):
    import transform_job

    with pytest.raises(SystemExit):
        transform_job.main(["--help"])
    help_text = capsys.readouterr().out
    glue = (TF_DIR / "glue.tf").read_text()
    ours = set(re.findall(r'"--(\w*_\w*)"\s*=', glue))  # Glue's own options use hyphens
    assert {"lake_uri", "pii_salt_ssm_param", "max_quarantine_rate"} <= ours
    for argument in ours:
        assert f"--{argument}" in help_text
