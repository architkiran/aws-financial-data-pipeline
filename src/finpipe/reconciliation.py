"""Reconciliation: prove that no data was lost or invented between layers.

Validation answers "is each row good?". Reconciliation answers "did every row end up
somewhere, and do the totals still agree?". It is the control a finance team asks for first.
"""
from __future__ import annotations

from decimal import Decimal

from finpipe.modeling import to_money

PII_COLUMNS = {"account_number", "customer_email"}


class ReconciliationError(Exception):
    def __init__(self, report: dict) -> None:
        failed = [c for c in report["checks"] if not c["passed"]]
        super().__init__("reconciliation failed: " + ", ".join(c["name"] for c in failed))
        self.report = report


def _sum_money(values) -> Decimal:
    total = Decimal("0.00")
    for value in values:
        amount = to_money(value)
        if amount is not None:
            total += amount
    return total


def _check(name: str, expected, actual) -> dict:
    return {"name": name, "expected": str(expected), "actual": str(actual), "passed": expected == actual}


def reconcile(raw, valid, quarantine, fact, dim_account, dim_merchant, dim_date) -> dict:
    """Run all checks; raise ReconciliationError if any fail. Returns the report otherwise."""
    checks = [
        # Every raw row is either in the clean layer or in quarantine, exactly once.
        _check("row_count_raw_equals_clean_plus_quarantine", len(raw), len(valid) + len(quarantine)),
        _check("row_count_fact_equals_clean", len(valid), len(fact)),
        # Control total over every parseable amount, to the cent.
        _check(
            "amount_control_total",
            _sum_money(raw["amount"]),
            _sum_money(valid["amount_raw"]) + _sum_money(quarantine["amount"]),
        ),
        _check("fact_transaction_id_unique", True, bool(fact["transaction_id"].is_unique)),
        # Referential integrity: every fact row can be joined to every dimension.
        _check("fk_date_key", 0, int((~fact["date_key"].isin(dim_date["date_key"])).sum())),
        _check("fk_merchant_id", 0, int((~fact["merchant_id"].isin(dim_merchant["merchant_id"])).sum())),
        _check("fk_account_key", 0, int((~fact["account_key"].isin(dim_account["account_key"])).sum())),
        # PII must not reach the curated layer or quarantine.
        _check(
            "no_pii_columns_in_outputs",
            0,
            len(PII_COLUMNS & (set(fact.columns) | set(quarantine.columns) | set(dim_account.columns))),
        ),
    ]
    report = {"passed": all(c["passed"] for c in checks), "checks": checks}
    if not report["passed"]:
        raise ReconciliationError(report)
    return report
