-- Spend by merchant category and day of week (three-way star join).
SELECT
    m.merchant_category,
    d.day_of_week,
    d.day_name,
    COUNT(*)           AS txn_count,
    SUM(f.amount_usd)  AS gross_usd
FROM fact_transactions f
JOIN dim_merchant m ON f.merchant_id = m.merchant_id
JOIN dim_date d     ON f.date_key = d.date_key
WHERE f.txn_type = 'PURCHASE'
GROUP BY m.merchant_category, d.day_of_week, d.day_name
ORDER BY m.merchant_category, d.day_of_week
