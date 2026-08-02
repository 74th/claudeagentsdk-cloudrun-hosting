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


def test_release_config_rejects_unknown_and_unsafe_job_values() -> None:
    with pytest.raises(Exception):
        ReleaseConfig(project_id="p", region="us-central1", firestore_location="us-central1",
                      bucket_name="b", image="i", task_retries=1)
    with pytest.raises(Exception):
        ReleaseConfig.model_validate({"project_id": "p", "region": "us-central1",
                                      "firestore_location": "us-central1", "bucket_name": "b",
                                      "image": "i", "token": "forbidden"})
