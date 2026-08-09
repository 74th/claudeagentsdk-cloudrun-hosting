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
        "lifecycle_rule",
        "retention_days",
        "RUN_RETENTION_DAYS",
        "google_service_account",
    )
    missing = [value for value in required if value not in text]
    if missing:
        raise ValueError(f"missing Terraform safeguards: {', '.join(missing)}")
    if "matches_prefix" in text or text.count("lifecycle_rule {") != 1:
        raise ValueError("GCS must have one unscoped retention lifecycle rule")
    if any(
        legacy in text
        for legacy in (
            "run_retention_days",
            "snapshot_retention_days",
            "uncommitted_retention_days",
        )
    ):
        raise ValueError("Terraform must use only the shared retention_days variable")


if __name__ == "__main__":
    validate(Path("terraform"))
