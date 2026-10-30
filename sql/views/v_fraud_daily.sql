CREATE OR REPLACE VIEW v_fraud_daily AS
SELECT
    d.full_date                                                          AS txn_date,
    COUNT(*)                                                             AS txn_count,
    SUM(CASE WHEN f.is_flagged THEN 1 ELSE 0 END)                        AS flagged_count,
    SUM(CASE WHEN f.flag_high_amount THEN 1 ELSE 0 END)                  AS high_amount_hits,
    SUM(CASE WHEN f.flag_velocity THEN 1 ELSE 0 END)                     AS velocity_hits,
    SUM(CASE WHEN f.flag_night_high_value THEN 1 ELSE 0 END)             AS night_high_value_hits
FROM fact_transactions f
JOIN dim_date d ON f.date_key = d.date_key
GROUP BY d.full_date
