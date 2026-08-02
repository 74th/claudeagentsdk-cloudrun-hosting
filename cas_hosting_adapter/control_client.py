"""High-level control-plane API over provider-neutral ports."""
from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from .errors import ExecutionTemporaryError
from .models import ChatEvent, ExecutionState, Run, RunState, Session, SessionPage
from .protocols import ChatStore, ExecutionBackend


class ControlClient:
    def __init__(self, chat_store: ChatStore, execution_backend: ExecutionBackend) -> None:
        self._chat_store = chat_store
        self._execution_backend = execution_backend

    def create_session(self, user_id: str, *, title: str = "") -> Session:
        return self._chat_store.create_session(user_id, title=title)

    def get_session(self, user_id: str, session_id: str) -> Session:
        return self._chat_store.get_session(user_id, session_id)

    def list_sessions(self, user_id: str, *, cursor: str | None, limit: int) -> SessionPage:
        return self._chat_store.list_sessions(user_id, cursor=cursor, limit=limit)

    def reserve_and_start(self, run: Run, message: str) -> Run:
        reserved = self._chat_store.reserve_run(
            run,
            ChatEvent(
                id=f"user:{run.id}", run_id=run.id, sequence=0, type="user",
                payload={"content": message},
            ),
        )
        if reserved.execution is not None:
            return reserved
        try:
            execution = self._execution_backend.start(reserved.id)
        except Exception as error:
            self._chat_store.fail_dispatch(reserved.id, type(error).__name__)
            raise
        return self._chat_store.save_execution(reserved.id, execution)

    def get_run(self, user_id: str, session_id: str, run_id: UUID) -> Run:
        return self._chat_store.get_run(user_id, session_id, run_id)

    def list_events(self, run_id: UUID, *, cursor: str | None = None) -> list[ChatEvent]:
        return self._chat_store.list_events(run_id, cursor=cursor)

    def subscribe(
        self, run_id: UUID, cursor: str | None, callback: Callable[[ChatEvent], None]
    ) -> Callable[[], None]:
        return self._chat_store.subscribe(run_id, cursor, callback)

    def subscribe_from_cursor(
        self, run_id: UUID, cursor: str | None, callback: Callable[[ChatEvent], None]
    ) -> Callable[[], None]:
        """Bridge durable catch-up and live delivery without duplicate UI events."""
        seen: set[str] = set()

        def deliver(event: ChatEvent) -> None:
            if event.id not in seen:
                seen.add(event.id)
                try:
                    callback(event)
                except Exception as error:
                    raise ExecutionTemporaryError("event subscription callback failed") from error

        for event in sorted(
            self._chat_store.list_events(run_id, cursor=cursor),
            key=lambda item: (item.sequence, item.id),
        ):
            deliver(event)
        return self._chat_store.subscribe(run_id, cursor, deliver)

    def cancel(self, run_id: UUID) -> Run:
        run = self._chat_store.request_cancel(run_id)
        if run.execution is not None:
            state = self._execution_backend.cancel(run.execution)
            if state is ExecutionState.CANCELLED:
                lease = self._chat_store.acquire_reconciliation_lease(run_id, "control-cancel")
                if lease is None:
                    return self._chat_store.get_run_for_job(run_id)
                return self._chat_store.reconcile_terminal(
                    run_id, "control-cancel", RunState.CANCELLED
                )
        return self._chat_store.get_run_for_job(run_id)

    def reconcile(self, run_id: UUID, *, holder: str) -> Run:
        run = self._chat_store.get_run_for_job(run_id)
        if not run.state.active or run.execution is None:
            return run
        lease = self._chat_store.acquire_reconciliation_lease(run_id, holder)
        if lease is None:
            return run
        status = self._execution_backend.get(run.execution)
        target = {
            ExecutionState.CANCELLED: RunState.CANCELLED,
            ExecutionState.FAILED: RunState.FAILED,
        }.get(status)
        if status is ExecutionState.SUCCEEDED:
            events = self._chat_store.list_events(run_id)
            has_final = any(event.type == "final" for event in events)
            target = RunState.PERSISTENCE_FAILED if run.snapshot is None or not has_final else None
        if target is None:
            return run
        return self._chat_store.reconcile_terminal(run_id, holder, target)
