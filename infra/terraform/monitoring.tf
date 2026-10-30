resource "aws_cloudwatch_metric_alarm" "pipeline_failed" {
  alarm_name          = "${local.name_prefix}-pipeline-execution-failed"
  alarm_description   = "A Step Functions pipeline execution failed."
  namespace           = "AWS/States"
  metric_name         = "ExecutionsFailed"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    StateMachineArn = aws_sfn_state_machine.pipeline.arn
  }
}

resource "aws_cloudwatch_metric_alarm" "ingest_errors" {
  alarm_name          = "${local.name_prefix}-ingest-lambda-errors"
  alarm_description   = "The ingest Lambda raised an error."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    FunctionName = aws_lambda_function.ingest.function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "ingest_dlq_not_empty" {
  alarm_name          = "${local.name_prefix}-ingest-dlq-not-empty"
  alarm_description   = "A file event could not be processed and was parked in the dead-letter queue."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    QueueName = aws_sqs_queue.ingest_dlq.name
  }
}

# Freshness: alert if a whole day passes without a successful run. Off by default so a
# sandbox that only receives files occasionally does not alarm constantly.
resource "aws_cloudwatch_metric_alarm" "no_successful_run" {
  count = var.alarm_on_missing_daily_run ? 1 : 0

  alarm_name          = "${local.name_prefix}-no-successful-run-24h"
  alarm_description   = "No pipeline execution succeeded in the last 24 hours (missing source file?)."
  namespace           = "AWS/States"
  metric_name         = "ExecutionsSucceeded"
  statistic           = "Sum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    StateMachineArn = aws_sfn_state_machine.pipeline.arn
  }
}

# Cost guard. The budget covers the whole account, which is the right scope for a sandbox account.
resource "aws_budgets_budget" "monthly" {
  count = var.monthly_budget_usd > 0 ? 1 : 0

  name         = "${local.name_prefix}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}
