"""Execute every analytics query and view against the processed demo lake (DuckDB)."""
from decimal import Decimal
from pathlib import Path

import pytest

from finpipe.analytics import apply_views, connect, run_sql

SQL_DIR = Path(__file__).resolve().parents[1] / "sql"
QUERIES = sorted((SQL_DIR / "analytics").glob("*.sql"))


@pytest.fixture(scope="module")
def con(demo_lake):
    return connect(demo_lake.root)


def scalar(con, sql):
    return con.execute(sql).fetchone()[0]


@pytest.mark.parametrize("path", QUERIES, ids=lambda p: p.name)
def test_every_analytics_query_runs_and_returns_rows(con, path):
    assert len(run_sql(con, path.read_text())) > 0


def test_there_is_a_query_file_for_each_analysis():
    assert len(QUERIES) >= 8


def test_star_joins_do_not_lose_or_multiply_fact_rows(con):
    total = scalar(con, "SELECT COUNT(*) FROM fact_transactions")
    joined = scalar(
        con,
        "SELECT COUNT(*) FROM fact_transactions f "
        "JOIN dim_date d ON f.date_key = d.date_key "
        "JOIN dim_merchant m ON f.merchant_id = m.merchant_id "
        "JOIN dim_account a ON f.account_key = a.account_key",
    )
    assert joined == total > 0


def test_daily_volume_adds_up_to_the_fact_table(con):
    frame = run_sql(con, (SQL_DIR / "analytics" / "01_daily_volume.sql").read_text())
    assert frame["txn_count"].sum() == scalar(con, "SELECT COUNT(*) FROM fact_transactions")
    exact = scalar(con, "SELECT SUM(amount_usd) FROM fact_transactions WHERE txn_type <> 'REFUND'")
    assert Decimal(str(frame["gross_usd"].sum())) == Decimal(str(exact))


def test_money_totals_match_the_pipeline_audit_records(con, demo_lake):
    audited = sum(Decimal(demo_lake.read_json(k)["gross_usd"]) for k in demo_lake.list_keys("audit/latest/"))
    in_sql = scalar(con, "SELECT SUM(amount_usd) FROM fact_transactions WHERE txn_type <> 'REFUND'")
    assert Decimal(str(in_sql)) == audited


def test_top_merchants_are_ranked_and_shares_are_sane(con):
    frame = run_sql(con, (SQL_DIR / "analytics" / "02_top_merchants.sql").read_text())
    assert frame["rnk"].iloc[0] == 1
    assert frame["gross_usd"].is_monotonic_decreasing
    assert float(frame["pct_of_purchases"].sum()) <= 100.01


def test_fraud_summary_matches_the_flag_column(con):
    frame = run_sql(con, (SQL_DIR / "analytics" / "04_fraud_summary.sql").read_text())
    assert frame["flagged_count"].sum() == scalar(con, "SELECT COUNT(*) FROM fact_transactions WHERE is_flagged")


def test_rolling_window_never_trails_the_day_it_ends_on(con):
    frame = run_sql(con, (SQL_DIR / "analytics" / "05_rolling_7day_volume.sql").read_text())
    assert (frame["rolling_7d_usd"].astype(float) >= frame["gross_usd"].astype(float)).all()


def test_quality_report_accounts_for_every_quarantined_row(con):
    frame = run_sql(con, (SQL_DIR / "analytics" / "07_data_quality_by_reason.sql").read_text())
    assert frame["rows_quarantined"].sum() == scalar(con, "SELECT COUNT(*) FROM quarantine_transactions")


def test_spend_quartiles_cover_every_purchasing_account(con):
    frame = run_sql(con, (SQL_DIR / "analytics" / "08_spend_quartiles.sql").read_text())
    assert frame["spend_quartile"].tolist() == [1, 2, 3, 4]
    assert frame["accounts"].sum() == scalar(con, "SELECT COUNT(DISTINCT account_key) FROM fact_transactions WHERE txn_type = 'PURCHASE'")


def test_views_can_be_created_and_queried(con):
    applied = apply_views(con, SQL_DIR / "views")
    assert applied == ["v_daily_volume.sql", "v_data_quality_daily.sql", "v_fraud_daily.sql"]
    for view in ("v_daily_volume", "v_fraud_daily", "v_data_quality_daily"):
        assert scalar(con, f"SELECT COUNT(*) FROM {view}") > 0
