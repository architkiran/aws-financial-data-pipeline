-- Daily transaction volume and value, with refunds reported separately from gross spend.
-- Star-schema join: fact_transactions -> dim_date.
SELECT
    d.full_date                                                          AS txn_date,
    COUNT(*)                                                             AS txn_count,
    SUM(CASE WHEN f.txn_type <> 'REFUND' THEN f.amount_usd ELSE 0 END)   AS gross_usd,
    SUM(CASE WHEN f.txn_type = 'REFUND' THEN f.amount_usd ELSE 0 END)    AS refunds_usd,
    AVG(f.amount_usd)                                                    AS avg_txn_usd,
    SUM(CASE WHEN f.is_flagged THEN 1 ELSE 0 END)                        AS flagged_count
FROM fact_transactions f
JOIN dim_date d ON f.date_key = d.date_key
GROUP BY d.full_date
ORDER BY d.full_date
