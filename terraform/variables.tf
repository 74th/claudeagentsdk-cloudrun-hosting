variable "project_id" { type = string }
variable "execution_platform" {
  type        = string
  description = "Runtime backend metadata; resource creation is controlled by enable flags."
  default     = "cloud-run"
  validation {
    condition     = contains(["cloud-run", "cloud-batch"], var.execution_platform)
    error_message = "execution_platform must be cloud-run or cloud-batch; gke is reserved."
  }
}
variable "enable_cloud_run" {
  type        = bool
  description = "Keep Cloud Run API, Job, and Cloud Run IAM available."
  default     = true
}
variable "enable_cloud_batch" {
  type        = bool
  description = "Keep Cloud Batch API and Batch IAM available."
  default     = true
}
variable "region" {
  type    = string
  default = "us-central1"
}
variable "firestore_database" {
  type        = string
  description = "Named Firestore Native database used by this deployment."
  validation {
    condition     = var.firestore_database != "(default)" && can(regex("^[a-z][a-z0-9-]{2,62}$", var.firestore_database))
    error_message = "firestore_database must be a named Firestore database ID, not (default)."
  }
}
variable "repository_id" {
  type    = string
  default = "claude-agent"
}
variable "bucket_name" { type = string }
variable "job_name" {
  type    = string
  default = "test-claudesdk-cloudrun"
}
variable "batch_job_id_prefix" {
  type        = string
  description = "Prefix for deterministic per-run Cloud Batch Job IDs."
  default     = "claude-agent"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,24}$", var.batch_job_id_prefix))
    error_message = "batch_job_id_prefix must start with a lowercase letter and be at most 25 characters."
  }
}
variable "batch_machine_type" {
  type    = string
  default = "e2-standard-2"
}
variable "batch_cpu_milli" {
  type    = number
  default = 2000
  validation {
    condition     = var.batch_cpu_milli >= 1
    error_message = "batch_cpu_milli must be positive."
  }
}
variable "batch_memory_mib" {
  type    = number
  default = 4096
  validation {
    condition     = var.batch_memory_mib >= 1
    error_message = "batch_memory_mib must be positive."
  }
}
variable "image" { type = string }
variable "task_timeout_seconds" {
  type    = number
  default = 1800
  validation {
    condition     = var.task_timeout_seconds >= 1 && var.task_timeout_seconds <= 86400
    error_message = "task_timeout_seconds must be between 1 and 86400 seconds."
  }
}
variable "vertex_region" {
  type        = string
  description = "Vertex AI region used for Claude inference (independent of Cloud Run region)."
  default     = "us-east5"
}
variable "claude_model" {
  type        = string
  description = "Claude partner model ID exposed by Vertex AI."
  default     = "claude-haiku-4-5@20251001"
}
variable "log_level" {
  type    = string
  default = "INFO"
}
variable "retention_days" {
  type        = number
  description = "Shared Firestore and GCS retention in days."
  default     = 30
  validation {
    condition     = var.retention_days >= 1
    error_message = "retention_days must be at least one day."
  }
}
