variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Short project name, used as a prefix for every resource."
  type        = string
  default     = "finpipe"
}

variable "environment" {
  description = "Environment name (dev, prod, ...)."
  type        = string
  default     = "dev"
}

variable "alert_email" {
  description = "Email address for pipeline alerts and budget notifications. AWS sends a confirmation link you must click."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alert_email))
    error_message = "alert_email must be a valid email address."
  }
}

variable "monthly_budget_usd" {
  description = "Monthly AWS cost budget in USD (account-wide). Set to 0 to skip creating the budget."
  type        = number
  default     = 5
}

variable "max_quarantine_rate" {
  description = "A run that quarantines more than this fraction of its rows is reported as degraded."
  type        = number
  default     = 0.15
}

variable "glue_max_capacity" {
  description = "DPUs for the Glue Python shell job: 1 (16 GB memory) or 0.0625 (1 GB, only for very small files)."
  type        = number
  default     = 1

  validation {
    condition     = contains([0.0625, 1], var.glue_max_capacity)
    error_message = "A Glue Python shell job supports either 0.0625 or 1 DPU."
  }
}

variable "data_start_date" {
  description = "Earliest batch date Athena partition projection will consider (YYYY-MM-DD)."
  type        = string
  default     = "2024-01-01"
}

variable "landing_retention_days" {
  description = "Days to keep files in landing/ (raw/ holds the permanent copy)."
  type        = number
  default     = 14
}

variable "log_retention_days" {
  description = "CloudWatch log retention."
  type        = number
  default     = 30
}

variable "alarm_on_missing_daily_run" {
  description = "Alarm if no pipeline execution succeeded in the last 24 hours (freshness check). Enable once files arrive daily."
  type        = bool
  default     = false
}
