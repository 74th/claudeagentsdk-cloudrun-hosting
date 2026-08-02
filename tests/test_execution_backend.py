from uuid import uuid4

import pytest

from cas_hosting_adapter.errors import ExecutionNotFoundError
from cas_hosting_adapter.models import ExecutionReference, ExecutionState
from cas_hosting_adapter.protocols import InMemoryExecutionBackend


def test_execution_backend_start_is_idempotent_and_cancel_is_terminal_safe() -> None:
    backend = InMemoryExecutionBackend()
    run_id = uuid4()
    reference = backend.start(run_id)

    assert backend.start(run_id) == reference
    assert backend.get(reference) is ExecutionState.PENDING
    assert backend.cancel(reference) is ExecutionState.CANCELLED
    assert backend.cancel(reference) is ExecutionState.CANCELLED


def test_execution_backend_rejects_unknown_reference() -> None:
    backend = InMemoryExecutionBackend()
    unknown = ExecutionReference(backend="in-memory", name="executions/missing")

    with pytest.raises(ExecutionNotFoundError):
        backend.get(unknown)
