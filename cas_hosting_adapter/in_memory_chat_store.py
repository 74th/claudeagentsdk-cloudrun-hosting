"""Thread-safe in-memory ChatStore for contract and end-to-end tests."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any
from uuid import UUID

from .errors import (
    ActiveRunConflictError,
    QuestionClosedError,
    QuestionConflictError,
    QuestionNotFoundError,
    QuestionOwnershipError,
    SessionNotFoundError,
    SessionOwnershipError,
)
from .models import (
    ChatEvent,
    ExecutionReference,
    InitialSessionResult,
    QuestionRequest,
    QuestionState,
    ReconciliationLease,
    Run,
    RunPage,
    RunState,
    Session,
    SessionPage,
    validate_question_batch,
)


class InMemoryChatStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self.sessions: dict[str, Session] = {}
        self.runs: dict[UUID, Run] = {}
        self.events: dict[UUID, dict[str, ChatEvent]] = {}
        self._owners: dict[UUID, str] = {}
        self._subscriptions: dict[UUID, list[Callable[[ChatEvent], None]]] = {}
        self._questions: dict[UUID, dict[str, QuestionRequest]] = {}
        self._question_subscriptions: dict[
            tuple[UUID, str], list[Callable[[QuestionRequest], None]]
        ] = {}
        self._leases: dict[UUID, ReconciliationLease] = {}

    def create_session(self, user_id: str, *, title: str = "") -> Session:
        from uuid import uuid4

        return self.put_session(
            Session(id=str(uuid4()), user_id=user_id, workspace_id=str(uuid4()), title=title)
        )

    def reserve_initial_run(
        self, session: Session, run: Run, event: ChatEvent
    ) -> InitialSessionResult:
        """Atomically reserve the draft session, first run, and user event."""
        if (
            session.user_id != run.user_id
            or session.id != run.session_id
            or session.workspace_id != run.workspace_id
            or event.run_id != run.id
        ):
            raise ValueError("initial session, run, and event references do not match")
        with self._lock:
            existing_session = self.sessions.get(session.id)
            if existing_session is not None:
                if existing_session.user_id != session.user_id:
                    raise SessionOwnershipError(session.id)
                existing_runs = [
                    candidate
                    for candidate in self.runs.values()
                    if candidate.session_id == session.id
                    and candidate.idempotency_key == run.idempotency_key
                ]
                if existing_runs:
                    return InitialSessionResult(session=existing_session, run=existing_runs[0])
                if existing_session.active_run_id is not None:
                    raise ActiveRunConflictError(str(existing_session.active_run_id))
                session = existing_session
            self.sessions[session.id] = session.model_copy(
                update={"active_run_id": run.id, "latest_run_state": run.state.value}
            )
            self.runs[run.id] = run
            self.events[run.id] = {event.id: event.model_copy(update={"sequence": 0})}
            return InitialSessionResult(session=self.sessions[session.id], run=run)

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

    def list_runs(
        self, user_id: str, session_id: str, *, cursor: str | None, limit: int
    ) -> RunPage:
        if limit < 1:
            raise ValueError("limit must be positive")
        self.get_session(user_id, session_id)
        with self._lock:
            runs = sorted(
                (run for run in self.runs.values() if run.session_id == session_id),
                key=lambda run: (run.created_at, str(run.id)),
            )
        if cursor is not None:
            created_at, run_id = self._decode_run_cursor(cursor)
            runs = [run for run in runs if (run.created_at, str(run.id)) > (created_at, run_id)]
        page = runs[:limit]
        next_cursor = None
        if len(runs) > limit and page:
            last = page[-1]
            next_cursor = self._encode_run_cursor(last.created_at, str(last.id))
        return RunPage(runs=page, next_cursor=next_cursor)

    # Explicit alias for callers that want to emphasize the history use case.
    def list_session_runs(
        self, user_id: str, session_id: str, *, cursor: str | None, limit: int
    ) -> RunPage:
        return self.list_runs(user_id, session_id, cursor=cursor, limit=limit)

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
            self.sessions[session.id] = session.model_copy(
                update={
                    "active_run_id": run.id,
                    "latest_run_state": run.state.value,
                    "updated_at": datetime.now(UTC),
                }
            )
            return run

    def save_execution(self, run_id: UUID, execution: ExecutionReference) -> Run:
        with self._lock:
            run = self.runs[run_id]
            state = RunState.PENDING if run.state is RunState.REQUESTED else run.state
            updated = run.model_copy(update={"execution": execution, "state": state})
            self.runs[run_id] = updated
            session = self.sessions[run.session_id]
            self.sessions[session.id] = session.model_copy(update={"updated_at": datetime.now(UTC)})
            return updated

    def fail_dispatch(self, run_id: UUID, error_code: str) -> Run:
        with self._lock:
            run = self.runs[run_id].model_copy(
                update={"state": RunState.DISPATCH_FAILED, "error_code": error_code}
            )
            self.runs[run_id] = run
            session = self.sessions[run.session_id]
            self.sessions[session.id] = session.model_copy(
                update={
                    "active_run_id": None,
                    "latest_run_state": run.state.value,
                    "updated_at": datetime.now(UTC),
                }
            )
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
            session = self.sessions[run.session_id]
            self.sessions[session.id] = session.model_copy(update={"updated_at": datetime.now(UTC)})
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
                run.state in {RunState.COMPLETED, RunState.TIMED_OUT}
                and run.claude_session_id is not None
                and run.snapshot is not None
            ):
                session_update.update(
                    {
                        "claude_session_id": run.claude_session_id,
                        "snapshot": run.snapshot,
                    }
                )
            self.sessions[session.id] = session.model_copy(update=session_update)
            return run

    def acquire_reconciliation_lease(self, run_id: UUID, holder: str) -> ReconciliationLease | None:
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

    def reconcile_terminal(
        self, run_id: UUID, holder: str, state: RunState, *, error_code: str | None = None
    ) -> Run:
        if not state.terminal:
            raise ValueError("terminal state is required")
        with self._lock:
            lease = self._leases.get(run_id)
            if lease is None or lease.holder != holder:
                raise SessionOwnershipError(str(run_id))
            current = self.runs[run_id]
            if current.state.terminal:
                return current
            run = current.model_copy(
                update={
                    "state": state,
                    "error_code": error_code,
                    "finished_at": datetime.now(UTC),
                }
            )
            self.runs[run_id] = run
            session = self.sessions[run.session_id]
            self.sessions[session.id] = session.model_copy(
                update={
                    "active_run_id": None,
                    "latest_run_state": state.value,
                    "updated_at": datetime.now(UTC),
                }
            )
            return run

    def release_reconciliation_lease(self, run_id: UUID, holder: str) -> None:
        with self._lock:
            lease = self._leases.get(run_id)
            if lease is not None and lease.holder == holder:
                del self._leases[run_id]

    def create_questions(
        self, run_id: UUID, questions: list[QuestionRequest]
    ) -> list[QuestionRequest]:
        """Create one SDK question batch idempotently before waiting for answers."""
        ordered = validate_question_batch(questions)
        if any(question.run_id != run_id for question in ordered):
            raise ValueError("question run IDs do not match")
        with self._lock:
            run = self.runs.get(run_id)
            if run is None:
                raise QuestionNotFoundError(str(run_id))
            if run.state not in {
                RunState.REQUESTED,
                RunState.DISPATCHING,
                RunState.PENDING,
                RunState.RUNNING,
            }:
                raise QuestionClosedError(str(run_id))
            current = self._questions.setdefault(run_id, {})
            result: list[QuestionRequest] = []
            new: list[QuestionRequest] = []
            for question in ordered:
                existing = current.get(question.id)
                if existing is not None:
                    if (
                        existing.question != question.question
                        or existing.options != question.options
                    ):
                        raise QuestionConflictError(question.id)
                    result.append(existing)
                    continue
                current[question.id] = question
                result.append(question)
                new.append(question)
        for question in new:
            self.append_event(
                ChatEvent(
                    id=f"question_pending:{question.id}",
                    run_id=run_id,
                    sequence=0,
                    type="question_pending",
                    occurred_at=question.created_at,
                    payload=question.model_dump(mode="json"),
                )
            )
        return result

    # Singular aliases are useful for SDK callbacks and contract tests.
    def create_question(self, question: QuestionRequest) -> QuestionRequest:
        return self.create_questions(question.run_id, [question])[0]

    def _visible_question(self, question: QuestionRequest) -> bool:
        return question.expires_at is None or question.expires_at > datetime.now(UTC)

    def list_questions_for_job(self, run_id: UUID) -> list[QuestionRequest]:
        with self._lock:
            run = self.runs.get(run_id)
            if run is None:
                raise QuestionNotFoundError(str(run_id))
            return [
                question
                for question in sorted(
                    self._questions.get(run_id, {}).values(), key=lambda value: value.ordinal
                )
                if self._visible_question(question)
            ]

    list_questions_for_run = list_questions_for_job

    def get_question_for_job(self, run_id: UUID, question_id: str) -> QuestionRequest:
        with self._lock:
            question = self._questions.get(run_id, {}).get(question_id)
            if question is None or not self._visible_question(question):
                raise QuestionNotFoundError(question_id)
            return question

    def list_questions(
        self, user_id: str, session_id: str, run_id: UUID
    ) -> list[QuestionRequest]:
        with self._lock:
            run = self.runs.get(run_id)
            if run is None:
                raise QuestionNotFoundError(str(run_id))
            if run.user_id != user_id or run.session_id != session_id:
                raise QuestionOwnershipError(str(run_id))
        return self.list_questions_for_job(run_id)

    def _answer_question(
        self, run_id: UUID, question_id: str, answers: str | list[str], idempotency_key: str
    ) -> QuestionRequest:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key must not be blank")
        with self._lock:
            run = self.runs.get(run_id)
            question = self._questions.get(run_id, {}).get(question_id)
            if run is None or question is None or not self._visible_question(question):
                raise QuestionNotFoundError(question_id)
            if run.state not in {
                RunState.REQUESTED,
                RunState.DISPATCHING,
                RunState.PENDING,
                RunState.RUNNING,
            }:
                raise QuestionClosedError(question_id)
            values = question.validate_answers(answers)
            if question.state is QuestionState.ANSWERED:
                if (
                    question.answer_idempotency_key == idempotency_key
                    and question.answers == values
                ):
                    return question
                raise QuestionConflictError(question_id)
            answered = question.model_copy(
                update={
                    "state": QuestionState.ANSWERED,
                    "answers": values,
                    "answer_idempotency_key": idempotency_key,
                    "answered_at": datetime.now(UTC),
                }
            )
            self._questions[run_id][question_id] = answered
            callbacks = list(self._question_subscriptions.get((run_id, question_id), []))
        self.append_event(
            ChatEvent(
                id=f"question_answered:{question_id}",
                run_id=run_id,
                sequence=0,
                type="question_answered",
                occurred_at=answered.answered_at or datetime.now(UTC),
                payload=answered.model_dump(mode="json"),
            )
        )
        for callback in callbacks:
            callback(answered)
        return answered

    def answer_question_for_job(
        self, run_id: UUID, question_id: str, answers: str | list[str], idempotency_key: str
    ) -> QuestionRequest:
        return self._answer_question(run_id, question_id, answers, idempotency_key)

    def answer_question(
        self,
        user_id: str,
        session_id: str,
        run_id: UUID,
        question_id: str,
        answers: str | list[str],
        idempotency_key: str,
    ) -> QuestionRequest:
        with self._lock:
            run = self.runs.get(run_id)
            if run is None or run.user_id != user_id or run.session_id != session_id:
                raise QuestionOwnershipError(question_id)
        return self._answer_question(run_id, question_id, answers, idempotency_key)

    answer_question_owned = answer_question

    def get_question(
        self, user_id: str, session_id: str, run_id: UUID, question_id: str
    ) -> QuestionRequest:
        with self._lock:
            run = self.runs.get(run_id)
            if run is None or run.user_id != user_id or run.session_id != session_id:
                raise QuestionOwnershipError(question_id)
        return self.get_question_for_job(run_id, question_id)

    def subscribe_question(
        self, run_id: UUID, question_id: str, callback: Callable[[QuestionRequest], None]
    ) -> Callable[[], None]:
        question = self.get_question_for_job(run_id, question_id)
        if question.state is QuestionState.ANSWERED:
            callback(question)
        key = (run_id, question_id)
        with self._lock:
            callbacks = self._question_subscriptions.setdefault(key, [])
            callbacks.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                values = self._question_subscriptions.get(key, [])
                if callback in values:
                    values.remove(callback)

        return unsubscribe

    subscribe_question_answer = subscribe_question

    @staticmethod
    def _encode_run_cursor(created_at: datetime, run_id: str) -> str:
        value = json.dumps([created_at.isoformat(), run_id]).encode("utf-8")
        return base64.urlsafe_b64encode(value).decode("ascii")

    @staticmethod
    def _decode_run_cursor(cursor: str) -> tuple[datetime, str]:
        try:
            timestamp, run_id = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
            return datetime.fromisoformat(timestamp).astimezone(UTC), str(run_id)
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            raise ValueError("invalid run cursor") from error
