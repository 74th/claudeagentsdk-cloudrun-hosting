from pathlib import Path

import pytest

from cas_hosting_adapter.release_config import ReleaseConfig, load_release_config


def test_example_release_config_is_valid() -> None:
    config = load_release_config(Path("release.example.yaml"))
    assert config.schema_version == "2"
    assert config.retention_days == 30
    assert config.terraform_variables()["job_name"] == "test-claudesdk-cloudrun"
    assert config.terraform_variables()["firestore_database"] == "claude-agent-chat"
    assert config.terraform_variables()["vertex_region"] == "us-east5"
    assert config.terraform_variables()["claude_model"] == "claude-haiku-4-5@20251001"
    assert config.terraform_variables()["retention_days"] == 30
    assert "run_retention_days" not in config.terraform_variables()


def test_release_config_defaults_and_overrides_shared_retention() -> None:
    values = {
        "project_id": "p",
        "region": "us-central1",
        "firestore_location": "us-central1",
        "firestore_database": "claude-agent-chat",
        "bucket_name": "bucket",
        "image": "image",
    }
    assert ReleaseConfig(**values).retention_days == 30
    assert ReleaseConfig(**values, retention_days=45).terraform_variables()["retention_days"] == 45


def test_release_config_rejects_legacy_retention_fields_with_migration_error() -> None:
    with pytest.raises(ValueError, match="replace them with retention_days"):
        ReleaseConfig(
            project_id="p",
            region="us-central1",
            firestore_location="us-central1",
            firestore_database="claude-agent-chat",
            bucket_name="bucket",
            image="image",
            run_retention_days=30,  # type: ignore[call-arg]
        )


def test_release_config_rejects_location_mismatch() -> None:
    with pytest.raises(ValueError, match="must match"):
        ReleaseConfig(project_id="p", region="us-central1", firestore_location="asia-northeast1",
                      firestore_database="claude-agent-chat", bucket_name="bucket", image="image")


@pytest.mark.parametrize("database", ["", "(default)", "INVALID_DATABASE"])
def test_release_config_rejects_default_or_invalid_firestore_database(database: str) -> None:
    with pytest.raises(ValueError):
        ReleaseConfig(project_id="p", region="us-central1", firestore_location="us-central1",
                      firestore_database=database, bucket_name="bucket", image="image")
