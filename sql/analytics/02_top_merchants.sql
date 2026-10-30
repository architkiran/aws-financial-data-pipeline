-- Top 20 merchants by purchase value, with each merchant's share of all purchases.
-- Uses RANK() and a windowed total to avoid a second pass over the fact table.
WITH merchant_totals AS (
    SELECT
        f.merchant_id,
        COUNT(*)           AS txn_count,
        SUM(f.amount_usd)  AS gross_usd
    FROM fact_transactions f
    WHERE f.txn_type = 'PURCHASE'
    GROUP BY f.merchant_id
)
SELECT
    RANK() OVER (ORDER BY t.gross_usd DESC)                         AS rnk,
    m.merchant_name,
    m.merchant_category,
    t.txn_count,
    t.gross_usd,
    ROUND(100.0 * t.gross_usd / SUM(t.gross_usd) OVER (), 2)        AS pct_of_purchases
FROM merchant_totals t
JOIN dim_merchant m ON m.merchant_id = t.merchant_id
ORDER BY rnk
LIMIT 20
