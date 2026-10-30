"""Keep the Terraform table definitions in sync with the columns the pipeline actually writes.

Athena reads Parquet by column name: a drifted column silently comes back as NULL.
"""
import re
from pathlib import Path

import pytest

from finpipe.schema import TABLE_COLUMNS

TF_DIR = Path(__file__).resolve().parents[1] / "infra" / "terraform"
TABLES_TF = (TF_DIR / "tables.tf").read_text()


def terraform_columns(table: str):
    block = re.search(rf"\b{table}\s*=\s*\[(.*?)\n\s*\]", TABLES_TF, re.DOTALL)
    assert block, f"{table} not found in tables.tf"
    return re.findall(r'name\s*=\s*"(\w+)"\s*,\s*type\s*=\s*"([^"]+)"', block.group(1))


@pytest.mark.parametrize("table", sorted(TABLE_COLUMNS))
def test_terraform_columns_match_pipeline_schema(table):
    assert terraform_columns(table) == TABLE_COLUMNS[table]


def test_partition_projection_is_enabled_for_partitioned_tables():
    assert "projection.dt.type" in TABLES_TF
    assert "storage.location.template" in TABLES_TF


def test_every_terraform_file_has_balanced_braces():
    for path in TF_DIR.glob("*.tf"):
        text = re.sub(r'"(?:[^"\\]|\\.)*"', '""', path.read_text())  # ignore braces inside strings
        text = re.sub(r"#.*", "", text)
        assert text.count("{") == text.count("}"), path.name
        assert text.count("[") == text.count("]"), path.name
