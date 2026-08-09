variable "project_id" { type = string }
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
variable "image" { type = string }
variable "task_timeout_seconds" {
  type    = number
  default = 1800
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
variable "snapshot_retention_days" {
  type    = number
  default = 30
}
variable "uncommitted_retention_days" {
  type    = number
  default = 1
}
variable "run_retention_days" {
  type        = number
  description = "Shared Firestore session, run, and event retention in days."
  default     = 30
  validation {
    condition     = var.run_retention_days >= 1
    error_message = "run_retention_days must be at least one day."
  }
}
