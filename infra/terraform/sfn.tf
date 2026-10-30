# Log group names under /aws/vendedlogs/ avoid CloudWatch Logs resource-policy size limits.
resource "aws_cloudwatch_log_group" "sfn" {
  name              = "/aws/vendedlogs/states/${local.name_prefix}-pipeline"
  retention_in_days = var.log_retention_days
}

resource "aws_sfn_state_machine" "pipeline" {
  name     = "${local.name_prefix}-pipeline"
  role_arn = aws_iam_role.sfn.arn

  definition = templatefile("${path.module}/pipeline.asl.json.tpl", {
    glue_job_name   = aws_glue_job.transform.name
    gate_lambda_arn = aws_lambda_function.quality_gate.arn
    alert_topic_arn = aws_sns_topic.alerts.arn
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn.arn}:*"
    include_execution_data = false
    level                  = "ERROR"
  }

  depends_on = [aws_iam_role_policy.sfn]
}
