-- What the validation layer rejected, per batch date and reason (from the quarantine table).
SELECT
    dt,
    primary_reason,
    COUNT(*) AS rows_quarantined
FROM quarantine_transactions
GROUP BY dt, primary_reason
ORDER BY dt, rows_quarantined DESC
