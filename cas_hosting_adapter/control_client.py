"""High-level control-plane API over provider-neutral ports."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from .errors import ExecutionNotFoundError, ExecutionTemporaryError
from .models import (
    ChatEvent,
    ExecutionState,
    InitialSessionResult,
    QuestionRequest,
    Run,
    RunPage,
    RunState,
    Session,
    SessionPage,
    derive_run_id,
    derive_session_id,
    derive_workspace_id,
    normalize_session_title,
)
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

    def start_session(
        self, user_id: str, prompt: str, idempotency_key: str
    ) -> InitialSessionResult:
        """Reserve and dispatch a session whose first run contains ``prompt``."""
        if not prompt.strip():
            raise ValueError("prompt must not be blank")
        now = datetime.now(UTC)
        session = Session(
            id=derive_session_id(user_id, idempotency_key),
            user_id=user_id,
            workspace_id=derive_workspace_id(user_id, idempotency_key),
            title=normalize_session_title(prompt),
            created_at=now,
            updated_at=now,
        )
        run = Run(
            id=derive_run_id(user_id, idempotency_key),
            user_id=user_id,
            session_id=session.id,
            workspace_id=session.workspace_id,
            idempotency_key=idempotency_key,
            created_at=now,
        )
        event = ChatEvent(
            id=f"user:{run.id}",
            run_id=run.id,
            sequence=0,
            type="user",
            occurred_at=now,
            payload={"content": prompt},
        )
        reserved = self._chat_store.reserve_initial_run(session, run, event)
        started = self._start_reserved(reserved.run, prompt)
        return InitialSessionResult(
            session=self._chat_store.get_session(user_id, session.id), run=started
        )

    # Keep a descriptive alias available to non-UI callers.
    def start_initial_session(
        self, user_id: str, prompt: str, idempotency_key: str
    ) -> InitialSessionResult:
        return self.start_session(user_id, prompt, idempotency_key)

    def list_runs(
        self, user_id: str, session_id: str, *, cursor: str | None, limit: int
    ) -> RunPage:
        return self._chat_store.list_runs(user_id, session_id, cursor=cursor, limit=limit)

    def list_run_history(
        self, user_id: str, session_id: str, *, cursor: str | None, limit: int
    ) -> RunPage:
        return self.list_runs(user_id, session_id, cursor=cursor, limit=limit)

    def reserve_and_start(self, run: Run, message: str) -> Run:
        reserved = self._chat_store.reserve_run(
            run,
            ChatEvent(
                id=f"user:{run.id}",
                run_id=run.id,
                sequence=0,
                type="user",
                payload={"content": message},
            ),
        )
        return self._start_reserved(reserved, message)

    def _start_reserved(self, reserved: Run, message: str) -> Run:
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

    def latest_event(self, run_id: UUID) -> ChatEvent | None:
        return self._chat_store.latest_event(run_id)

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

    def list_questions(
        self, user_id: str, session_id: str, run_id: UUID
    ) -> list[QuestionRequest]:
        """List durable interaction state inside the caller's ownership boundary."""
        return self._chat_store.list_questions(user_id, session_id, run_id)

    def answer_question(
        self,
        user_id: str,
        session_id: str,
        run_id: UUID,
        question_id: str,
        answers: str | list[str],
        idempotency_key: str,
    ) -> QuestionRequest:
        return self._chat_store.answer_question(
            user_id, session_id, run_id, question_id, answers, idempotency_key
        )

    def create_questions_for_job(
        self, run_id: UUID, questions: list[QuestionRequest]
    ) -> list[QuestionRequest]:
        return self._chat_store.create_questions(run_id, questions)

    def questions_for_job(self, run_id: UUID) -> list[QuestionRequest]:
        return self._chat_store.list_questions_for_job(run_id)

    def answer_question_for_job(
        self, run_id: UUID, question_id: str, answers: str | list[str], idempotency_key: str
    ) -> QuestionRequest:
        return self._chat_store.answer_question_for_job(
            run_id, question_id, answers, idempotency_key
        )

    def cancel(self, run_id: UUID) -> Run:
        run = self._chat_store.request_cancel(run_id)
        if run.execution is not None:
            try:
                state = self._execution_backend.cancel(run.execution)
            except ExecutionNotFoundError:
                # request_cancel is durable and precedes provider deletion. If a
                # foreground delete has already removed the provider object,
                # the durable cancel request is the source of truth.
                lease = self._chat_store.acquire_reconciliation_lease(run_id, "control-cancel")
                if lease is None:
                    return self._chat_store.get_run_for_job(run_id)
                return self._chat_store.reconcile_terminal(
                    run_id,
                    "control-cancel",
                    RunState.CANCELLED,
                    error_code="execution_cancelled_after_deletion",
                )
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
        try:
            status = self._execution_backend.get(run.execution)
        except ExecutionTemporaryError:
            self._chat_store.release_reconciliation_lease(run_id, holder)
            return run
        except ExecutionNotFoundError:
            cancelled = run.state is RunState.CANCEL_REQUESTED
            result = self._chat_store.reconcile_terminal(
                run_id,
                holder,
                RunState.CANCELLED if cancelled else RunState.FAILED,
                error_code=(
                    "execution_cancelled_after_deletion"
                    if cancelled
                    else (
                        f"{getattr(self._execution_backend, 'backend_name', 'cloud_run')}_"
                        "execution_not_found"
                    )
                ),
            )
            self._chat_store.release_reconciliation_lease(run_id, holder)
            return result
        except Exception:
            self._chat_store.release_reconciliation_lease(run_id, holder)
            raise
        target = {
            ExecutionState.CANCELLED: RunState.CANCELLED,
            ExecutionState.FAILED: RunState.FAILED,
        }.get(status)
        if status is ExecutionState.SUCCEEDED:
            events = self._chat_store.list_events(run_id)
            has_final = any(event.type == "final" for event in events)
            target = RunState.PERSISTENCE_FAILED if run.snapshot is None or not has_final else None
        if target is None:
            self._chat_store.release_reconciliation_lease(run_id, holder)
            return run
        error_code = {
            RunState.FAILED: "cloud_run_execution_failed",
            RunState.CANCELLED: "cloud_run_execution_cancelled",
            RunState.PERSISTENCE_FAILED: "persistence_failed",
        }[target]
        result = self._chat_store.reconcile_terminal(run_id, holder, target, error_code=error_code)
        self._chat_store.release_reconciliation_lease(run_id, holder)
        return result
