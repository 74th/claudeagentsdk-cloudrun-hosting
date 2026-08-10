from uuid import uuid4

import pytest

from cas_hosting_adapter.control_client import ControlClient
from cas_hosting_adapter.errors import ExecutionNotFoundError, ExecutionTemporaryError
from cas_hosting_adapter.in_memory_chat_store import InMemoryChatStore
from cas_hosting_adapter.models import ChatEvent, ExecutionReference, ExecutionState, Run, RunState
from cas_hosting_adapter.protocols import InMemoryExecutionBackend


def test_control_client_reserves_and_starts_a_run() -> None:
    store = InMemoryChatStore()
    client = ControlClient(store, InMemoryExecutionBackend())
    session = client.create_session("user", title="test")
    run = client.reserve_and_start(
        Run(
            user_id="user",
            session_id=session.id,
            workspace_id=session.workspace_id,
            idempotency_key="key",
        ),
        "hello",
    )
    assert run.execution is not None
    assert store.list_events(run.id)[0].payload == {"content": "hello"}


def test_control_client_releases_session_after_dispatch_failure() -> None:
    class FailingBackend(InMemoryExecutionBackend):
        def start(self, run_id):
            raise RuntimeError("unavailable")

    store = InMemoryChatStore()
    client = ControlClient(store, FailingBackend())
    session = client.create_session("user")
    run = Run(
        user_id="user",
        session_id=session.id,
        workspace_id=session.workspace_id,
        idempotency_key="key",
    )
    with pytest.raises(RuntimeError):
        client.reserve_and_start(run, "hello")
    assert store.get_run_for_job(run.id).state.value == "dispatch_failed"
    assert store.get_session("user", session.id).active_run_id is None


def test_control_client_reuses_idempotent_run_for_redelivery() -> None:
    store = InMemoryChatStore()
    backend = InMemoryExecutionBackend()
    client = ControlClient(store, backend)
    session = client.create_session("user")
    first = Run(
        user_id="user",
        session_id=session.id,
        workspace_id=session.workspace_id,
        idempotency_key="key",
    )
    started = client.reserve_and_start(first, "hello")
    retried = client.reserve_and_start(
        Run(
            user_id="user",
            session_id=session.id,
            workspace_id=session.workspace_id,
            idempotency_key="key",
        ),
        "hello",
    )
    assert retried.id == started.id
    assert retried.execution == started.execution


def test_start_session_lazily_creates_one_named_session() -> None:
    store = InMemoryChatStore()
    client = ControlClient(store, InMemoryExecutionBackend())
    first = client.start_session("user", "  First prompt\nmore", "initial-key")
    retry = client.start_session("user", "First prompt", "initial-key")
    assert first.session.title == "First prompt"
    assert retry.session.id == first.session.id
    assert retry.run.id == first.run.id
    assert len(store.sessions) == 1
    assert len(store.runs) == 1


def test_start_session_preserves_failed_dispatch_for_retry() -> None:
    class FailingOnceBackend(InMemoryExecutionBackend):
        def __init__(self) -> None:
            super().__init__()
            self.failed = False

        def start(self, run_id):
            if not self.failed:
                self.failed = True
                raise RuntimeError("unavailable")
            return super().start(run_id)

    store = InMemoryChatStore()
    backend = FailingOnceBackend()
    client = ControlClient(store, backend)
    with pytest.raises(RuntimeError):
        client.start_session("user", "hello", "initial-key")
    retry = client.start_session("user", "hello", "initial-key")
    assert retry.run.execution is not None
    assert len(store.sessions) == 1
    assert len(store.runs) == 1


def test_late_execution_save_never_revives_a_terminal_run() -> None:
    store = InMemoryChatStore()
    session = store.create_session("user")
    run = Run(
        user_id="user",
        session_id=session.id,
        workspace_id=session.workspace_id,
        idempotency_key="key",
    )
    store.reserve_run(run, ChatEvent(id="user", run_id=run.id, sequence=0, type="user"))
    store.runs[run.id] = run.model_copy(update={"state": RunState.COMPLETED})

    saved = store.save_execution(
        run.id, ExecutionReference(backend="test", name="executions/late", identity="late")
    )

    assert saved.state is RunState.COMPLETED


