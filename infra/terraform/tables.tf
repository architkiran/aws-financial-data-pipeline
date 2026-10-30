# Athena tables are defined here (not discovered by a crawler) so the schema is explicit,
# versioned, and cannot drift when a source file has a quirk. Partition projection lets
# Athena compute dt=YYYY-MM-DD partitions itself: no crawler, no MSCK REPAIR, no cost.
#
# The column lists below must match src/finpipe/schema.py; tests/test_terraform_schema.py
# fails the build if they drift.

locals {
  table_columns = {
    fact_transactions = [
      { name = "transaction_id", type = "string" },
      { name = "date_key", type = "int" },
      { name = "account_key", type = "string" },
      { name = "merchant_id", type = "string" },
      { name = "txn_type", type = "string" },
      { name = "channel", type = "string" },
      { name = "country", type = "string" },
      { name = "currency", type = "string" },
      { name = "amount", type = "decimal(18,2)" },
      { name = "amount_usd", type = "decimal(18,2)" },
      { name = "txn_timestamp", type = "timestamp" },
      { name = "flag_high_amount", type = "boolean" },
      { name = "flag_velocity", type = "boolean" },
      { name = "flag_night_high_value", type = "boolean" },
      { name = "fraud_score", type = "int" },
      { name = "is_flagged", type = "boolean" },
      { name = "run_id", type = "string" },
    ]
    dim_account = [
      { name = "account_key", type = "string" },
      { name = "account_last4", type = "string" },
      { name = "first_seen_date", type = "date" },
      { name = "last_seen_date", type = "date" },
    ]
    dim_merchant = [
      { name = "merchant_id", type = "string" },
      { name = "merchant_name", type = "string" },
      { name = "merchant_category", type = "string" },
    ]
    dim_date = [
      { name = "date_key", type = "int" },
      { name = "full_date", type = "date" },
      { name = "year", type = "int" },
      { name = "quarter", type = "int" },
      { name = "month", type = "int" },
      { name = "month_name", type = "string" },
      { name = "day", type = "int" },
      { name = "day_of_week", type = "int" },
      { name = "day_name", type = "string" },
      { name = "is_weekend", type = "boolean" },
    ]
    quarantine_transactions = [
      { name = "transaction_id", type = "string" },
      { name = "account_key", type = "string" },
      { name = "merchant_id", type = "string" },
      { name = "merchant_name", type = "string" },
      { name = "merchant_category", type = "string" },
      { name = "txn_type", type = "string" },
      { name = "amount", type = "string" },
      { name = "currency", type = "string" },
      { name = "txn_timestamp", type = "string" },
      { name = "channel", type = "string" },
      { name = "country", type = "string" },
      { name = "source_file", type = "string" },
      { name = "source_row", type = "int" },
      { name = "primary_reason", type = "string" },
      { name = "all_reasons", type = "string" },
      { name = "run_id", type = "string" },
    ]
  }

  # Tables partitioned by batch date (dt=YYYY-MM-DD) and their S3 prefix.
  partitioned_tables = {
    fact_transactions       = "curated/fact_transactions"
    quarantine_transactions = "quarantine/transactions"
  }

  # Small tables that are rewritten whole.
  reference_tables = {
    dim_account  = "curated/dim_account"
    dim_merchant = "curated/dim_merchant"
    dim_date     = "curated/dim_date"
  }

  parquet_input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
  parquet_output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"
  parquet_serde         = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
}

resource "aws_glue_catalog_table" "partitioned" {
  for_each = local.partitioned_tables

  name          = each.key
  database_name = aws_glue_catalog_database.lake.name
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    classification              = "parquet"
    "parquet.compression"       = "SNAPPY"
    "projection.enabled"        = "true"
    "projection.dt.type"        = "date"
    "projection.dt.format"      = "yyyy-MM-dd"
    "projection.dt.range"       = "${var.data_start_date},NOW"
    "storage.location.template" = "s3://${aws_s3_bucket.lake.bucket}/${each.value}/dt=$${dt}"
  }

  partition_keys {
    name = "dt"
    type = "string"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.lake.bucket}/${each.value}/"
    input_format  = local.parquet_input_format
    output_format = local.parquet_output_format

    ser_de_info {
      serialization_library = local.parquet_serde
      parameters = {
        "serialization.format" = "1"
      }
    }

    dynamic "columns" {
      for_each = local.table_columns[each.key]

      content {
        name = columns.value.name
        type = columns.value.type
      }
    }
  }
}

resource "aws_glue_catalog_table" "reference" {
  for_each = local.reference_tables

  name          = each.key
  database_name = aws_glue_catalog_database.lake.name
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    classification        = "parquet"
    "parquet.compression" = "SNAPPY"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.lake.bucket}/${each.value}/"
    input_format  = local.parquet_input_format
    output_format = local.parquet_output_format

    ser_de_info {
      serialization_library = local.parquet_serde
      parameters = {
        "serialization.format" = "1"
      }
    }

    dynamic "columns" {
      for_each = local.table_columns[each.key]

      content {
        name = columns.value.name
        type = columns.value.type
      }
    }
  }
}
