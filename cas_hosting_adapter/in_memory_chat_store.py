"""Thread-safe in-memory ChatStore for contract and end-to-end tests."""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any
from uuid import UUID

from .errors import ActiveRunConflictError, SessionNotFoundError, SessionOwnershipError
from .models import (
    ChatEvent,
    ExecutionReference,
    ReconciliationLease,
    Run,
    RunState,
    Session,
    SessionPage,
)


class InMemoryChatStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self.sessions: dict[str, Session] = {}
        self.runs: dict[UUID, Run] = {}
        self.events: dict[UUID, dict[str, ChatEvent]] = {}
        self._owners: dict[UUID, str] = {}
        self._subscriptions: dict[UUID, list[Callable[[ChatEvent], None]]] = {}
        self._leases: dict[UUID, ReconciliationLease] = {}

    def create_session(self, user_id: str, *, title: str = "") -> Session:
        from uuid import uuid4

        return self.put_session(Session(
            id=str(uuid4()), user_id=user_id, workspace_id=str(uuid4()), title=title
        ))

    def list_sessions(self, user_id: str, *, cursor: str | None, limit: int) -> SessionPage:
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._lock:
            sessions = sorted(
                (session for session in self.sessions.values() if session.user_id == user_id),
                key=lambda session: (session.updated_at, session.id),
                reverse=True,
            )
        if cursor is not None:
            sessions = [session for session in sessions if session.id < cursor]
        page = sessions[:limit]
        return SessionPage(
            sessions=page,
            next_cursor=page[-1].id if len(sessions) > limit and page else None,
        )

    def put_session(self, session: Session) -> Session:
        with self._lock:
            self.sessions[session.id] = session
            return session

    def get_session(self, user_id: str, session_id: str) -> Session:
        with self._lock:
            session = self.sessions.get(session_id)
            if session is None or session.user_id != user_id:
                raise SessionNotFoundError(session_id)
            return session

    def reserve_run(self, run: Run, event: ChatEvent) -> Run:
        with self._lock:
            session = self.sessions.get(run.session_id)
            if session is None or session.user_id != run.user_id:
                raise SessionNotFoundError(run.session_id)
            for existing in self.runs.values():
                if (
                    existing.session_id == run.session_id
                    and existing.idempotency_key == run.idempotency_key
                ):
                    return existing
            if session.active_run_id is not None:
                raise ActiveRunConflictError(str(session.active_run_id))
            self.runs[run.id] = run
            self.events[run.id] = {event.id: event}
            self.sessions[session.id] = session.model_copy(update={"active_run_id": run.id})
            return run

    def save_execution(self, run_id: UUID, execution: ExecutionReference) -> Run:
        with self._lock:
            run = self.runs[run_id]
            state = RunState.PENDING if run.state is RunState.REQUESTED else run.state
            updated = run.model_copy(update={"execution": execution, "state": state})
            self.runs[run_id] = updated
            return updated

    def fail_dispatch(self, run_id: UUID, error_code: str) -> Run:
        with self._lock:
            run = self.runs[run_id].model_copy(update={
                "state": RunState.DISPATCH_FAILED, "error_code": error_code
            })
            self.runs[run_id] = run
            session = self.sessions[run.session_id]
            self.sessions[session.id] = session.model_copy(update={"active_run_id": None})
            return run

    def claim_run(self, run_id: UUID, execution_identity: str) -> bool:
        with self._lock:
            run = self.runs[run_id]
            owner = self._owners.get(run_id)
            if owner is not None and owner != execution_identity:
                return False
            self._owners[run_id] = execution_identity
            self.runs[run_id] = run.model_copy(update={"state": RunState.RUNNING})
            return True

    def heartbeat_run(self, run_id: UUID, execution_identity: str) -> bool:
        with self._lock:
            return self._owners.get(run_id) == execution_identity

    def get_run(self, user_id: str, session_id: str, run_id: UUID) -> Run:
        with self._lock:
            run = self.runs.get(run_id)
            if run is None:
                raise SessionNotFoundError(str(run_id))
            if run.user_id != user_id or run.session_id != session_id:
                raise SessionOwnershipError(str(run_id))
            return run

    def get_run_for_job(self, run_id: UUID) -> Run:
        with self._lock:
            try:
                return self.runs[run_id]
            except KeyError as error:
                raise SessionNotFoundError(str(run_id)) from error

    def append_event(self, event: ChatEvent) -> ChatEvent:
        with self._lock:
            events = self.events[event.run_id]
            existing = events.get(event.id)
            if existing is not None:
                return existing
            assigned = event.model_copy(update={"sequence": len(events)})
            events[assigned.id] = assigned
            callbacks = list(self._subscriptions.get(event.run_id, []))
        for callback in callbacks:
            callback(assigned)
        return assigned

    def list_events(self, run_id: UUID, *, cursor: str | None = None) -> list[ChatEvent]:
        with self._lock:
            events = sorted(
                self.events[run_id].values(), key=lambda event: (event.sequence, event.id)
            )
        if cursor is None:
            return events
        return [event for event in events if event.id > cursor]

    def subscribe(
        self, run_id: UUID, cursor: str | None, callback: Callable[[ChatEvent], None]
    ) -> Callable[[], None]:
        for event in self.list_events(run_id, cursor=cursor):
            callback(event)
        with self._lock:
            subscribers = self._subscriptions.setdefault(run_id, [])
            subscribers.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                self._subscriptions.get(run_id, []).remove(callback)

        return unsubscribe

    def request_cancel(self, run_id: UUID) -> Run:
        with self._lock:
            run = self.runs[run_id]
            if run.state.terminal:
                return run
            updated = run.model_copy(update={"state": RunState.CANCEL_REQUESTED})
            self.runs[run_id] = updated
            return updated

    def commit_terminal(self, run: Run, execution_identity: str) -> Run:
        if not run.state.terminal:
            raise ValueError("terminal state is required")
        with self._lock:
            current = self.runs[run.id]
            if self._owners.get(run.id) != execution_identity:
                raise SessionOwnershipError(str(run.id))
            if current.user_id != run.user_id or current.session_id != run.session_id:
                raise SessionOwnershipError(str(run.id))
            self.runs[run.id] = run
            session = self.sessions[run.session_id]
            session_update: dict[str, Any] = {
                "active_run_id": None,
                "latest_run_state": run.state.value,
                "updated_at": datetime.now(UTC),
            }
            # A failed follow-up must not discard the last usable Claude
            # transcript/snapshot for the session.
            if (
                run.state is RunState.COMPLETED
                and run.claude_session_id is not None
                and run.snapshot is not None
            ):
                session_update.update({
                    "claude_session_id": run.claude_session_id,
                    "snapshot": run.snapshot,
                })
            self.sessions[session.id] = session.model_copy(update=session_update)
            return run

    def acquire_reconciliation_lease(
        self, run_id: UUID, holder: str
    ) -> ReconciliationLease | None:
        now = datetime.now(UTC)
        with self._lock:
            current = self._leases.get(run_id)
            if current is not None and current.holder != holder and current.expires_at > now:
                return None
            lease = ReconciliationLease(
                run_id=run_id, holder=holder, expires_at=now + timedelta(seconds=30)
            )
            self._leases[run_id] = lease
            return lease

    def reconcile_terminal(self, run_id: UUID, holder: str, state: RunState) -> Run:
        if not state.terminal:
            raise ValueError("terminal state is required")
        with self._lock:
            lease = self._leases.get(run_id)
            if lease is None or lease.holder != holder:
                raise SessionOwnershipError(str(run_id))
            run = self.runs[run_id].model_copy(update={"state": state})
            self.runs[run_id] = run
            session = self.sessions[run.session_id]
            self.sessions[session.id] = session.model_copy(update={"active_run_id": None})
            return run
