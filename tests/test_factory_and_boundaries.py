from pathlib import Path

import pytest

from cas_hosting_adapter.factory import GoogleCloudSettings


def test_google_cloud_settings_rejects_cross_region_job_name() -> None:
    with pytest.raises(ValueError):
        GoogleCloudSettings(
            project="project", region="us-central1", firestore_database="claude-agent-chat",
            bucket_name="bucket", job_name="projects/project/locations/europe-west1/jobs/job",
        )


def test_core_public_modules_do_not_import_google_sdks() -> None:
    root = Path("cas_hosting_adapter")
    for path in (root / "models.py", root / "lifecycle.py", root / "control_client.py"):
        assert "google.cloud" not in path.read_text()
