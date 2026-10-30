# Handlers are packaged by `make package` (scripts/build_artifacts.py) into build/lambda/.
data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = "${local.artifacts_local}/lambda"
  output_path = "${local.artifacts_local}/lambda.zip"
}

resource "aws_cloudwatch_log_group" "ingest" {
  name              = "/aws/lambda/${local.name_prefix}-ingest"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "quality_gate" {
  name              = "/aws/lambda/${local.name_prefix}-quality-gate"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "ingest" {
  function_name    = "${local.name_prefix}-ingest"
  description      = "Validates a landed CSV, copies it to raw/, and starts the pipeline"
  role             = aws_iam_role.ingest.arn
  runtime          = "python3.12"
  handler          = "ingest_handler.handler"
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256
  timeout          = 60
  memory_size      = 256

  environment {
    variables = {
      STATE_MACHINE_ARN = aws_sfn_state_machine.pipeline.arn
      ALERT_TOPIC_ARN   = aws_sns_topic.alerts.arn
    }
  }

  dead_letter_config {
    target_arn = aws_sqs_queue.ingest_dlq.arn
  }

  depends_on = [
    aws_cloudwatch_log_group.ingest,
    aws_iam_role_policy_attachment.ingest_logs,
    aws_iam_role_policy.ingest,
  ]
}

# S3 invokes Lambda asynchronously: retry twice, then the event goes to the dead-letter queue.
resource "aws_lambda_function_event_invoke_config" "ingest" {
  function_name                = aws_lambda_function.ingest.function_name
  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 3600
}

resource "aws_lambda_permission" "allow_s3" {
  statement_id   = "AllowS3Invoke"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.ingest.function_name
  principal      = "s3.amazonaws.com"
  source_arn     = aws_s3_bucket.lake.arn
  source_account = data.aws_caller_identity.current.account_id
}

resource "aws_lambda_function" "quality_gate" {
  function_name    = "${local.name_prefix}-quality-gate"
  description      = "Reads the run's audit summary and reports whether data quality is acceptable"
  role             = aws_iam_role.quality_gate.arn
  runtime          = "python3.12"
  handler          = "quality_gate.handler"
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256
  timeout          = 30
  memory_size      = 128

  environment {
    variables = {
      DATA_BUCKET = local.bucket_name
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.quality_gate,
    aws_iam_role_policy_attachment.quality_gate_logs,
    aws_iam_role_policy.quality_gate,
  ]
}