def test_reconcile_marks_success_without_snapshot_as_persistence_failure() -> None:
    store = InMemoryChatStore()
    backend = InMemoryExecutionBackend()
    client = ControlClient(store, backend)
    session = client.create_session("user")
    run = client.reserve_and_start(
        Run(
            user_id="user",
            session_id=session.id,
            workspace_id=session.workspace_id,
            idempotency_key="key",
        ),
        "hello",
    )
    assert run.execution is not None
    backend.set_state(run.execution, ExecutionState.SUCCEEDED)
    assert client.reconcile(run.id, holder="reconciler").state is RunState.PERSISTENCE_FAILED


def test_reconcile_keeps_active_run_on_temporary_status_error() -> None:
    class TemporaryBackend(InMemoryExecutionBackend):
        def get(self, reference):
            raise ExecutionTemporaryError("unavailable")

    store = InMemoryChatStore()
    client = ControlClient(store, TemporaryBackend())
    session = client.create_session("user")
    run = client.reserve_and_start(
        Run(
            user_id="user",
            session_id=session.id,
            workspace_id=session.workspace_id,
            idempotency_key="key",
        ),
        "hello",
    )
    assert client.reconcile(run.id, holder="reconciler").state is RunState.PENDING
    assert store.get_session("user", session.id).active_run_id == run.id


def test_reconcile_marks_missing_execution_with_safe_error_code_idempotently() -> None:
    class MissingBackend(InMemoryExecutionBackend):
        def get(self, reference):
            raise ExecutionNotFoundError("provider detail must not persist")

    store = InMemoryChatStore()
    client = ControlClient(store, MissingBackend())
    session = client.create_session("user")
    run = client.reserve_and_start(
        Run(
            user_id="user",
            session_id=session.id,
            workspace_id=session.workspace_id,
            idempotency_key="key",
        ),
        "hello",
    )
    first = client.reconcile(run.id, holder="reconciler")
    second = client.reconcile(run.id, holder="reconciler-2")
    assert (first.state, first.error_code) == (RunState.FAILED, "cloud_run_execution_not_found")
    assert second == first
    assert store.get_session("user", session.id).active_run_id is None


def test_reconcile_leaves_run_unchanged_when_execution_reference_is_missing() -> None:
    store = InMemoryChatStore()
    client = ControlClient(store, InMemoryExecutionBackend())
    session = client.create_session("user")
    run_id = uuid4()
    run = store.reserve_run(
        Run(
            id=run_id,
            user_id="user",
            session_id=session.id,
            workspace_id=session.workspace_id,
            idempotency_key="key",
        ),
        ChatEvent(id="user", run_id=run_id, sequence=0, type="user"),
    )
    assert client.reconcile(run.id, holder="reconciler") == run


def test_cancel_commits_only_after_backend_confirms_cancelled() -> None:
    store = InMemoryChatStore()
    backend = InMemoryExecutionBackend()
    client = ControlClient(store, backend)
    session = client.create_session("user")
    run = client.reserve_and_start(
        Run(
            user_id="user",
            session_id=session.id,
            workspace_id=session.workspace_id,
            idempotency_key="key",
        ),
        "hello",
    )
    assert client.cancel(run.id).state is RunState.CANCELLED


def test_cancel_uses_durable_request_when_provider_job_is_already_gone() -> None:
    class MissingCancelBackend(InMemoryExecutionBackend):
        def cancel(self, reference):
            raise ExecutionNotFoundError("provider Job was removed after delete")

    store = InMemoryChatStore()
    client = ControlClient(store, MissingCancelBackend())
    session = client.create_session("user")
    run = client.reserve_and_start(
        Run(
            user_id="user",
            session_id=session.id,
            workspace_id=session.workspace_id,
            idempotency_key="key",
        ),
        "hello",
    )
    cancelled = client.cancel(run.id)
    assert cancelled.state is RunState.CANCELLED
    assert cancelled.error_code == "execution_cancelled_after_deletion"


def test_subscription_deduplicates_catchup_and_listener_boundary() -> None:
    store = InMemoryChatStore()
    client = ControlClient(store, InMemoryExecutionBackend())
    session = client.create_session("user")
    run = client.reserve_and_start(
        Run(
            user_id="user",
            session_id=session.id,
            workspace_id=session.workspace_id,
            idempotency_key="key",
        ),
        "hello",
    )
    received: list[str] = []
    unsubscribe = client.subscribe_from_cursor(
        run.id, None, lambda event: received.append(event.id)
    )
    store.append_event(store.list_events(run.id)[0])
    store.append_event(
        type(store.list_events(run.id)[0])(
            id="agent", run_id=run.id, sequence=0, type="agent", payload={}
        )
    )
    unsubscribe()
    assert received == [f"user:{run.id}", "agent"]
