terraform {
  required_version = ">= 1.8"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 6.0" }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

resource "google_project_service" "required" {
  for_each = toset(concat(
    ["firestore.googleapis.com", "storage.googleapis.com", "artifactregistry.googleapis.com", "aiplatform.googleapis.com"],
    var.enable_cloud_run ? ["run.googleapis.com"] : [],
    var.enable_cloud_batch ? ["batch.googleapis.com"] : []
  ))
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = var.repository_id
  format        = "DOCKER"
  depends_on    = [google_project_service.required]
}

resource "google_storage_bucket" "workspace" {
  name                        = var.bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  lifecycle_rule {
    action { type = "Delete" }
    condition { age = var.retention_days }
  }
}

# Stop managing an existing default database without deleting or changing it.
# This permits a stateful upgrade to create the named chat database separately.
removed {
  from = google_firestore_database.default
  lifecycle {
    destroy = false
  }
}

removed {
  from = google_firestore_index.sessions_by_updated
  lifecycle {
    destroy = false
  }
}

removed {
  from = google_firestore_field.runs_by_id
  lifecycle {
    destroy = false
  }
}

removed {
  from = google_firestore_index.events_by_sequence
  lifecycle {
    destroy = false
  }
}

resource "google_firestore_database" "chat" {
  project     = var.project_id
  name        = var.firestore_database
  location_id = var.region
  type        = "FIRESTORE_NATIVE"
  depends_on  = [google_project_service.required]
}

resource "google_firestore_index" "chat_sessions_by_updated" {
  collection = "sessions"
  database   = google_firestore_database.chat.name
  fields {
    field_path = "updated_at"
    order      = "DESCENDING"
  }
  fields {
    field_path = "id"
    order      = "DESCENDING"
  }
}

resource "google_firestore_field" "chat_sessions_expires_at" {
  project    = var.project_id
  collection = "sessions"
  field      = "expires_at"
  database   = google_firestore_database.chat.name
  ttl_config {}
}

resource "google_firestore_field" "chat_runs_expires_at" {
  project    = var.project_id
  collection = "runs"
  field      = "expires_at"
  database   = google_firestore_database.chat.name
  ttl_config {}
}

resource "google_firestore_field" "chat_events_expires_at" {
  project    = var.project_id
  collection = "events"
  field      = "expires_at"
  database   = google_firestore_database.chat.name
  ttl_config {}
}

resource "google_firestore_index" "chat_runs_by_created" {
  collection = "runs"
  # list_runs queries the runs collection nested under one session, not the
  # collection group.  The generated Firestore error is otherwise misleading
  # because both index types use the collectionGroups API path.
  query_scope = "COLLECTION"
  database    = google_firestore_database.chat.name
  fields {
    field_path = "created_at"
    order      = "ASCENDING"
  }
  fields {
    field_path = "id"
    order      = "ASCENDING"
  }
  fields {
    field_path = "__name__"
    order      = "ASCENDING"
  }
}

# JobRunner receives only a run ID, so it must resolve nested run documents
# through a collection-group query without knowing the owning session first.
resource "google_firestore_field" "chat_runs_by_id" {
  project    = var.project_id
  collection = "runs"
  field      = "id"
  database   = google_firestore_database.chat.name
  index_config {
    indexes {
      query_scope = "COLLECTION_GROUP"
      order       = "ASCENDING"
    }
  }
}

# Event streams are queried by sequence with event ID as a stable tie-breaker.
resource "google_firestore_index" "chat_events_by_sequence" {
  collection  = "events"
  query_scope = "COLLECTION_GROUP"
  database    = google_firestore_database.chat.name
  fields {
    field_path = "sequence"
    order      = "ASCENDING"
  }
  fields {
    field_path = "id"
    order      = "ASCENDING"
  }
}

resource "google_cloud_run_v2_job" "agent" {
  count               = var.enable_cloud_run ? 1 : 0
  name                = var.job_name
  deletion_protection = false
  location            = var.region
  template {
    template {
      max_retries     = 0
      timeout         = "${var.task_timeout_seconds}s"
      service_account = google_service_account.job.email
      containers {
        image = var.image
        # Claude Agent SDK obtains short-lived credentials from this Job's service
        # account and sends inference requests to Vertex AI; no API key is injected.
        env {
          name  = "GOOGLE_CLOUD_PROJECT"
          value = var.project_id
        }
        env {
          name  = "CLAUDE_CODE_USE_VERTEX"
          value = "1"
        }
        env {
          name  = "ANTHROPIC_VERTEX_PROJECT_ID"
          value = var.project_id
        }
        env {
          name  = "CLOUD_ML_REGION"
          value = var.vertex_region
        }
        env {
          name  = "CLAUDE_MODEL"
          value = var.claude_model
        }
        env {
          name  = "LOG_LEVEL"
          value = var.log_level
        }
        env {
          name  = "WORKSPACE_BUCKET"
          value = google_storage_bucket.workspace.name
        }
        env {
          name  = "FIRESTORE_DATABASE"
          value = google_firestore_database.chat.name
        }
        env {
          name  = "RUN_RETENTION_DAYS"
          value = tostring(var.retention_days)
        }
        env {
          name  = "QUESTION_TIMEOUT_SECONDS"
          value = tostring(var.question_timeout_seconds)
        }
      }
    }
  }
  depends_on = [google_project_service.required]
}

# Batch jobs are created per run by CloudBatchBackend. Terraform manages the
# API, service identities, and shared data plane rather than a static Job.

resource "google_service_account" "control" { account_id = "claude-control" }
resource "google_service_account" "job" { account_id = "claude-job" }

resource "google_project_iam_member" "control_run_developer" {
  count   = var.enable_cloud_run ? 1 : 0
  project = var.project_id
  role    = "roles/run.developer"
  member  = "serviceAccount:${google_service_account.control.email}"
}

resource "google_project_iam_member" "control_batch_jobs_editor" {
  count   = var.enable_cloud_batch ? 1 : 0
  project = var.project_id
  role    = "roles/batch.jobsEditor"
  member  = "serviceAccount:${google_service_account.control.email}"
}

resource "google_service_account_iam_member" "control_batch_job_user" {
  count              = var.enable_cloud_batch ? 1 : 0
  service_account_id = google_service_account.job.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.control.email}"
}

resource "google_project_iam_member" "control_firestore" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.control.email}"
}

resource "google_project_iam_member" "job_firestore" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.job.email}"
}

resource "google_project_iam_member" "job_artifact_reader" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${google_service_account.job.email}"
}

# Required by the Job service account to invoke the Claude partner model through
# Vertex AI.  This is intentionally the least-privileged predefined Vertex role.
resource "google_project_iam_member" "job_vertex_ai_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.job.email}"
}

resource "google_storage_bucket_iam_member" "job_workspace" {
  bucket = google_storage_bucket.workspace.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.job.email}"
}

moved {
  from = google_cloud_run_v2_job.agent
  to   = google_cloud_run_v2_job.agent[0]
}
