"""Rule-based fraud and anomaly flags.

These are transparent heuristics, not a model: every flag can be explained in one sentence,
which is how first-line monitoring rules are usually built in practice.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from finpipe.config import Thresholds

FLAG_COLUMNS = ["flag_high_amount", "flag_velocity", "flag_night_high_value"]


def _velocity(df: pd.DataFrame, window_seconds: int) -> pd.Series:
    """Number of transactions by the same account in the trailing window (including this one)."""
    seconds = ((df["txn_timestamp"] - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1)).to_numpy()
    work = pd.DataFrame({"account_key": df["account_key"].to_numpy(), "sec": seconds}, index=df.index)
    work = work.sort_values(["account_key", "sec"], kind="mergesort")
    sec = work["sec"].to_numpy()
    counts = np.zeros(len(work), dtype="int64")
    for positions in work.groupby("account_key", sort=False).indices.values():
        group = sec[positions]
        first_in_window = np.searchsorted(group, group - window_seconds, side="left")
        counts[positions] = np.arange(len(group)) - first_in_window + 1
    return pd.Series(counts, index=work.index).reindex(df.index)


def add_fraud_flags(df: pd.DataFrame, th: Thresholds = Thresholds()) -> pd.DataFrame:
    """Adds flag_* columns, fraud_score (0-3) and is_flagged.

    Requires: account_key, txn_timestamp (datetime), amount_usd_f (float USD amount).
    """
    out = df.copy()
    hour = out["txn_timestamp"].dt.hour
    out["flag_high_amount"] = (out["amount_usd_f"] >= th.high_amount_usd).astype(bool)
    out["flag_night_high_value"] = (
        (hour >= th.night_start_hour) & (hour < th.night_end_hour) & (out["amount_usd_f"] >= th.night_min_usd)
    ).astype(bool)
    out["flag_velocity"] = (_velocity(out, th.velocity_window_seconds) >= th.velocity_count).astype(bool)
    out["fraud_score"] = out[FLAG_COLUMNS].sum(axis=1).astype("int32")
    out["is_flagged"] = out["fraud_score"] >= 1
    return out
