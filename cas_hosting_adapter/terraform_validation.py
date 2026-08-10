"""Static deployment guardrails in addition to terraform validate."""

import re
from pathlib import Path


def validate(path: Path) -> None:
    text = (path / "main.tf").read_text()
    required = (
        "google_cloud_run_v2_job",
        "max_retries",
        "google_firestore_index",
        "google_firestore_field",
        "ttl_config",
        "retention_days",
        "lifecycle_rule",
        "google_service_account",
        "FIRESTORE_DATABASE",
        "google_firestore_database.chat.name",
        "var.firestore_database",
        "batch.googleapis.com",
        "roles/batch.jobsEditor",
        "roles/iam.serviceAccountUser",
        "enable_cloud_run",
        "enable_cloud_batch",
        "enable_gke",
        "kubernetes_namespace_v1",
        "kubernetes_service_account_v1",
        "data \"google_project\" \"current\"",
        "principal://iam.googleapis.com/projects/${data.google_project.current.number}",
        'google_storage_bucket_iam_member" "gke_workspace',
    )
    missing = [value for value in required if value not in text]
    if missing:
        raise ValueError(f"missing Terraform safeguards: {', '.join(missing)}")
    if any(
        legacy in text
        for legacy in (
            "run_retention_days",
            "snapshot_retention_days",
            "uncommitted_retention_days",
        )
    ):
        raise ValueError("Terraform must use only the shared retention_days variable")
    if text.count("lifecycle_rule {") != 1 or "matches_prefix" in text:
        raise ValueError("GCS must have one unscoped retention lifecycle rule")
    if "condition { age = var.retention_days }" not in text:
        raise ValueError("GCS lifecycle must use retention_days for every object")
    if not re.search(r"count\s*=\s*var\.enable_cloud_run \? 1 : 0", text):
        raise ValueError("Cloud Run resources must be conditional on enable_cloud_run")
    if not re.search(r"count\s*=\s*var\.enable_cloud_batch \? 1 : 0", text):
        raise ValueError("Batch IAM must be conditional on enable_cloud_batch")
    if text.count("max_retries     = 0") != 1:
        raise ValueError("Cloud Run task retries must remain zero")
    if "iam.gke.io/gcp-service-account" in text:
        raise ValueError("GKE KSA must not impersonate a GSA")
    if "google_service_account.gke" in text or "google_service_account_key" in text:
        raise ValueError("GKE must not create a dedicated GSA or JSON key")
