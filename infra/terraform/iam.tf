# Least privilege: each component gets only the S3 prefixes and APIs it actually uses.

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# ---------------------------------------------------------------- ingest Lambda
resource "aws_iam_role" "ingest" {
  name               = "${local.name_prefix}-ingest-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "ingest_logs" {
  role       = aws_iam_role.ingest.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "ingest" {
  statement {
    sid       = "ReadLandingFiles"
    actions   = ["s3:GetObject"]
    resources = ["${local.bucket_arn}/landing/*"]
  }

  statement {
    sid       = "WriteAndCheckRaw"
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${local.bucket_arn}/raw/*"]
  }

  statement {
    sid       = "WriteRejectedFiles"
    actions   = ["s3:PutObject"]
    resources = ["${local.bucket_arn}/quarantine/rejected_files/*"]
  }

  # Without s3:ListBucket, HeadObject on a missing key returns 403 instead of 404,
  # which would break the "does this raw file already exist?" check.
  statement {
    sid       = "HeadObjectNotFoundSemantics"
    actions   = ["s3:ListBucket"]
    resources = [local.bucket_arn]
  }

  statement {
    sid       = "StartPipeline"
    actions   = ["states:StartExecution"]
    resources = [aws_sfn_state_machine.pipeline.arn]
  }

  statement {
    sid       = "Alert"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
  }

  statement {
    sid       = "DeadLetter"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.ingest_dlq.arn]
  }
}

resource "aws_iam_role_policy" "ingest" {
  name   = "ingest"
  role   = aws_iam_role.ingest.id
  policy = data.aws_iam_policy_document.ingest.json
}

# ---------------------------------------------------------------- quality gate Lambda
resource "aws_iam_role" "quality_gate" {
  name               = "${local.name_prefix}-quality-gate-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "quality_gate_logs" {
  role       = aws_iam_role.quality_gate.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "quality_gate" {
  statement {
    sid       = "ReadAuditSummaries"
    actions   = ["s3:GetObject"]
    resources = ["${local.bucket_arn}/audit/latest/*"]
  }
}

resource "aws_iam_role_policy" "quality_gate" {
  name   = "quality-gate"
  role   = aws_iam_role.quality_gate.id
  policy = data.aws_iam_policy_document.quality_gate.json
}

# ---------------------------------------------------------------- Glue job
data "aws_iam_policy_document" "glue_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["glue.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "glue" {
  name               = "${local.name_prefix}-glue-job"
  assume_role_policy = data.aws_iam_policy_document.glue_assume.json
}

resource "aws_iam_role_policy_attachment" "glue_service" {
  role       = aws_iam_role.glue.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole"
}

data "aws_iam_policy_document" "glue" {
  statement {
    sid       = "ReadInputsAndArtifacts"
    actions   = ["s3:GetObject"]
    resources = ["${local.bucket_arn}/raw/*", "${local.bucket_arn}/artifacts/*"]
  }

  # Delete is needed so a re-run can remove stale files from a partition it is replacing.
  statement {
    sid     = "ReadWriteOutputs"
    actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = [
      "${local.bucket_arn}/clean/*",
      "${local.bucket_arn}/curated/*",
      "${local.bucket_arn}/quarantine/transactions/*",
      "${local.bucket_arn}/audit/*",
    ]
  }

  statement {
    sid       = "ListBucket"
    actions   = ["s3:ListBucket"]
    resources = [local.bucket_arn]
  }

  statement {
    sid       = "ReadPiiHashingKey"
    actions   = ["ssm:GetParameter"]
    resources = [aws_ssm_parameter.pii_salt.arn]
  }
}

resource "aws_iam_role_policy" "glue" {
  name   = "glue-job"
  role   = aws_iam_role.glue.id
  policy = data.aws_iam_policy_document.glue.json
}

# ---------------------------------------------------------------- Step Functions
data "aws_iam_policy_document" "sfn_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sfn" {
  name               = "${local.name_prefix}-state-machine"
  assume_role_policy = data.aws_iam_policy_document.sfn_assume.json
}

data "aws_iam_policy_document" "sfn" {
  statement {
    sid       = "RunAndWatchGlueJob"
    actions   = ["glue:StartJobRun", "glue:GetJobRun", "glue:GetJobRuns", "glue:BatchStopJobRun"]
    resources = [aws_glue_job.transform.arn]
  }

  statement {
    sid       = "InvokeQualityGate"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.quality_gate.arn]
  }

  statement {
    sid       = "Alert"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
  }

  # Required by Step Functions to deliver execution logs to CloudWatch Logs.
  statement {
    sid = "DeliverExecutionLogs"
    actions = [
      "logs:CreateLogDelivery",
      "logs:GetLogDelivery",
      "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:ListLogDeliveries",
      "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies",
      "logs:DescribeLogGroups",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "sfn" {
  name   = "state-machine"
  role   = aws_iam_role.sfn.id
  policy = data.aws_iam_policy_document.sfn.json
}
