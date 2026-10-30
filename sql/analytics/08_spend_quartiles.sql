-- Customer segmentation: split accounts into spend quartiles with NTILE.
WITH spend AS (
    SELECT
        account_key,
        SUM(amount_usd) AS total_usd
    FROM fact_transactions
    WHERE txn_type = 'PURCHASE'
    GROUP BY account_key
),
ranked AS (
    SELECT
        account_key,
        total_usd,
        NTILE(4) OVER (ORDER BY total_usd) AS spend_quartile
    FROM spend
)
SELECT
    spend_quartile,
    COUNT(*)        AS accounts,
    MIN(total_usd)  AS min_usd,
    MAX(total_usd)  AS max_usd,
    SUM(total_usd)  AS total_usd
FROM ranked
GROUP BY spend_quartile
ORDER BY spend_quartile
