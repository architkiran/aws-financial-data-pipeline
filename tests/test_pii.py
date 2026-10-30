import pandas as pd

from finpipe.pii import KEY_LENGTH, hash_value, hash_values, last4


def test_hash_is_deterministic_and_fixed_length():
    first = hash_value("4111111111111111", "salt")
    assert first == hash_value("4111111111111111", "salt")
    assert len(first) == KEY_LENGTH
    assert "4111" not in first


def test_hash_depends_on_the_secret_salt():
    assert hash_value("4111111111111111", "salt-a") != hash_value("4111111111111111", "salt-b")


def test_different_accounts_get_different_keys():
    assert hash_value("4111111111111111", "s") != hash_value("4111111111111112", "s")


def test_series_hashing_keeps_nulls_and_maps_equal_values_equally():
    series = pd.Series(["4111111111111111", None, "4111111111111111", "4222222222222222"], dtype="string")
    out = hash_values(series, "s")
    assert pd.isna(out.iloc[1])
    assert out.iloc[0] == out.iloc[2] != out.iloc[3]


def test_last4():
    assert last4(pd.Series(["4111111111111234"], dtype="string")).iloc[0] == "1234"
