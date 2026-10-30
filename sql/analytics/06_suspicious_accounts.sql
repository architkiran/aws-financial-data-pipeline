-- Accounts with the most flagged transactions. Only the hashed key and last four digits are
-- available here: raw account numbers never reach the curated layer.
SELECT
    a.account_last4,
    f.account_key,
    COUNT(*)            AS flagged_txns,
    SUM(f.amount_usd)   AS flagged_usd,
    MAX(f.fraud_score)  AS max_fraud_score
FROM fact_transactions f
JOIN dim_account a ON f.account_key = a.account_key
WHERE f.is_flagged
GROUP BY a.account_last4, f.account_key
ORDER BY flagged_txns DESC, flagged_usd DESC
LIMIT 20
