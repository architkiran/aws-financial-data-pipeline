data "aws_caller_identity" "current" {}

locals {
  name_prefix     = "${var.project}-${var.environment}"
  bucket_name     = "${local.name_prefix}-lake-${data.aws_caller_identity.current.account_id}"
  bucket_arn      = "arn:aws:s3:::${local.bucket_name}"
  glue_database   = replace("${local.name_prefix}_db", "-", "_")
  glue_job_name   = "${local.name_prefix}-transform"
  workgroup_name  = "${local.name_prefix}-wg"
  pii_salt_param  = "/${var.project}/${var.environment}/pii_salt"
  artifacts_local = "${path.module}/../../build"
}
