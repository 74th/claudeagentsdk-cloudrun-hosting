from uuid import uuid4

import pytest

from cas_hosting_adapter.errors import (
    ConfigurationError,
    ExecutionConflictError,
    ExecutionNotFoundError,
    ExecutionPermissionError,
    ExecutionQuotaError,
    ExecutionTemporaryError,
    ValidationError,
)
from cas_hosting_adapter.gke_backend import (
    GKEJobsBackend,
    GKEToleration,
    normalize_gke_job_state,
)
from cas_hosting_adapter.models import ExecutionReference, ExecutionState


class ApiError(Exception):
    def __init__(self, status: int, reason: str = "") -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


class FakeKubernetesClient:
    def __init__(self) -> None:
        self.jobs: dict[tuple[str, str], dict[str, object]] = {}
        self.created: list[dict[str, object]] = []
        self.deleted: list[tuple[str, str, str]] = []
        self.create_error: Exception | None = None
        self.get_error: Exception | None = None

    def create_job(self, *, namespace: str, body: dict[str, object]) -> object:
        if self.create_error is not None:
            raise self.create_error
        name = str(body["metadata"]["name"])  # type: ignore[index]
        self.created.append(body)
        job = {"metadata": body["metadata"], "status": {}}
        self.jobs[(namespace, name)] = job
        return job

    def get_job(self, *, namespace: str, name: str) -> object:
        if self.get_error is not None:
            raise self.get_error
        try:
            return self.jobs[(namespace, name)]
        except KeyError as error:
            raise ApiError(404, "not found") from error

    def delete_job(self, *, namespace: str, name: str, propagation_policy: str) -> object:
        self.deleted.append((namespace, name, propagation_policy))
        self.jobs.pop((namespace, name), None)
        return object()


def backend(
    client: FakeKubernetesClient,
    tolerations: tuple[GKEToleration, ...] = (),
) -> GKEJobsBackend:
    return GKEJobsBackend(
        client,
        image="image@sha256:digest",
        cancel_poll_attempts=2,
        cancel_poll_interval_seconds=0,
        environment={"GOOGLE_CLOUD_PROJECT": "project"},
        tolerations=tolerations,
    )


def test_job_manifest_is_single_non_retrying_run_with_safe_environment() -> None:
    client = FakeKubernetesClient()
    run_id = uuid4()
    reference = backend(client).start(run_id)
    manifest = client.created[0]
    spec = manifest["spec"]  # type: ignore[index]
    template = spec["template"]  # type: ignore[index]
    pod = template["spec"]  # type: ignore[index]
    container = pod["containers"][0]  # type: ignore[index]

    assert reference.name == f"claude-agent/{reference.name.rsplit('/', 1)[-1]}"
    assert spec["parallelism"] == 1  # type: ignore[index]
    assert spec["completions"] == 1  # type: ignore[index]
    assert spec["backoffLimit"] == 0  # type: ignore[index]
    assert pod["serviceAccountName"] == "claude-agent"  # type: ignore[index]
    assert container["resources"]["requests"] == {"cpu": "1", "memory": "2Gi"}  # type: ignore[index]
    env = {item["name"]: item["value"] for item in container["env"]}  # type: ignore[index]
    assert env == {
        "GOOGLE_CLOUD_PROJECT": "project",
        "RUN_ID": str(run_id),
        "CLOUD_RUN_EXECUTION": reference.name,
    }
    assert manifest["metadata"]["labels"]["run-id"] == str(run_id)  # type: ignore[index]
    assert "tolerations" not in pod  # type: ignore[operator]


def test_job_manifest_preserves_multiple_tolerations_and_exists_value() -> None:
    client = FakeKubernetesClient()
    tolerations = (
        GKEToleration("dedicated", "Exists", "", "NoSchedule"),
        GKEToleration("workload", "Equal", "agent", "PreferNoSchedule"),
    )
    backend(client, tolerations).start(uuid4())
    pod = client.created[0]["spec"]["template"]["spec"]  # type: ignore[index]
    assert pod["tolerations"] == [  # type: ignore[index]
        {"key": "dedicated", "operator": "Exists", "value": "", "effect": "NoSchedule"},
        {
            "key": "workload",
            "operator": "Equal",
            "value": "agent",
            "effect": "PreferNoSchedule",
        },
    ]


