from types import SimpleNamespace
from uuid import uuid4

import pytest

from cas_hosting_adapter.batch_backend import CloudBatchBackend, normalize_batch_job_state
from cas_hosting_adapter.errors import (
    ExecutionNotFoundError,
    ExecutionPermissionError,
    ExecutionQuotaError,
    ExecutionTemporaryError,
    ValidationError,
)
from cas_hosting_adapter.models import ExecutionReference, ExecutionState


class FakeBatchClient:
    def __init__(self) -> None:
        self.jobs: dict[str, object] = {}
        self.created: list[dict[str, object]] = []
        self.deleted: list[str] = []
        self.get_error: Exception | None = None

    def create_job(self, **kwargs: object) -> object:
        self.created.append(kwargs)
        name = f"{kwargs['parent']}/jobs/{kwargs['job_id']}"
        self.jobs[name] = SimpleNamespace(status=SimpleNamespace(state="QUEUED"))
        return self.jobs[name]

    def get_job(self, *, name: str) -> object:
        if self.get_error is not None:
            raise self.get_error
        return self.jobs[name]

    def delete_job(self, *, name: str) -> object:
        self.deleted.append(name)
        self.jobs.pop(name, None)
        return object()


def backend(client: FakeBatchClient) -> CloudBatchBackend:
    return CloudBatchBackend(client, project="project", region="us-central1", image="image")


def test_batch_job_definition_is_one_non_retrying_task_with_run_id_only() -> None:
    client = FakeBatchClient()
    run_id = uuid4()
    reference = backend(client).start(run_id)
    spec = client.created[0]["job"]
    assert reference.name.endswith(run_id.hex)
    assert spec.task_count == 1
    assert spec.parallelism == 1
    assert spec.max_retry_count == 0
    assert spec.environment == {
        "RUN_ID": str(run_id),
        "CLOUD_RUN_EXECUTION": reference.name,
    }


def test_start_is_idempotent_and_already_exists_converges() -> None:
    client = FakeBatchClient()
    batch = backend(client)
    run_id = uuid4()
    first = batch.start(run_id)
    assert batch.start(run_id) == first
    assert len(client.created) == 1


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("QUEUED", ExecutionState.PENDING),
        ("SCHEDULED", ExecutionState.PENDING),
        ("RUNNING", ExecutionState.RUNNING),
        ("SUCCEEDED", ExecutionState.SUCCEEDED),
        ("FAILED", ExecutionState.FAILED),
        ("CANCELLED", ExecutionState.CANCELLED),
    ],
)
def test_batch_states_are_normalized(state: str, expected: ExecutionState) -> None:
    assert (
        normalize_batch_job_state(SimpleNamespace(status=SimpleNamespace(state=state)))
        is expected
    )


def test_unknown_state_is_safe_and_retryable() -> None:
    with pytest.raises(ExecutionTemporaryError):
        normalize_batch_job_state(SimpleNamespace(status=SimpleNamespace(state="NEW_STATE")))


def test_get_and_cancel_validate_backend_reference() -> None:
    batch = backend(FakeBatchClient())
    reference = ExecutionReference(backend="cloud-run-jobs", name="wrong")
    with pytest.raises(ValidationError):
        batch.get(reference)
    with pytest.raises(ValidationError):
        batch.cancel(reference)


def test_cancel_is_idempotent_for_active_and_terminal_jobs() -> None:
    client = FakeBatchClient()
    batch = backend(client)
    reference = batch.start(uuid4())
    assert batch.cancel(reference) is ExecutionState.CANCELLED
    assert batch.cancel(reference) is ExecutionState.CANCELLED
    assert len(client.deleted) == 1


@pytest.mark.parametrize(
    ("error", "error_type"),
    [
        (KeyError("missing"), ExecutionNotFoundError),
        (RuntimeError("permission denied"), ExecutionPermissionError),
        (RuntimeError("quota exhausted"), ExecutionQuotaError),
        (RuntimeError("temporary network failure"), ExecutionTemporaryError),
    ],
)
def test_batch_lookup_errors_use_domain_errors(
    error: Exception, error_type: type[Exception]
) -> None:
    client = FakeBatchClient()
    client.get_error = error
    batch = backend(client)
    reference = ExecutionReference(backend="cloud-batch", name="jobs/missing")
    with pytest.raises(error_type):
        batch.get(reference)
