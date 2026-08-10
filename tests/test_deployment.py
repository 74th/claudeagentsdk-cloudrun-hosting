from pathlib import Path

import pytest

from cas_hosting_adapter.release_config import ReleaseConfig
from cas_hosting_adapter.terraform_validation import validate


def test_terraform_has_required_deployment_safeguards() -> None:
    validate(Path("terraform"))
    main_tf = Path("terraform/main.tf").read_text()
    assert "aiplatform.googleapis.com" in main_tf
    assert "roles/aiplatform.user" in main_tf
    assert "CLAUDE_CODE_USE_VERTEX" in main_tf
    assert "FIRESTORE_DATABASE" in main_tf
    assert 'name        = var.firestore_database' in main_tf
    assert 'name        = "(default)"' not in main_tf
    assert main_tf.count("lifecycle_rule {") == 1
    assert "condition { age = var.retention_days }" in main_tf
    assert "matches_prefix" not in main_tf
    assert 'value = tostring(var.retention_days)' in main_tf
    assert main_tf.count('field      = "expires_at"') == 3
    assert 'member = local.gke_principal' in main_tf
    assert 'resource "kubernetes_namespace_v1" "agent"' in main_tf
    assert 'resource "kubernetes_service_account_v1" "agent"' in main_tf
    assert 'projects/${data.google_project.current.number}' in main_tf
    assert 'iam.gke.io/gcp-service-account' not in main_tf
    assert 'resource "google_service_account_key"' not in main_tf


def test_release_config_rejects_unknown_and_unsafe_job_values() -> None:
    with pytest.raises(Exception):
        ReleaseConfig(
            project_id="p", region="us-central1", firestore_location="us-central1",
            firestore_database="claude-agent-chat", bucket_name="b", image="i", task_retries=1,
        )
    with pytest.raises(Exception):
        ReleaseConfig.model_validate(
            {
                "project_id": "p", "region": "us-central1",
                "firestore_location": "us-central1", "bucket_name": "b",
                "firestore_database": "claude-agent-chat", "image": "i", "token": "forbidden",
            }
        )
