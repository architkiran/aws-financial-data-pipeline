# Alerts. The topic is intentionally not encrypted with the AWS-managed SNS key: CloudWatch
# alarms cannot publish to a topic encrypted with an AWS-managed key. Messages contain file
# names, counts and error text only, never transaction data.
resource "aws_sns_topic" "alerts" {
  name = "${local.name_prefix}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# Events the ingest Lambda could not process after its retries end up here instead of being lost.
resource "aws_sqs_queue" "ingest_dlq" {
  name                      = "${local.name_prefix}-ingest-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}
