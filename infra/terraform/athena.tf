# Dedicated workgroup: query results go to a prefix that expires after 7 days, and a runaway
# query is cut off at 1 GB scanned (Athena bills per byte scanned).
resource "aws_athena_workgroup" "main" {
  name          = local.workgroup_name
  force_destroy = true

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    bytes_scanned_cutoff_per_query     = 1073741824

    engine_version {
      selected_engine_version = "Athena engine version 3"
    }

    result_configuration {
      output_location = "s3://${aws_s3_bucket.lake.bucket}/athena-results/"

      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }
  }
}

# Every file in sql/analytics becomes a saved query in the Athena console.
resource "aws_athena_named_query" "analytics" {
  for_each = fileset("${path.module}/../../sql/analytics", "*.sql")

  name      = trimsuffix(each.value, ".sql")
  workgroup = aws_athena_workgroup.main.id
  database  = aws_glue_catalog_database.lake.name
  query     = file("${path.module}/../../sql/analytics/${each.value}")
}
