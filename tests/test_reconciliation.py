import pandas as pd
import pytest

from finpipe.reconciliation import ReconciliationError, reconcile


def frames():
    raw = pd.DataFrame({"amount": ["10.00", "abc", "5.50"]})
    valid = pd.DataFrame({"amount_raw": ["10.00", "5.50"]})
    quarantine = pd.DataFrame({"transaction_id": ["x"], "amount": ["abc"]})
    fact = pd.DataFrame(
        {
            "transaction_id": ["a", "b"],
            "date_key": [20261001, 20261001],
            "merchant_id": ["M1", "M1"],
            "account_key": ["k1", "k2"],
        }
    )
    dims = (
        pd.DataFrame({"account_key": ["k1", "k2"]}),
        pd.DataFrame({"merchant_id": ["M1"]}),
        pd.DataFrame({"date_key": [20261001]}),
    )
    return raw, valid, quarantine, fact, dims


def run(raw, valid, quarantine, fact, dims):
    return reconcile(raw, valid, quarantine, fact, *dims)


def failed_checks(excinfo):
    return {c["name"] for c in excinfo.value.report["checks"] if not c["passed"]}


def test_consistent_layers_pass():
    report = run(*frames())
    assert report["passed"] and all(c["passed"] for c in report["checks"])


def test_a_lost_row_is_detected():
    raw, valid, quarantine, fact, dims = frames()
    with pytest.raises(ReconciliationError) as err:
        run(raw, valid.iloc[:-1], quarantine, fact, dims)
    assert "row_count_raw_equals_clean_plus_quarantine" in failed_checks(err)


def test_an_amount_that_changed_between_layers_is_detected():
    raw, valid, quarantine, fact, dims = frames()
    valid = valid.assign(amount_raw=["10.00", "5.51"])
    with pytest.raises(ReconciliationError) as err:
        run(raw, valid, quarantine, fact, dims)
    assert failed_checks(err) == {"amount_control_total"}


def test_a_fact_row_that_is_missing_downstream_is_detected():
    raw, valid, quarantine, fact, dims = frames()
    with pytest.raises(ReconciliationError) as err:
        run(raw, valid, quarantine, fact.iloc[:-1], dims)
    assert "row_count_fact_equals_clean" in failed_checks(err)


def test_duplicate_fact_keys_are_detected():
    raw, valid, quarantine, fact, dims = frames()
    fact = fact.assign(transaction_id=["a", "a"])
    with pytest.raises(ReconciliationError) as err:
        run(raw, valid, quarantine, fact, dims)
    assert failed_checks(err) == {"fact_transaction_id_unique"}


@pytest.mark.parametrize(
    "column,value,check",
    [("date_key", [20261001, 20990101], "fk_date_key"), ("merchant_id", ["M1", "M9"], "fk_merchant_id"), ("account_key", ["k1", "zz"], "fk_account_key")],
)
def test_orphaned_foreign_keys_are_detected(column, value, check):
    raw, valid, quarantine, fact, dims = frames()
    with pytest.raises(ReconciliationError) as err:
        run(raw, valid, quarantine, fact.assign(**{column: value}), dims)
    assert failed_checks(err) == {check}


def test_pii_columns_in_any_output_are_detected():
    raw, valid, quarantine, fact, dims = frames()
    with pytest.raises(ReconciliationError) as err:
        run(raw, valid, quarantine.assign(account_number="4111111111111111"), fact, dims)
    assert failed_checks(err) == {"no_pii_columns_in_outputs"}
