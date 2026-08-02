"""Static deployment guardrails in addition to terraform validate."""
from pathlib import Path


def validate(path: Path) -> None:
    text = (path / "main.tf").read_text()
    required = ("google_cloud_run_v2_job", "max_retries", "google_firestore_index",
                "lifecycle_rule", "google_service_account")
    missing = [value for value in required if value not in text]
    if missing:
        raise ValueError(f"missing Terraform safeguards: {', '.join(missing)}")


if __name__ == "__main__":
    validate(Path("terraform"))
