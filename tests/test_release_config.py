from pathlib import Path

import pytest

from cas_hosting_adapter.release_config import ReleaseConfig, load_release_config


def test_example_release_config_is_valid() -> None:
    config = load_release_config(Path("release.example.yaml"))
    assert config.schema_version == "3"
    assert config.retention_days == 30
    assert config.question_timeout_seconds == 300
    assert config.terraform_variables()["job_name"] == "test-claudesdk-cloudrun"
    assert config.terraform_variables()["firestore_database"] == "claude-agent-chat"
    assert config.terraform_variables()["vertex_region"] == "us-east5"
    assert config.terraform_variables()["claude_model"] == "claude-haiku-4-5@20251001"
    assert config.terraform_variables()["retention_days"] == 30
    assert config.terraform_variables()["question_timeout_seconds"] == 300
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


def test_release_config_rejects_unsupported_platform_and_mixed_settings() -> None:
    common = {
        "project_id": "p",
        "region": "us-central1",
        "firestore_location": "us-central1",
        "firestore_database": "claude-agent-chat",
        "bucket_name": "bucket",
        "image": "image",
        "schema_version": "3",
    }
    with pytest.raises(ValueError, match="not implemented"):
        ReleaseConfig(**common, execution_platform="gke")
    with pytest.raises(ValueError, match="must not contain"):
        ReleaseConfig(**common, execution_platform="cloud-batch", job_name="run")


def test_gke_release_config_keeps_cluster_location_independent() -> None:
    config = ReleaseConfig(
        schema_version="4",
        project_id="nnyn-dev",
        region="us-central1",
        firestore_location="us-central1",
        firestore_database="claude-agent-chat",
        bucket_name="bucket",
        image="image",
        execution_platform="gke",
        enable_cloud_run=True,
        enable_cloud_batch=True,
        enable_gke=True,
        gke={
            "cluster": "autopilot",
            "cluster_region": "asia-northeast1",
            "namespace": "claude-agent",
            "ksa_name": "claude-agent",
            "kube_context": "gke_nnyn-dev_asia-northeast1_autopilot",
        },
    )
    variables = config.terraform_variables()
    assert variables["gke_cluster_region"] == "asia-northeast1"
    assert variables["region"] == "us-central1"
    assert variables["enable_gke"] is True


def test_gke_requires_its_enable_flag_and_rejects_secrets() -> None:
    common = {
        "schema_version": "4",
        "project_id": "p",
        "region": "us-central1",
        "firestore_location": "us-central1",
        "firestore_database": "claude-agent-chat",
        "bucket_name": "bucket",
        "image": "image",
        "execution_platform": "gke",
        "gke": {
            "cluster": "autopilot",
            "cluster_region": "asia-northeast1",
            "kube_context": "context",
        },
    }
    with pytest.raises(ValueError, match="requires enable_gke"):
        ReleaseConfig(**common)
    with pytest.raises(ValueError):
        secret_values = dict(common)
        secret_values["enable_gke"] = True
        secret_values["gke"] = {**common["gke"], "api_key": "secret"}
        ReleaseConfig(**secret_values)


def test_release_config_keeps_both_terraform_platforms_enabled_by_default() -> None:
    config = ReleaseConfig(
        project_id="p",
        region="us-central1",
        firestore_location="us-central1",
        firestore_database="claude-agent-chat",
        bucket_name="bucket",
        image="image",
        execution_platform="cloud-batch",
    )
    variables = config.terraform_variables()
    assert variables["enable_cloud_run"] is True
    assert variables["enable_cloud_batch"] is True


def test_release_config_rejects_runtime_platform_when_disabled() -> None:
    with pytest.raises(ValueError, match="requires enable_cloud_batch"):
        ReleaseConfig(
            project_id="p",
            region="us-central1",
            firestore_location="us-central1",
            firestore_database="claude-agent-chat",
            bucket_name="bucket",
            image="image",
            execution_platform="cloud-batch",
            enable_cloud_batch=False,
        )
