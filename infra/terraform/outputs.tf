output "bucket_name" {
  description = "Data lake bucket."
  value       = aws_s3_bucket.lake.bucket
}

output "landing_uri" {
  description = "Drop transactions_YYYY-MM-DD.csv files here to trigger the pipeline."
  value       = "s3://${aws_s3_bucket.lake.bucket}/landing/"
}

output "state_machine_arn" {
  value = aws_sfn_state_machine.pipeline.arn
}

output "glue_database" {
  value = aws_glue_catalog_database.lake.name
}

output "athena_workgroup" {
  value = aws_athena_workgroup.main.name
}

output "alerts_topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "apply_views_command" {
  description = "Run after the first successful pipeline run to create the Athena views."
  value       = "python scripts/apply_athena_views.py --database ${aws_glue_catalog_database.lake.name} --workgroup ${aws_athena_workgroup.main.name}"
}
