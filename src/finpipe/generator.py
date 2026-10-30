"""Synthetic bank-transaction generator.

Why synthetic: real financial data cannot be shared, and a generator lets us control volume
and inject *known* defects, so the validation layer can be tested against ground truth.

Each generated day contains:
  * realistic traffic (hourly seasonality, a mix of transaction types and currencies)
  * fraud-like patterns (card-testing bursts, high-value night transfers, very large transfers)
  * cosmetic mess that cleaning should repair (casing, whitespace, formatted account numbers)
  * hard defects that validation should quarantine (missing/invalid values, duplicates, ...)
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from finpipe.schema import FX_TO_USD, RAW_COLUMNS

N_ACCOUNTS = 2000
N_MERCHANTS = 150

_CATEGORIES = [
    "GROCERY", "RESTAURANT", "FUEL", "TRAVEL", "ENTERTAINMENT",
    "RETAIL", "UTILITIES", "HEALTHCARE", "ELECTRONICS", "SUBSCRIPTION",
]
_ADJECTIVES = [
    "Golden", "Urban", "Blue", "Prime", "Metro", "Green", "Royal", "Swift",
    "Bright", "Northern", "Pacific", "Summit", "Harbor", "Maple", "Cedar",
]
_NOUNS = [
    "Market", "Bistro", "Pharmacy", "Station", "Electronics", "Cinema", "Travel",
    "Grocers", "Outfitters", "Cafe", "Utilities", "Fitness", "Books", "Hardware", "Diner",
]
_CURRENCIES = ["USD", "EUR", "GBP", "CAD", "INR", "JPY"]
_CURRENCY_P = [0.70, 0.10, 0.08, 0.06, 0.04, 0.02]
_COUNTRIES = ["US", "GB", "DE", "CA", "IN", "JP"]
_COUNTRY_P = [0.60, 0.10, 0.10, 0.10, 0.05, 0.05]
_HOUR_WEIGHTS = np.array(
    [1, 1, 1, 1, 1, 2, 3, 5, 7, 8, 8, 9, 10, 9, 8, 8, 8, 9, 9, 8, 6, 4, 3, 2], dtype=float
)
_HOUR_P = _HOUR_WEIGHTS / _HOUR_WEIGHTS.sum()

_DEFECTS = [
    "missing_amount", "invalid_amount", "negative_amount", "zero_amount", "missing_account",
    "bad_account", "invalid_currency", "bad_timestamp", "future_timestamp", "stale_timestamp",
    "invalid_channel", "missing_txn_id", "missing_merchant",
]
_DEFECT_P = [0.14, 0.08, 0.14, 0.08, 0.09, 0.08, 0.11, 0.08, 0.06, 0.04, 0.05, 0.03, 0.02]


@lru_cache(maxsize=1)
def _pools():
    """Accounts and merchants are fixed across days so history accumulates realistically."""
    rng = np.random.default_rng(20240101)
    accounts, seen = [], set()
    while len(accounts) < N_ACCOUNTS:
        number = "4" + "".join(str(d) for d in rng.integers(0, 10, 15))
        if number not in seen:
            seen.add(number)
            accounts.append(number)
    account_currency = rng.choice(_CURRENCIES, size=N_ACCOUNTS, p=_CURRENCY_P)
    merchants = []
    for i in range(1, N_MERCHANTS + 1):
        merchants.append(
            (
                f"M{i:04d}",
                f"{rng.choice(_ADJECTIVES)} {rng.choice(_NOUNS)}",
                str(rng.choice(_CATEGORIES)),
                str(rng.choice(_COUNTRIES, p=_COUNTRY_P)),
            )
        )
    return accounts, account_currency, merchants


def _fmt(usd: float, currency: str) -> str:
    local = usd / float(FX_TO_USD[currency])
    return f"{local:.2f}"


def _fraud_rows(day, rng, accounts, account_currency, merchants, first_seq, n_rows):
    """Rows that the fraud heuristics should flag."""
    rows = []
    seq = first_seq
    base = datetime.combine(day, time.min)

    def make(acc_idx, ts, usd, txn_type, channel, merchant):
        nonlocal seq
        cur = str(account_currency[acc_idx])
        row = {
            "transaction_id": f"TXN-{day:%Y%m%d}-{seq:07d}",
            "account_number": accounts[acc_idx],
            "customer_email": f"customer{acc_idx:05d}@example.com",
            "merchant_id": merchant[0],
            "merchant_name": merchant[1],
            "merchant_category": merchant[2],
            "txn_type": txn_type,
            "amount": _fmt(usd, cur),
            "currency": cur,
            "txn_timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "channel": channel,
            "country": merchant[3],
        }
        seq += 1
        return row

    # 1) card-testing bursts: several small online purchases within minutes
    for _ in range(max(1, int(n_rows * 0.001))):
        acc = int(rng.integers(0, N_ACCOUNTS))
        start = base + timedelta(seconds=int(rng.integers(8 * 3600, 22 * 3600)))
        merchant = merchants[int(rng.integers(0, N_MERCHANTS))]
        offset = 0
        for _ in range(int(rng.integers(6, 10))):
            offset += int(rng.integers(10, 50))
            usd = float(rng.uniform(1, 20))
            rows.append(make(acc, start + timedelta(seconds=offset), usd, "PURCHASE", "ONLINE", merchant))

    # 2) high-value transfers in the middle of the night
    for _ in range(max(1, int(n_rows * 0.003))):
        acc = int(rng.integers(0, N_ACCOUNTS))
        ts = base + timedelta(seconds=int(rng.integers(2 * 3600, 5 * 3600 - 1)))
        rows.append(make(acc, ts, float(rng.uniform(1500, 4500)), "TRANSFER", "ONLINE", ("M-XFER", "Funds Transfer", "TRANSFER", "US")))

    # 3) very large transfers during the day
    for _ in range(max(1, int(n_rows * 0.004))):
        acc = int(rng.integers(0, N_ACCOUNTS))
        ts = base + timedelta(seconds=int(rng.integers(9 * 3600, 18 * 3600)))
        rows.append(make(acc, ts, float(rng.uniform(5000, 25000)), "TRANSFER", "WIRE", ("M-XFER", "Funds Transfer", "TRANSFER", "US")))
    return rows


def _base_rows(day, rng, n, accounts, account_currency, merchants) -> pd.DataFrame:
    acc_idx = rng.integers(0, N_ACCOUNTS, n)
    txn_type = rng.choice(
        ["PURCHASE", "REFUND", "ATM_WITHDRAWAL", "TRANSFER"], size=n, p=[0.74, 0.04, 0.08, 0.14]
    )

    usd = np.empty(n)
    mask = txn_type == "PURCHASE"
    usd[mask] = np.exp(rng.normal(3.4, 1.0, mask.sum()))
    mask = txn_type == "REFUND"
    usd[mask] = np.exp(rng.normal(3.2, 0.9, mask.sum()))
    mask = txn_type == "ATM_WITHDRAWAL"
    usd[mask] = rng.choice([20, 40, 60, 100, 200, 300], mask.sum())
    mask = txn_type == "TRANSFER"
    usd[mask] = np.exp(rng.normal(5.3, 1.0, mask.sum()))
    usd = np.clip(usd, 1.0, 4500.0)  # normal traffic stays under the fraud thresholds

    currency = np.where(rng.random(n) < 0.95, account_currency[acc_idx], "USD")

    channel = np.empty(n, dtype=object)
    for kind, options, probs in [
        ("PURCHASE", ["POS", "ONLINE", "MOBILE"], [0.5, 0.3, 0.2]),
        ("REFUND", ["POS", "ONLINE"], [0.5, 0.5]),
        ("ATM_WITHDRAWAL", ["ATM"], [1.0]),
        ("TRANSFER", ["ONLINE", "MOBILE", "WIRE"], [0.4, 0.4, 0.2]),
    ]:
        mask = txn_type == kind
        channel[mask] = rng.choice(options, mask.sum(), p=probs)

    merchant_idx = rng.integers(0, N_MERCHANTS, n)
    m_id = np.array([merchants[i][0] for i in merchant_idx], dtype=object)
    m_name = np.array([merchants[i][1] for i in merchant_idx], dtype=object)
    m_cat = np.array([merchants[i][2] for i in merchant_idx], dtype=object)
    m_country = np.array([merchants[i][3] for i in merchant_idx], dtype=object)
    for kind, mid, name, cat in [
        ("ATM_WITHDRAWAL", "M-ATM", "ATM Network", "CASH"),
        ("TRANSFER", "M-XFER", "Funds Transfer", "TRANSFER"),
    ]:
        mask = txn_type == kind
        m_id[mask], m_name[mask], m_cat[mask], m_country[mask] = mid, name, cat, "US"

    hours = rng.choice(24, n, p=_HOUR_P)
    seconds = hours * 3600 + rng.integers(0, 3600, n)
    late = rng.random(n) < 0.015  # late-arriving: event happened 1-2 days before the batch
    seconds = seconds - late * rng.integers(1, 3, n) * 86400
    stamps = (pd.Timestamp(datetime.combine(day, time.min)) + pd.to_timedelta(seconds, unit="s")).strftime("%Y-%m-%d %H:%M:%S")

    return pd.DataFrame(
        {
            "transaction_id": [f"TXN-{day:%Y%m%d}-{i:07d}" for i in range(n)],
            "account_number": [accounts[i] for i in acc_idx],
            "customer_email": [f"customer{i:05d}@example.com" for i in acc_idx],
            "merchant_id": m_id,
            "merchant_name": m_name,
            "merchant_category": m_cat,
            "txn_type": txn_type,
            "amount": [_fmt(u, c) for u, c in zip(usd, currency)],
            "currency": currency,
            "txn_timestamp": stamps,
            "channel": channel,
            "country": m_country,
        }
    )


def _apply_cosmetic_mess(df: pd.DataFrame, rng) -> None:
    """Problems that cleaning (not validation) is expected to repair."""
    n = len(df)
    for i in rng.choice(n, int(n * 0.03), replace=False):
        df.at[i, "currency"] = " " + str(df.at[i, "currency"]).lower() + " "
    for i in rng.choice(n, int(n * 0.03), replace=False):
        df.at[i, "merchant_name"] = "  " + str(df.at[i, "merchant_name"]).upper().replace(" ", "   ")
    for i in rng.choice(n, int(n * 0.02), replace=False):
        df.at[i, "country"] = str(df.at[i, "country"]).lower()
    for i in rng.choice(n, int(n * 0.02), replace=False):
        num = str(df.at[i, "account_number"])
        df.at[i, "account_number"] = " ".join(num[j:j + 4] for j in range(0, len(num), 4))


def _apply_defects(df: pd.DataFrame, rng, day: date, defect_rate: float) -> None:
    """Hard defects that validation should catch. Each row gets at most one."""
    n = len(df)
    count = int(n * defect_rate)
    rows = rng.choice(n, size=count, replace=False)
    kinds = rng.choice(_DEFECTS, size=count, p=_DEFECT_P)
    for i, kind in zip(rows, kinds):
        if kind == "missing_amount":
            df.at[i, "amount"] = None
        elif kind == "invalid_amount":
            df.at[i, "amount"] = str(rng.choice(["N/A", "abc", "12,5O", "--", "NaN"]))
        elif kind == "negative_amount":
            df.at[i, "amount"] = "-" + str(df.at[i, "amount"]).strip()
        elif kind == "zero_amount":
            df.at[i, "amount"] = "0.00"
        elif kind == "missing_account":
            df.at[i, "account_number"] = None
        elif kind == "bad_account":
            df.at[i, "account_number"] = str(rng.choice(["ACCT-UNKNOWN", "12345", "4111-XXXX-1111"]))
        elif kind == "invalid_currency":
            df.at[i, "currency"] = str(rng.choice(["XXX", "US$", "EURO", "???"]))
        elif kind == "bad_timestamp":
            df.at[i, "txn_timestamp"] = str(rng.choice(["not-a-date", "2026-13-45 99:00:00", "31/02/2026", "yesterday"]))
        elif kind == "future_timestamp":
            df.at[i, "txn_timestamp"] = f"{day + timedelta(days=400):%Y-%m-%d} 12:00:00"
        elif kind == "stale_timestamp":
            df.at[i, "txn_timestamp"] = "1970-01-01 00:00:00"
        elif kind == "invalid_channel":
            df.at[i, "channel"] = str(rng.choice(["FAX", "CARRIER_PIGEON"]))
        elif kind == "missing_txn_id":
            df.at[i, "transaction_id"] = None
        elif kind == "missing_merchant":
            df.at[i, "merchant_id"] = None


def generate_day(day: date, n_rows: int = 5000, seed: int = 7, defect_rate: float = 0.04) -> pd.DataFrame:
    """Generate one day's raw feed as strings, exactly as a source system would deliver it.

    Deterministic for a given (day, n_rows, seed, defect_rate).
    """
    rng = np.random.default_rng(seed * 1_000_003 + day.toordinal())
    accounts, account_currency, merchants = _pools()

    fraud = _fraud_rows(day, rng, accounts, account_currency, merchants, 10_000_000, n_rows)
    base = _base_rows(day, rng, max(n_rows - len(fraud), 1), accounts, account_currency, merchants)
    df = pd.concat([base, pd.DataFrame(fraud, columns=RAW_COLUMNS)], ignore_index=True).astype(object)

    _apply_cosmetic_mess(df, rng)
    _apply_defects(df, rng, day, defect_rate)

    # Duplicate deliveries: the same row sent twice (a classic at-least-once source behaviour).
    dups = df.iloc[rng.choice(len(df), int(len(df) * 0.01), replace=False)].copy()
    df = pd.concat([df, dups], ignore_index=True)
    df = df.sample(frac=1, random_state=int(rng.integers(0, 2**31 - 1))).reset_index(drop=True)
    return df[RAW_COLUMNS]


def write_day_csv(df: pd.DataFrame, day: date, out_dir: "str | Path") -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"transactions_{day.isoformat()}.csv"
    df.to_csv(path, index=False)
    return path
