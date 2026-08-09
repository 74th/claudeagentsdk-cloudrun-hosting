"""Provider seams and deterministic in-memory fakes."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Protocol
from uuid import UUID

from .errors import ExecutionNotFoundError
from .models import (
    ChatEvent,
    ExecutionReference,
    ExecutionState,
    InitialSessionResult,
    QuestionRequest,
    ReconciliationLease,
    Run,
    RunPage,
    RunState,
    Session,
    SessionEvent,
    SessionPage,
    WorkspaceReference,
)


class Clock(Protocol):
    def now(self) -> datetime: ...


class SessionEvents(Protocol):
    def append(self, session_id: str, event: SessionEvent) -> None: ...
    def list(self, session_id: str) -> list[SessionEvent]: ...


class SnapshotStore(Protocol):
    def upload(self, object_path: str, data: bytes, *, if_generation_match: int) -> int: ...
    def download(self, object_path: str, generation: int) -> bytes: ...
    def delete(self, object_path: str, generation: int) -> None: ...


class ActiveRunLockStore(Protocol):
    def acquire(self, session_id: str, run_id: UUID) -> int: ...
    def release(self, session_id: str, generation: int) -> None: ...


class ExecutionBackend(Protocol):
    """Starts, reads, and cancels a provider execution for one durable run."""

    def start(self, run_id: UUID) -> ExecutionReference: ...
    def get(self, reference: ExecutionReference) -> ExecutionState: ...
    def cancel(self, reference: ExecutionReference) -> ExecutionState: ...


class ChatStore(Protocol):
    """Durable chat state contract with transaction-level semantics."""

    def create_session(self, user_id: str, *, title: str = "") -> Session: ...
    def reserve_initial_run(
        self, session: Session, run: Run, event: ChatEvent
    ) -> InitialSessionResult: ...
    def get_session(self, user_id: str, session_id: str) -> Session: ...
    def list_sessions(self, user_id: str, *, cursor: str | None, limit: int) -> SessionPage: ...
    def list_runs(
        self, user_id: str, session_id: str, *, cursor: str | None, limit: int
    ) -> RunPage: ...
    def reserve_run(self, run: Run, event: ChatEvent) -> Run: ...
    def save_execution(self, run_id: UUID, execution: ExecutionReference) -> Run: ...
    def fail_dispatch(self, run_id: UUID, error_code: str) -> Run: ...
    def get_run(self, user_id: str, session_id: str, run_id: UUID) -> Run: ...
    def get_run_for_job(self, run_id: UUID) -> Run: ...
    def claim_run(self, run_id: UUID, execution_identity: str) -> bool: ...
    def heartbeat_run(self, run_id: UUID, execution_identity: str) -> bool: ...
    def append_event(self, event: ChatEvent) -> ChatEvent: ...
    def list_events(self, run_id: UUID, *, cursor: str | None = None) -> list[ChatEvent]: ...
    def latest_event(self, run_id: UUID) -> ChatEvent | None: ...
    def subscribe(
        self, run_id: UUID, cursor: str | None, callback: Callable[[ChatEvent], None]
    ) -> Callable[[], None]: ...
    def request_cancel(self, run_id: UUID) -> Run: ...
    def commit_terminal(self, run: Run, execution_identity: str) -> Run: ...
    def acquire_reconciliation_lease(
        self, run_id: UUID, holder: str
    ) -> ReconciliationLease | None: ...
    def reconcile_terminal(
        self, run_id: UUID, holder: str, state: RunState, *, error_code: str | None = None
    ) -> Run: ...
    def release_reconciliation_lease(self, run_id: UUID, holder: str) -> None: ...
    def create_questions(
        self, run_id: UUID, questions: list[QuestionRequest]
    ) -> list[QuestionRequest]: ...
    def list_questions_for_job(self, run_id: UUID) -> list[QuestionRequest]: ...
    def get_question_for_job(self, run_id: UUID, question_id: str) -> QuestionRequest: ...
    def list_questions(
        self, user_id: str, session_id: str, run_id: UUID
    ) -> list[QuestionRequest]: ...
    def answer_question(
        self,
        user_id: str,
        session_id: str,
        run_id: UUID,
        question_id: str,
        answers: str | list[str],
        idempotency_key: str,
    ) -> QuestionRequest: ...
    def answer_question_for_job(
        self, run_id: UUID, question_id: str, answers: str | list[str], idempotency_key: str
    ) -> QuestionRequest: ...
    def subscribe_question(
        self, run_id: UUID, question_id: str, callback: Callable[[QuestionRequest], None]
    ) -> Callable[[], None]: ...


class WorkspaceStore(Protocol):
    """Immutable snapshots without provider object types."""

    def create(self, object_key: str, data: bytes) -> WorkspaceReference: ...
    def get(self, reference: WorkspaceReference) -> bytes: ...
    def delete(self, reference: WorkspaceReference) -> None: ...


class AgentFactory(Protocol):
    async def run(
        self, *, prompt: str, workspace: Path, transcript_dir: Path, resume: str | None = None
    ) -> str: ...


class InMemoryClock:
    def __init__(self, current: datetime | None = None) -> None:
        self.current = current or datetime.now(UTC)

    def now(self) -> datetime:
        return self.current


class InMemoryEvents:
    def __init__(self) -> None:
        self.events: dict[str, list[SessionEvent]] = {}

    def append(self, session_id: str, event: SessionEvent) -> None:
        self.events.setdefault(session_id, []).append(event)

    def list(self, session_id: str) -> list[SessionEvent]:
        return list(self.events.get(session_id, []))


class InMemorySnapshotStore:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[int, bytes]] = {}
        self.next_generation = 1

    def upload(self, object_path: str, data: bytes, *, if_generation_match: int) -> int:
        current = self.objects.get(object_path)
        if if_generation_match == 0:
            if current is not None:
                raise FileExistsError(object_path)
        elif current is None or current[0] != if_generation_match:
            raise FileNotFoundError(object_path)
        generation = self.next_generation
        self.next_generation += 1
        self.objects[object_path] = (generation, data)
        return generation

    def download(self, object_path: str, generation: int) -> bytes:
        found_generation, data = self.objects[object_path]
        if found_generation != generation:
            raise FileNotFoundError(object_path)
        return data

    def delete(self, object_path: str, generation: int) -> None:
        self.download(object_path, generation)
        del self.objects[object_path]


class InMemoryWorkspaceStore:
    """Immutable WorkspaceStore fake with monotonically increasing versions."""

    def __init__(self) -> None:
        self._objects: dict[str, tuple[str, bytes]] = {}
        self._next_version = 1

    def create(self, object_key: str, data: bytes) -> WorkspaceReference:
        if object_key in self._objects:
            raise FileExistsError(object_key)
        version = str(self._next_version)
        self._next_version += 1
        self._objects[object_key] = (version, data)
        return WorkspaceReference(
            object_key=object_key,
            version=version,
            sha256=hashlib.sha256(data).hexdigest(),
            size=len(data),
        )

    def get(self, reference: WorkspaceReference) -> bytes:
        version, data = self._objects[reference.object_key]
        if version != reference.version:
            raise FileNotFoundError(reference.object_key)
        return data

    def delete(self, reference: WorkspaceReference) -> None:
        self.get(reference)
        del self._objects[reference.object_key]


class InMemoryActiveRunLocks:
    def __init__(self) -> None:
        self.locks: dict[str, tuple[int, UUID]] = {}
        self.next_generation = 1

    def acquire(self, session_id: str, run_id: UUID) -> int:
        if session_id in self.locks:
            raise FileExistsError(session_id)
        generation = self.next_generation
        self.next_generation += 1
        self.locks[session_id] = (generation, run_id)
        return generation

    def release(self, session_id: str, generation: int) -> None:
        if self.locks[session_id][0] != generation:
            raise FileNotFoundError(session_id)
        del self.locks[session_id]


class InMemoryExecutionBackend:
    """Thread-safe fake with explicit state controls for contract tests."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._references_by_run: dict[UUID, ExecutionReference] = {}
        self._states: dict[str, ExecutionState] = {}

    def start(self, run_id: UUID) -> ExecutionReference:
        with self._lock:
            existing = self._references_by_run.get(run_id)
            if existing is not None:
                return existing
            reference = ExecutionReference(
                backend="in-memory",
                name=f"executions/{len(self._states) + 1}",
                identity=str(run_id),
            )
            self._references_by_run[run_id] = reference
            self._states[reference.name] = ExecutionState.PENDING
            return reference

    def get(self, reference: ExecutionReference) -> ExecutionState:
        with self._lock:
            try:
                return self._states[reference.name]
            except KeyError as error:
                raise ExecutionNotFoundError(reference.name) from error

    def cancel(self, reference: ExecutionReference) -> ExecutionState:
        with self._lock:
            try:
                state = self._states[reference.name]
            except KeyError as error:
                raise ExecutionNotFoundError(reference.name) from error
            if state in {ExecutionState.SUCCEEDED, ExecutionState.FAILED, ExecutionState.CANCELLED}:
                return state
            self._states[reference.name] = ExecutionState.CANCELLED
            return ExecutionState.CANCELLED

    def set_state(self, reference: ExecutionReference, state: ExecutionState) -> None:
        with self._lock:
            if reference.name not in self._states:
                raise ExecutionNotFoundError(reference.name)
            self._states[reference.name] = state
