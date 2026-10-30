-- Daily fraud-rule hit counts and the share of transactions flagged.
SELECT
    d.full_date                                                          AS txn_date,
    COUNT(*)                                                             AS txn_count,
    SUM(CASE WHEN f.is_flagged THEN 1 ELSE 0 END)                        AS flagged_count,
    ROUND(100.0 * SUM(CASE WHEN f.is_flagged THEN 1 ELSE 0 END) / COUNT(*), 3) AS flagged_pct,
    SUM(CASE WHEN f.flag_high_amount THEN 1 ELSE 0 END)                  AS high_amount_hits,
    SUM(CASE WHEN f.flag_velocity THEN 1 ELSE 0 END)                     AS velocity_hits,
    SUM(CASE WHEN f.flag_night_high_value THEN 1 ELSE 0 END)             AS night_high_value_hits,
    SUM(CASE WHEN f.is_flagged THEN f.amount_usd ELSE 0 END)             AS flagged_usd
FROM fact_transactions f
JOIN dim_date d ON f.date_key = d.date_key
GROUP BY d.full_date
ORDER BY d.full_date
