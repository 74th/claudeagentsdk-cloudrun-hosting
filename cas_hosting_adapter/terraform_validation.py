"""Static deployment guardrails in addition to terraform validate."""

from pathlib import Path


def validate(path: Path) -> None:
    text = (path / "main.tf").read_text()
    required = (
        "google_cloud_run_v2_job",
        "max_retries",
        "google_firestore_index",
        "google_firestore_field",
        "ttl_config",
        "run_retention_days",
        "lifecycle_rule",
        "google_service_account",
        "FIRESTORE_DATABASE",
        "google_firestore_database.chat.name",
        "var.firestore_database",
    )
    missing = [value for value in required if value not in text]
    if missing:
        raise ValueError(f"missing Terraform safeguards: {', '.join(missing)}")
