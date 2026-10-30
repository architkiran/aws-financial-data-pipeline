# AWS Financial Data Pipeline

A batch data pipeline for card-style financial transactions. Daily CSV files land in S3, get cleaned, validated, PII-protected, modelled into a star schema, and published as Parquet that is queried with SQL in Athena.

Python, S3, Lambda, Glue, Step Functions, Athena, Terraform. 190 automated tests. The same transform code runs on a laptop (local folder) or on AWS (S3), so the whole pipeline can be exercised without an AWS account.

> **Status, stated plainly.** The data pipeline, SQL and Lambda code are fully tested locally (including against mocked S3 and SSM). The Terraform has been parsed and statically checked but **has not been applied to a real AWS account**, and the Glue job has not been run on real Glue. See [Verification status](#verification-status) before relying on any AWS claim.

## Architecture

```
 daily CSV
     |
     v
 s3://lake/landing/            S3 event
     |  ------------------>  ingest Lambda
     |                          | validates file name + header (ranged GET)
     |                          | copies to immutable raw/, rejects bad files -> quarantine/rejected_files + SNS
     |                          v
     |                    Step Functions (execution name = date + ETag, so duplicate events are no-ops)
     |                          |
     |                          v
     |                    Glue Python shell job  (retries, waits if another run is active)
     |                          |  raw -> clean -> curated
     |                          v
     |                    quality gate Lambda  (reads audit summary, checks run_id)
     |                          |
     |                 ok ------+------ degraded -> SNS alert (run still completes)
     |                          v
 s3://lake/                     Athena (Glue Catalog tables, partition projection, 1 GB scan cutoff)
   raw/dt=YYYY-MM-DD/           immutable source of truth
   clean/dt=.../                valid rows, typed
   curated/fact_*, dim_*        star schema Parquet
   quarantine/dt=.../           rejected rows with reasons
   audit/                       one JSON per run, success or failure
```

Failures at any step go to an SNS topic (email) and CloudWatch alarms.

## What the pipeline does

1. **Ingest.** Checks the file name (`transactions_YYYY-MM-DD.csv`) and header, copies the file to `raw/` without ever overwriting it. A corrected file for the same date is stored beside the original (`__v<etag>`), not over it.
2. **Clean.** Trims whitespace, normalises case, strips currency symbols and thousands separators, parses flexible timestamp formats. Cleaning never drops rows.
3. **Validate.** 15 rules (table below). Bad rows go to `quarantine/` with a `primary_reason` and every reason that applied. Duplicates are judged only among otherwise-valid rows, so a bad copy never causes a good row to be thrown away.
4. **Protect PII.** Account numbers become an HMAC-SHA256 key (`account_key`) plus `account_last4`. Raw account numbers and emails never reach clean, curated or quarantine.
5. **Flag fraud.** Three explainable rules, not a model: high amount (>= 5000 USD), velocity (>= 5 transactions in 600 seconds per account), night high value (01:00 to 04:59 and >= 1000 USD).
6. **Model.** Star schema: `fact_transactions`, `dim_account`, `dim_merchant`, `dim_date`. Money is `DECIMAL(18,2)` using `ROUND_HALF_UP`, converted to USD at fixed documented rates.
7. **Reconcile.** Row counts, an amount control total to the cent, unique ids, foreign key integrity and a no-PII check. **If any check fails, nothing is written to curated.** The audit record is still written.
8. **Report.** Athena SQL for volume, merchants, fraud, data quality, rolling windows and spend quartiles.

### Validation rules

| Rule | Meaning |
|---|---|
| missing_transaction_id | transaction_id is empty |
| missing_account_number | account_number is empty |
| invalid_account_number | account_number is not 12 to 19 digits |
| missing_merchant_id | merchant_id is empty |
| missing_amount | amount is empty |
| invalid_amount | amount is present but not a finite number |
| non_positive_amount | amount is zero or negative (refunds are positive with txn_type REFUND) |
| missing_timestamp | txn_timestamp is empty |
| invalid_timestamp | does not match YYYY-MM-DD HH:MM:SS |
| future_timestamp | after the end of the batch date |
| stale_timestamp | older than the 7 day late-arrival window |
| invalid_currency | not in the supported set |
| invalid_channel | not in the supported set |
| invalid_txn_type | not in the supported set |
| duplicate_transaction_id | an earlier valid row already has this id |

## Run it locally (no AWS needed)

```bash
python -m venv .venv && source .venv/bin/activate
make install
make test        # 190 tests
make demo        # 7 days of synthetic data through the whole pipeline, then SQL on DuckDB
```

Individual commands:

```bash
finpipe backfill --start 2026-10-01 --end 2026-10-07 --rows 5000
finpipe summary
finpipe query 06                # any file in sql/analytics by prefix
finpipe --lake s3://my-bucket/prefix run --date 2026-10-03
```

### Real output from `make demo`

```
        dt    status  raw_rows  clean_rows  quarantined_rows  quarantine_rate  flagged_rows gross_usd
2026-10-01 SUCCEEDED      5050        4800               250           0.0495            53 782287.92
2026-10-02 SUCCEEDED      5050        4800               250           0.0495            53 801235.86
2026-10-03 SUCCEEDED      5050        4800               250           0.0495            50 756817.36
...
```

Each day is 5,000 generated rows plus 1% duplicates. The generator injects exactly 200 defects (4%) and 50 duplicates, and the tests assert that the pipeline quarantines exactly 250. That ground-truth check is how I know the validation catches what it should and nothing else. The synthetic data is for demonstration only.

## Deploy to AWS

Requirements: AWS credentials, Terraform >= 1.5, Python 3.9+.

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # set alert_email
cd ../..
make tf-init
make tf-plan          # builds build/finpipe.zip and build/lambda first
make tf-apply         # confirm the SNS subscription email
make upload-sample    # uploads synthetic CSVs to landing/, which triggers the pipeline
make athena-views     # after the first successful run
make tf-destroy       # remove everything when done
```

## Design decisions

- **Idempotent by construction.** Each date partition is written as a deterministic `part-0.parquet` (write first, then delete stale files). Re-running a day gives identical output, never duplicates. The Step Functions execution name is the date plus the S3 ETag, so a repeated S3 event cannot start a second run.
- **Immutable raw layer.** Everything downstream can be rebuilt from `raw/`. The Glue role has no write access to it (checked by a test).
- **Quarantine, not delete.** Every rejected row is kept with its reasons, so data quality is measurable (query 07) and fixable upstream.
- **Reconciliation blocks bad writes.** A silent row loss is worse than a failed run.
- **HMAC, not plain hashing.** A plain SHA-256 of a 16 digit number can be brute-forced. The key is a random value held in SSM Parameter Store (SecureString).
- **Explicit Glue tables with partition projection, no crawler.** Schemas are reviewed code, and partitions need no `MSCK REPAIR`.
- **Same code on laptop and AWS.** A `Storage` interface has local and S3 implementations, so the full pipeline runs in unit tests.
- **Least-privilege IAM.** A static test fails the build if any Allow statement uses a wildcard action, or a wildcard resource other than log delivery.
- **Lambda stays small.** The Lambdas use only boto3 and a dependency-free schema module, and a test checks that pandas, numpy and pyarrow are never imported there.

## Cost

Designed for small volumes: S3 and Parquet storage, one Glue Python shell DPU per run, on-demand Athena capped at 1 GB scanned per query, Lambda and Step Functions at free-tier scale. A daily 5,000 row file should cost cents per month. A budget alarm is created (`monthly_budget_usd`). I have not measured the real bill. Treat this as an estimate and check the AWS pricing pages for your region.

## Verification status

| Area | Status |
|---|---|
| Cleaning, validation, PII, fraud, modelling, reconciliation, pipeline | Tested (unit and end to end, local and mocked S3) |
| Athena SQL | Run on DuckDB over the same Parquet. **Not run on real Athena**, so small dialect differences are possible |
| Lambdas | Tested with moto and fakes. Not deployed |
| Glue job entry point | Tested locally with Glue-style arguments. **Not run on real Glue** (Python 3.9 shell, pip pins, memory untested) |
| Terraform | Parsed with python-hcl2, references and IAM statically checked. **Never run through `terraform validate`, `plan` or `apply`.** CI runs `validate` |
| Step Functions definition | Structure checked (reachable states, terminations, error handling). Never executed |

## Limitations

- The Glue job is a single worker running pandas. It fits tens of millions of rows a day, not billions. Beyond that, move the transform to Spark (Glue ETL or EMR); the module boundaries (clean, validate, model) were kept separate to make that port straightforward.
- `max_concurrent_runs = 1` because dimension upserts are read-modify-write. A multi-day backfill therefore queues in Step Functions retries rather than running in parallel.
- Dimensions are SCD type 1 (latest value wins). There is no history.
- FX rates are fixed constants, not a live feed.
- Fraud rules are static thresholds, with no model and no feedback loop.
- The Terraform state contains the generated PII salt. Use an encrypted remote backend with restricted access for anything beyond a demo.
- Alerting goes to one email address; there is no on-call integration.

## Possible next steps

- Run `terraform apply` in a sandbox account and fix whatever real AWS finds.
- Iceberg tables for schema evolution and row-level corrections.
- A dbt layer on Athena for the reporting models.
- Data contracts with the file producer, and an SCD type 2 account dimension.

## Layout

```
src/finpipe/      pipeline code (cleaning, validation, pii, fraud, modeling, reconciliation, storage, cli)
lambdas/          ingest and quality gate handlers
glue/             Glue job entry point
sql/              analytics queries and Athena views
scripts/          artifact build, sample upload, view creation
infra/terraform/  all AWS resources
tests/            190 tests
```
