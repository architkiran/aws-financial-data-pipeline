CREATE OR REPLACE VIEW v_data_quality_daily AS
SELECT
    dt,
    primary_reason,
    COUNT(*) AS rows_quarantined
FROM quarantine_transactions
GROUP BY dt, primary_reason
