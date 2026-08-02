from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from cas_hosting_adapter.cloud_run_backend import (
    CloudRunJobsBackend,
    _map_execution_error,
    normalize_execution_conditions,
)
from cas_hosting_adapter.errors import ExecutionNotFoundError, ExecutionPermissionError
from cas_hosting_adapter.models import ExecutionReference, ExecutionState


def condition(type_: str, state: str, reason: str = "") -> SimpleNamespace:
    return SimpleNamespace(type_=type_, state=SimpleNamespace(name=state), reason=reason)


def test_normalize_cloud_run_execution_conditions() -> None:
    assert normalize_execution_conditions([]) is ExecutionState.PENDING
    started = normalize_execution_conditions([condition("Started", "CONDITION_SUCCEEDED")])
    succeeded = normalize_execution_conditions([condition("Completed", "CONDITION_SUCCEEDED")])
    cancelled = normalize_execution_conditions(
        [condition("Completed", "CONDITION_FAILED", "Cancelled")]
    )
    failed = normalize_execution_conditions([condition("Completed", "CONDITION_FAILED", "Error")])
    assert started is ExecutionState.RUNNING
    assert succeeded is ExecutionState.SUCCEEDED
    assert cancelled is ExecutionState.CANCELLED
    assert failed is ExecutionState.FAILED


class FakeExecutions:
    def __init__(self, conditions: list[Any]) -> None:
        self.conditions = conditions
        self.cancelled: list[str] = []

    def get_execution(self, *, name: str) -> SimpleNamespace:
        return SimpleNamespace(conditions=self.conditions)

    def cancel_execution(self, *, name: str) -> None:
        self.cancelled.append(name)


class FakeJobs:
    def __init__(self, target: str) -> None:
        self.target = target

    def run_job(self, *, request: Any) -> SimpleNamespace:
        return SimpleNamespace(metadata=SimpleNamespace(target=self.target))


class FakeAsyncJobs:
    def __init__(self) -> None:
        self.transport = SimpleNamespace(
            operations_client=SimpleNamespace(
                get_operation=lambda _name: SimpleNamespace(done=False),
                cancel_operation=lambda _name: None,
            )
        )

    def run_job(self, *, request: Any) -> SimpleNamespace:
        return SimpleNamespace(
            metadata=SimpleNamespace(target=""),
            operation=SimpleNamespace(
                name="projects/project/locations/us-central1/operations/operation-1"
            ),
        )


def test_cancel_is_idempotent_for_terminal_execution() -> None:
    terminal = FakeExecutions([condition("Completed", "CONDITION_SUCCEEDED")])
    backend = CloudRunJobsBackend(
        object(), terminal, project="project", region="us-central1", job_name="job"
    )
    reference = ExecutionReference(backend="cloud-run-jobs", name="execution")
    assert backend.cancel(reference) is ExecutionState.SUCCEEDED
    assert terminal.cancelled == []


def test_cloud_run_errors_are_normalized() -> None:
    not_found = _map_execution_error(RuntimeError("not found"), "get")
    assert isinstance(not_found, ExecutionNotFoundError)
    assert isinstance(
        _map_execution_error(RuntimeError("permission denied"), "get"), ExecutionPermissionError
    )


def test_dispatch_once_returns_existing_reference_without_backend_call() -> None:
    backend = CloudRunJobsBackend(
        object(), object(), project="project", region="us-central1", job_name="job"
    )
    reference = ExecutionReference(backend="cloud-run-jobs", name="execution")
    assert backend.dispatch_once(uuid4(), existing=reference) == reference


def test_cancel_calls_backend_for_active_execution() -> None:
    active = FakeExecutions([condition("Started", "CONDITION_SUCCEEDED")])
    backend = CloudRunJobsBackend(
        object(), active, project="project", region="us-central1", job_name="job"
    )
    reference = ExecutionReference(backend="cloud-run-jobs", name="execution")
    assert backend.cancel(reference) is ExecutionState.CANCELLED
    assert active.cancelled == ["execution"]


def test_timeout_condition_is_a_failed_execution() -> None:
    conditions = [condition("Completed", "CONDITION_FAILED", "DeadlineExceeded")]
    state = normalize_execution_conditions(conditions)
    assert state is ExecutionState.FAILED


def test_start_uses_execution_name_from_run_operation_metadata() -> None:
    run_id = uuid4()
    target = "projects/project/locations/us-central1/jobs/job/executions/execution-1"
    backend = CloudRunJobsBackend(
        FakeJobs(target), object(), project="project", region="us-central1", job_name="job"
    )
    assert backend.start(run_id).name == target


def test_start_returns_operation_reference_without_waiting_for_job_completion() -> None:
    backend = CloudRunJobsBackend(
        FakeAsyncJobs(), object(), project="project", region="us-central1", job_name="job"
    )

    reference = backend.start(uuid4())

    assert reference.name.endswith("/operations/operation-1")
    assert backend.get(reference) is ExecutionState.PENDING
