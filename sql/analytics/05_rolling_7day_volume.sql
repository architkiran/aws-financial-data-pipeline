-- Gross spend per day with a trailing 7-day total and average (window frame over daily rows).
WITH daily AS (
    SELECT
        d.full_date        AS txn_date,
        COUNT(*)           AS txn_count,
        SUM(f.amount_usd)  AS gross_usd
    FROM fact_transactions f
    JOIN dim_date d ON f.date_key = d.date_key
    WHERE f.txn_type <> 'REFUND'
    GROUP BY d.full_date
)
SELECT
    txn_date,
    txn_count,
    gross_usd,
    SUM(gross_usd) OVER (ORDER BY txn_date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)           AS rolling_7d_usd,
    ROUND(AVG(gross_usd) OVER (ORDER BY txn_date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW), 2) AS rolling_7d_avg_usd
FROM daily
ORDER BY txn_date
