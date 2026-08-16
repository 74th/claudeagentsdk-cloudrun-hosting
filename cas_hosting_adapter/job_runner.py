"""Job entry boundary; validates only non-secret run coordinates."""
from __future__ import annotations

import asyncio
import logging
import os
import signal
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from .errors import AgentError
from .models import ChatEvent, EventCursor, Run, RunState, WorkspaceReference
from .protocols import ChatStore

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class JobInvocation:
    run_id: UUID
    execution_identity: str

    @classmethod
    def from_environment(cls, environment: dict[str, str] | None = None) -> JobInvocation:
        values = os.environ if environment is None else environment
        try:
            run_id = UUID(values["RUN_ID"])
            execution_identity = values["CLOUD_RUN_EXECUTION"].strip()
        except (KeyError, ValueError) as error:
            raise AgentError("invalid JobRunner invocation environment") from error
        if not execution_identity:
            raise AgentError("JobRunner invocation values must not be blank")
        return cls(run_id, execution_identity)


@dataclass(frozen=True)
class ExecutionLimits:
    max_runtime: timedelta
    idle_timeout: timedelta

    def __post_init__(self) -> None:
        if self.max_runtime.total_seconds() <= 0 or self.idle_timeout.total_seconds() <= 0:
            raise ValueError("execution limits must be positive")


