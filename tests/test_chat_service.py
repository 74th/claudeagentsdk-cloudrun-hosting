from __future__ import annotations

from uuid import UUID

from cas_hosting_adapter.control_client import ControlClient
from cas_hosting_adapter.errors import ExecutionTemporaryError
from cas_hosting_adapter.in_memory_chat_store import InMemoryChatStore
from cas_hosting_adapter.models import ChatEvent, RunState
from cas_hosting_adapter.protocols import InMemoryExecutionBackend
from example.chat.events import ChatEventKind, normalize_events
from example.chat.service import ChatService


def make_service() -> tuple[ChatService, InMemoryChatStore, InMemoryExecutionBackend]:
    store = InMemoryChatStore()
    backend = InMemoryExecutionBackend()
    return ChatService(ControlClient(store, backend), "user"), store, backend


def test_start_supports_new_and_existing_sessions_idempotently() -> None:
    service, store, _backend = make_service()
    first = service.start("hello", idempotency_key="request-1")
    repeated = service.start(
        "hello", session_id=first.session_id, idempotency_key="request-1"
    )
    store.claim_run(first.run_id, "test")
    lease = store.acquire_reconciliation_lease(first.run_id, "test-holder")
    assert lease is not None
    store.reconcile_terminal(first.run_id, "test-holder", RunState.COMPLETED)
    store.release_reconciliation_lease(first.run_id, "test-holder")
    continued = service.start(
        "follow up", session_id=first.session_id, idempotency_key="request-2"
    )

    assert first.session_id == repeated.session_id
    assert first.run_id == repeated.run_id
    assert first.run_id != continued.run_id


def test_stream_delivers_saved_events_once_and_stops_at_final() -> None:
    service, store, _backend = make_service()
    started = service.start("hello", idempotency_key="request-1")
    store.append_event(
        ChatEvent(
            id="agent-1",
            run_id=started.run_id,
            sequence=99,
            type="agent",
            payload={"content": "hi"},
        )
    )
    store.append_event(
        ChatEvent(
            id="final-1",
            run_id=started.run_id,
            sequence=99,
            type="final",
            payload={"output": "done"},
        )
    )
    store.claim_run(started.run_id, "test")
    lease = store.acquire_reconciliation_lease(started.run_id, "test-holder")
    assert lease is not None
    store.reconcile_terminal(started.run_id, "test-holder", RunState.COMPLETED)
    store.release_reconciliation_lease(started.run_id, "test-holder")

    events = list(service.stream(started.run))

    assert [event.id for event in events] == [
        f"user:{started.run_id}",
        "agent-1",
        "final-1",
    ]
    assert events[-1].kind is ChatEventKind.FINAL


def test_normalizer_preserves_unknown_events_and_converts_tool_blocks() -> None:
    run_id = UUID("00000000-0000-0000-0000-000000000001")
    events = normalize_events(
        [
            ChatEvent(
                id="legacy",
                run_id=run_id,
                sequence=0,
                type="user",
                payload={"content": [{"tool_use_id": "tool-1", "content": "ok"}]},
            ),
            ChatEvent(id="future", run_id=run_id, sequence=1, type="future_event"),
        ]
    )

    assert [event.kind for event in events] == [
        ChatEventKind.TOOL_COMPLETED,
        ChatEventKind.UNKNOWN,
    ]
    assert events[1].raw_type == "future_event"


def test_stream_emits_synthetic_terminal_event_for_failed_run() -> None:
    service, store, _backend = make_service()
    started = service.start("hello", idempotency_key="request-1")
    store.claim_run(started.run_id, "test")
    lease = store.acquire_reconciliation_lease(started.run_id, "test-holder")
    assert lease is not None
    store.reconcile_terminal(started.run_id, "test-holder", RunState.FAILED, error_code="failed")
    store.release_reconciliation_lease(started.run_id, "test-holder")

    events = list(service.stream(started.run))

    assert events[-1].kind is ChatEventKind.TERMINAL
    assert events[-1].raw_type == "terminal"
    assert events[-1].payload["state"] == "failed"


def test_stream_reconnects_after_temporary_subscription_error() -> None:
    store = InMemoryChatStore()
    backend = InMemoryExecutionBackend()
    base = ControlClient(store, backend)
    started = ChatService(base, "user").start("hello", idempotency_key="request-1")
    store.append_event(
        ChatEvent(
            id="final-1",
            run_id=started.run_id,
            sequence=1,
            type="final",
            payload={"output": "done"},
        )
    )
    store.claim_run(started.run_id, "test")
    lease = store.acquire_reconciliation_lease(started.run_id, "test-holder")
    assert lease is not None
    store.reconcile_terminal(started.run_id, "test-holder", RunState.COMPLETED)

    class FlakyClient(ControlClient):
        attempts = 0

        def subscribe_from_cursor(self, run_id, cursor, callback):
            self.attempts += 1
            if self.attempts == 1:
                raise ExecutionTemporaryError("temporary")
            return super().subscribe_from_cursor(run_id, cursor, callback)

    service = ChatService(FlakyClient(store, backend), "user")
    events = list(service.stream(started.run, reconnect_delay=0))

    assert [event.id for event in events] == [f"user:{started.run_id}", "final-1"]