@pytest.mark.parametrize(
    "toleration",
    [
        GKEToleration("key", "Equal", "value", "NoSchedule"),
    ],
)
def test_gke_toleration_is_immutable(toleration: GKEToleration) -> None:
    with pytest.raises(AttributeError):
        toleration.key = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "values",
    [
        ("key", "In", "", "NoSchedule"),
        ("key", "Equal", "", "Invalid"),
        ("key", "Exists", "agent", "NoSchedule"),
    ],
)
def test_gke_toleration_defensively_rejects_invalid_values(
    values: tuple[str, str, str, str],
) -> None:
    with pytest.raises(ConfigurationError):
        GKEToleration(*values)


def test_backend_rejects_non_value_object_tolerations_before_create() -> None:
    client = FakeKubernetesClient()
    with pytest.raises(ConfigurationError, match="GKEToleration"):
        GKEJobsBackend(
            client,
            image="image@sha256:digest",
            tolerations=(  # type: ignore[arg-type]
                {"key": "dedicated", "operator": "In", "value": "", "effect": "NoSchedule"},
            ),
        )
    assert client.created == []


def test_start_is_idempotent_and_conflict_recovers_same_run() -> None:
    client = FakeKubernetesClient()
    gke = backend(client)
    run_id = uuid4()
    first = gke.start(run_id)
    assert gke.start(run_id) == first
    assert len(client.created) == 1

    other = backend(FakeKubernetesClient())
    other_client = other._client  # type: ignore[attr-defined]
    other_client.create_error = ApiError(409, "already exists")  # type: ignore[attr-defined]
    other_client.jobs[("claude-agent", other.job_name_for_run(run_id))] = {  # type: ignore[attr-defined]
        "metadata": {"labels": {"run-id": str(run_id)}}
    }
    assert other.start(run_id).identity == str(run_id)


def test_conflict_with_a_different_run_is_not_silently_reused() -> None:
    client = FakeKubernetesClient()
    run_id = uuid4()
    client.create_error = ApiError(409, "already exists")
    client.jobs[("claude-agent", f"claude-agent-{run_id.hex}")] = {
        "metadata": {"labels": {"run-id": str(uuid4())}}
    }
    with pytest.raises(ExecutionConflictError):
        backend(client).start(run_id)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ({}, ExecutionState.PENDING),
        ({"active": 1}, ExecutionState.RUNNING),
        ({"succeeded": 1}, ExecutionState.SUCCEEDED),
        ({"failed": 1}, ExecutionState.FAILED),
        ({"conditions": [{"type": "Complete", "status": "True"}]}, ExecutionState.SUCCEEDED),
        ({"conditions": [{"type": "Failed", "status": "True"}]}, ExecutionState.FAILED),
    ],
)
def test_job_states_are_normalized(status: dict[str, object], expected: ExecutionState) -> None:
    assert normalize_gke_job_state({"status": status}) is expected


def test_cancel_uses_foreground_deletion_and_is_idempotent() -> None:
    client = FakeKubernetesClient()
    gke = backend(client)
    reference = gke.start(uuid4())
    assert gke.cancel(reference) is ExecutionState.CANCELLED
    assert gke.cancel(reference) is ExecutionState.CANCELLED
    assert client.deleted == [("claude-agent", reference.name.rsplit("/", 1)[1], "Foreground")]


@pytest.mark.parametrize(
    ("error", "error_type"),
    [
        (ApiError(404, "not found"), ExecutionNotFoundError),
        (ApiError(401, "unauthorized"), ExecutionPermissionError),
        (ApiError(403, "forbidden"), ExecutionPermissionError),
        (ApiError(429, "quota"), ExecutionQuotaError),
        (ApiError(503, "unavailable"), ExecutionTemporaryError),
        (ApiError(400, "invalid"), ValidationError),
    ],
)
def test_kubernetes_errors_use_domain_errors(error: Exception, error_type: type[Exception]) -> None:
    client = FakeKubernetesClient()
    client.get_error = error
    reference = ExecutionReference(backend="gke", name="claude-agent/job", identity=str(uuid4()))
    with pytest.raises(error_type):
        backend(client).get(reference)


def test_reference_for_another_backend_is_rejected() -> None:
    with pytest.raises(ValidationError):
        backend(FakeKubernetesClient()).get(ExecutionReference(backend="cloud-batch", name="job"))