class JobRunner:
    def __init__(self, chat_store: ChatStore) -> None:
        self._chat_store = chat_store
        self._shutdown_requested = False
        self.claude_session_id: str | None = None

    def install_sigterm_handler(self) -> None:
        """Request cooperative shutdown; request_directories handles local cleanup."""
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, self.request_shutdown)

    def request_shutdown(self) -> None:
        self._shutdown_requested = True

    def claim(self, invocation: JobInvocation) -> Run | None:
        """Return the durable run only for its single winning execution."""
        run = self._chat_store.get_run_for_job(invocation.run_id)
        if run.state is RunState.CANCEL_REQUESTED or run.state.terminal:
            LOGGER.info("job.claim.skipped run_id=%s state=%s", invocation.run_id, run.state)
            return None
        if not self._chat_store.claim_run(invocation.run_id, invocation.execution_identity):
            LOGGER.info("job.claim.duplicate run_id=%s", invocation.run_id)
            return None
        claimed = self._chat_store.get_run_for_job(invocation.run_id)
        LOGGER.info("job.claim.acquired run_id=%s", invocation.run_id)
        return claimed

    async def persist_events(
        self,
        invocation: JobInvocation,
        events: AsyncIterator[dict[str, object]],
        *,
        claimed_run: Run | None = None,
        limits: ExecutionLimits | None = None,
    ) -> RunState:
        """Persist each normalized agent event while honoring durable cancellation."""
        for_event = claimed_run or self.claim(invocation)
        if for_event is None:
            LOGGER.info("job.events.cancelled_before_start run_id=%s", invocation.run_id)
            return RunState.CANCELLED
        LOGGER.info("job.events.start run_id=%s", invocation.run_id)
        started_at = asyncio.get_running_loop().time()
        iterator = events.__aiter__()
        while True:
            if self._shutdown_requested:
                await self._stop_iterator(iterator)
                LOGGER.info("job.events.sigterm run_id=%s", invocation.run_id)
                return RunState.CANCELLED
            remaining_runtime = None
            if limits is not None:
                elapsed = asyncio.get_running_loop().time() - started_at
                remaining_runtime = limits.max_runtime.total_seconds() - elapsed
                if remaining_runtime <= 0:
                    await self._stop_iterator(iterator)
                    LOGGER.warning("job.events.runtime_timeout run_id=%s", invocation.run_id)
                    return RunState.TIMED_OUT
            try:
                pending_question = False
                try:
                    pending_question = any(
                        question.pending
                        for question in self._chat_store.list_questions_for_job(invocation.run_id)
                    )
                except (AttributeError, KeyError):
                    # Older/fake ChatStore implementations have no question
                    # contract and retain the original idle-timeout behavior.
                    pending_question = False
                timeout = (
                    min(limits.idle_timeout.total_seconds(), 10.0)
                    if limits is not None and pending_question
                    else limits.idle_timeout.total_seconds()
                    if limits is not None
                    else None
                )
                if remaining_runtime is not None:
                    assert timeout is not None
                    timeout = min(timeout, remaining_runtime)
                event = await asyncio.wait_for(iterator.__anext__(), timeout=timeout)
            except StopAsyncIteration:
                break
            except TimeoutError:
                if pending_question and remaining_runtime is not None and remaining_runtime > 0:
                    if not self._chat_store.heartbeat_run(
                        invocation.run_id, invocation.execution_identity
                    ):
                        return RunState.FAILED
                    continue
                await self._stop_iterator(iterator)
                LOGGER.warning("job.events.idle_timeout run_id=%s", invocation.run_id)
                return RunState.TIMED_OUT
            latest = self._chat_store.get_run_for_job(invocation.run_id)
            if latest.state is RunState.CANCEL_REQUESTED:
                LOGGER.info("job.events.cancel_requested run_id=%s", invocation.run_id)
                return RunState.CANCELLED
            if not self._chat_store.heartbeat_run(invocation.run_id, invocation.execution_identity):
                LOGGER.error("job.events.heartbeat_lost run_id=%s", invocation.run_id)
                return RunState.FAILED
            event_id = event.get("event_id")
            event_type = event.get("event_type")
            payload = event.get("payload", {})
            claude_session_id = event.get("claude_session_id")
            if isinstance(claude_session_id, str) and claude_session_id:
                self.claude_session_id = claude_session_id
            if not isinstance(event_id, str) or not isinstance(event_type, str):
                raise AgentError("agent emitted an invalid normalized event")
            if not isinstance(payload, dict):
                raise AgentError("agent event payload must be an object")
            persisted = self._chat_store.append_event(ChatEvent(
                id=event_id,
                run_id=invocation.run_id,
                sequence=0,
                type=event_type,
                occurred_at=datetime.now(UTC),
                payload=payload,
            ))
            LOGGER.info(
                "job.events.persisted run_id=%s sequence=%d type=%s event_id=%s",
                invocation.run_id,
                persisted.sequence,
                event_type,
                event_id,
            )
        LOGGER.info("job.events.stream_ended run_id=%s", invocation.run_id)
        return RunState.RUNNING

    def prompt_for_run(self, run_id: UUID) -> str:
        """Read the durable user input; Job environment never carries prompts."""
        for event in self._chat_store.list_events(run_id):
            content = event.payload.get("content")
            if event.type == "user" and isinstance(content, str):
                return content
        raise AgentError("run has no persisted user prompt")

    def commit_unsuccessful(
        self,
        invocation: JobInvocation,
        state: RunState,
        *,
        error_code: str | None = None,
        snapshot: WorkspaceReference | None = None,
        claude_session_id: str | None = None,
    ) -> Run:
        """Commit a terminal run, optionally preserving a resumable snapshot."""
        if state not in {RunState.FAILED, RunState.CANCELLED, RunState.TIMED_OUT}:
            raise ValueError("unsuccessful terminal state is required")
        current = self._chat_store.get_run_for_job(invocation.run_id)
        update: dict[str, object] = {
            "state": state,
            "error_code": error_code,
            "finished_at": datetime.now(UTC),
        }
        if snapshot is not None:
            update["snapshot"] = snapshot
        if claude_session_id is not None:
            update["claude_session_id"] = claude_session_id
        terminal = current.model_copy(update=update)
        return self._chat_store.commit_terminal(terminal, invocation.execution_identity)

    def commit_success(
        self,
        invocation: JobInvocation,
        *,
        result: str,
        snapshot: WorkspaceReference,
        claude_session_id: str | None,
    ) -> Run:
        """Commit a final event before the immutable snapshot-backed terminal run."""
        current = self._chat_store.get_run_for_job(invocation.run_id)
        existing_final = next(
            (
                event for event in reversed(self._chat_store.list_events(invocation.run_id))
                if event.type == "final" and event.payload.get("output") == result
            ),
            None,
        )
        final_event = existing_final or self._chat_store.append_event(ChatEvent(
            id=f"final:{invocation.run_id}",
            run_id=invocation.run_id,
            sequence=0,
            type="final",
            occurred_at=datetime.now(UTC),
            payload={"output": result},
        ))
        committed = current.model_copy(update={
            "state": RunState.COMPLETED,
            "snapshot": snapshot,
            "claude_session_id": claude_session_id,
            "event_cursor": EventCursor(
                sequence=final_event.sequence, event_id=final_event.id
            ),
            "result": result,
            "finished_at": datetime.now(UTC),
        })
        return self._chat_store.commit_terminal(committed, invocation.execution_identity)

    def result_for_run(self, run_id: UUID) -> str:
        """Return the durable SDK terminal output that is safe to commit."""
        for event in reversed(self._chat_store.list_events(run_id)):
            output = event.payload.get("output")
            if event.type == "final" and isinstance(output, str):
                return output
        raise AgentError("Claude Agent SDK ended without a successful final event")

    @staticmethod
    async def _stop_iterator(iterator: AsyncIterator[dict[str, object]]) -> None:
        closer = getattr(iterator, "aclose", None)
        if closer is not None:
            await closer()
