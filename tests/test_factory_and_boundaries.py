from pathlib import Path
from uuid import uuid4

import pytest

import cas_hosting_adapter.factory as factory
from cas_hosting_adapter import ClaudeAgentConfig, RuntimePolicy
from cas_hosting_adapter.batch_backend import CloudBatchBackend
from cas_hosting_adapter.cloud_run_backend import CloudRunJobsBackend
from cas_hosting_adapter.factory import (
    GoogleCloudClients,
    GoogleCloudJobComposition,
    GoogleCloudSettings,
)
from cas_hosting_adapter.gke_backend import GKEJobsBackend, GKEToleration


class FakeBatchClient:
    def create_job(self, **kwargs: object) -> object:
        return object()

    def get_job(self, **kwargs: object) -> object:
        return object()

    def delete_job(self, **kwargs: object) -> object:
        return object()


class FakeChatStore:
    pass


@pytest.mark.asyncio
async def test_job_composition_passes_optional_usage_hook_to_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeAdapter:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        async def run_job(self, invocation: object) -> int:
            captured["invocation"] = invocation
            return 7

    import cas_hosting_adapter.agent_adapter as agent_adapter

    monkeypatch.setattr(agent_adapter, "ClaudeAgentAdapter", FakeAdapter)
    def usage_hook(_record: object) -> None:
        pass
    composition = GoogleCloudJobComposition(
        chat_store=FakeChatStore(), workspace_store=object(), runtime_policy=RuntimePolicy()
    )

    result = await composition.run_from_environment(
        ClaudeAgentConfig(model="model"),
        usage_hook=usage_hook,
        environment={"RUN_ID": str(uuid4()), "CLOUD_RUN_EXECUTION": "execution-1"},
    )

    assert result == 7
    assert captured["usage_hook"] is usage_hook


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


@pytest.mark.parametrize("platform", ["cloud-run", "cloud-batch"])
def test_factory_injects_only_the_selected_execution_backend(
    monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    settings = GoogleCloudSettings(
        project="project",
        region="us-central1",
        firestore_database="claude-agent-chat",
        bucket_name="bucket",
        execution_platform=platform,  # type: ignore[arg-type]
        image="image",
    )
    clients = GoogleCloudClients(
        firestore=object(),
        storage=object(),
        jobs=object(),
        executions=object(),
        batch=FakeBatchClient(),
    )
    monkeypatch.setattr(factory, "create_google_cloud_clients", lambda _: clients)
    monkeypatch.setattr(factory, "FirestoreChatStore", lambda *args, **kwargs: FakeChatStore())

    client = factory.create_google_cloud_control_client(settings)
    backend = client._execution_backend
    if platform == "cloud-run":
        assert isinstance(backend, CloudRunJobsBackend)
    else:
        assert isinstance(backend, CloudBatchBackend)


def test_factory_forwards_gke_tolerations_to_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tolerations = (GKEToleration("dedicated", "Exists", "", "NoSchedule"),)
    settings = GoogleCloudSettings(
        project="project",
        region="us-central1",
        firestore_database="claude-agent-chat",
        bucket_name="bucket",
        execution_platform="gke",
        image="image",
        gke_cluster="autopilot",
        gke_cluster_region="asia-northeast1",
        gke_kube_context="context",
        gke_tolerations=tolerations,
    )
    clients = GoogleCloudClients(
        firestore=object(),
        storage=object(),
        kubernetes_batch=object(),
    )
    monkeypatch.setattr(factory, "create_google_cloud_clients", lambda _: clients)
    monkeypatch.setattr(factory, "FirestoreChatStore", lambda *args, **kwargs: FakeChatStore())

    control_client = factory.create_google_cloud_control_client(settings)

    assert isinstance(control_client._execution_backend, GKEJobsBackend)
    assert control_client._execution_backend._spec.tolerations == tolerations  # type: ignore[attr-defined]
