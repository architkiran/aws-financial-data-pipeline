# Secret used to key the account-number hashes (see src/finpipe/pii.py).
# NOTE: random_password stores its value in the Terraform state file. That is acceptable for a
# personal sandbox with local state; for a shared environment create the parameter out of band
# and remove random_password so the secret never enters state.
resource "random_password" "pii_salt" {
  length  = 48
  special = false
}

resource "aws_ssm_parameter" "pii_salt" {
  name        = local.pii_salt_param
  description = "HMAC key for hashing account numbers. Changing it changes every account_key."
  type        = "SecureString"
  value       = random_password.pii_salt.result
}

resource "aws_s3_object" "glue_script" {
  bucket = aws_s3_bucket.lake.id
  key    = "artifacts/glue/transform_job.py"
  source = "${path.module}/../../glue/transform_job.py"
  etag   = filemd5("${path.module}/../../glue/transform_job.py")
}

resource "aws_s3_object" "glue_library" {
  bucket = aws_s3_bucket.lake.id
  key    = "artifacts/glue/finpipe.zip"
  source = "${local.artifacts_local}/finpipe.zip"
  etag   = filemd5("${local.artifacts_local}/finpipe.zip")
}

resource "aws_glue_catalog_database" "lake" {
  name        = local.glue_database
  description = "Curated financial transaction tables (star schema) and quarantine"
}

# Python shell job: pandas on a single worker is the right size for daily batches.
# max_concurrent_runs = 1 is deliberate: the dimension upsert is read-modify-write, so two
# runs at once could lose an update. Step Functions retries a run that hits the limit.
resource "aws_glue_job" "transform" {
  name         = local.glue_job_name
  description  = "Transforms one batch date: raw -> clean -> validated -> curated star schema"
  role_arn     = aws_iam_role.glue.arn
  glue_version = "3.0"
  max_capacity = var.glue_max_capacity
  timeout      = 30
  max_retries  = 0 # retries are handled by Step Functions; the transform is idempotent

  command {
    name            = "pythonshell"
    python_version  = "3.9"
    script_location = "s3://${aws_s3_bucket.lake.bucket}/${aws_s3_object.glue_script.key}"
  }

  execution_property {
    max_concurrent_runs = 1
  }

  default_arguments = {
    "--job-language"              = "python"
    "--extra-py-files"            = "s3://${aws_s3_bucket.lake.bucket}/${aws_s3_object.glue_library.key}"
    "--additional-python-modules" = "numpy==1.26.4,pandas==2.2.2,pyarrow==17.0.0"
    "--lake_uri"                  = "s3://${aws_s3_bucket.lake.bucket}"
    "--pii_salt_ssm_param"        = local.pii_salt_param
    "--max_quarantine_rate"       = tostring(var.max_quarantine_rate)
  }

  depends_on = [
    aws_iam_role_policy_attachment.glue_service,
    aws_iam_role_policy.glue,
  ]
}
