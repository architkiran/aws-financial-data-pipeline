import pandas as pd

from finpipe.config import Thresholds
from finpipe.fraud import add_fraud_flags

BASE = pd.Timestamp("2026-10-01 12:00:00")


def frame(rows):
    """rows: (account_key, timestamp, usd_amount)"""
    return pd.DataFrame(
        {
            "account_key": [r[0] for r in rows],
            "txn_timestamp": pd.to_datetime([r[1] for r in rows]),
            "amount_usd_f": [float(r[2]) for r in rows],
        }
    )


def test_high_amount_threshold_is_inclusive():
    out = add_fraud_flags(frame([("A", BASE, 4999.99), ("B", BASE, 5000.00)]))
    assert out["flag_high_amount"].tolist() == [False, True]


def test_night_flag_needs_both_the_hour_window_and_a_large_amount():
    out = add_fraud_flags(
        frame(
            [
                ("A", "2026-10-01 02:00:00", 1000),  # night + large  -> flag
                ("B", "2026-10-01 02:00:00", 999),  # night, small   -> no
                ("C", "2026-10-01 12:00:00", 2000),  # day, large     -> no
                ("D", "2026-10-01 01:00:00", 2000),  # window start is inclusive
                ("E", "2026-10-01 05:00:00", 2000),  # window end is exclusive
            ]
        )
    )
    assert out["flag_night_high_value"].tolist() == [True, False, False, True, False]


def test_velocity_flags_from_the_fifth_transaction_in_ten_minutes():
    rows = [("A", BASE + pd.Timedelta(seconds=60 * i), 10) for i in range(5)]
    out = add_fraud_flags(frame(rows))
    assert out["flag_velocity"].tolist() == [False, False, False, False, True]


def test_velocity_window_edge_is_inclusive_at_600_seconds():
    on_edge = [("A", BASE + pd.Timedelta(seconds=150 * i), 10) for i in range(5)]  # 0..600s
    past_edge = on_edge[:4] + [("A", BASE + pd.Timedelta(seconds=601), 10)]
    assert add_fraud_flags(frame(on_edge))["flag_velocity"].iloc[-1]
    assert not add_fraud_flags(frame(past_edge))["flag_velocity"].iloc[-1]


def test_velocity_is_counted_per_account_not_across_accounts():
    rows = []
    for i in range(3):
        rows.append(("A", BASE + pd.Timedelta(seconds=30 * i), 10))
        rows.append(("B", BASE + pd.Timedelta(seconds=30 * i + 5), 10))
    assert not add_fraud_flags(frame(rows))["flag_velocity"].any()


def test_result_does_not_depend_on_input_order():
    rows = [("A", BASE + pd.Timedelta(seconds=40 * i), 10) for i in range(7)]
    rows += [("B", BASE + pd.Timedelta(seconds=40 * i), 6000) for i in range(2)]
    df = frame(rows)
    expected = add_fraud_flags(df)
    shuffled = add_fraud_flags(df.sample(frac=1, random_state=3)).sort_index()
    pd.testing.assert_frame_equal(expected, shuffled)


def test_score_and_flagged_summarise_the_individual_flags():
    out = add_fraud_flags(frame([("A", "2026-10-01 03:00:00", 7000), ("B", BASE, 20)]))
    assert out["fraud_score"].tolist() == [2, 0]  # high amount + night
    assert out["is_flagged"].tolist() == [True, False]


def test_thresholds_are_configurable():
    out = add_fraud_flags(frame([("A", BASE, 600)]), Thresholds(high_amount_usd=500))
    assert out["flag_high_amount"].iloc[0]


def test_empty_input_is_handled():
    out = add_fraud_flags(frame([]).astype({"txn_timestamp": "datetime64[ns]"}))
    assert out.empty and "is_flagged" in out.columns
