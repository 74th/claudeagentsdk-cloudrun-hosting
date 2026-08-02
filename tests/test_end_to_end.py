"""In-memory user journey across the control plane and one JobRunner."""
from __future__ import annotations

import pytest

from cas_hosting_adapter.control_client import ControlClient
from cas_hosting_adapter.in_memory_chat_store import InMemoryChatStore
from cas_hosting_adapter.job_runner import JobInvocation, JobRunner
from cas_hosting_adapter.models import Run, RunState
from cas_hosting_adapter.protocols import InMemoryExecutionBackend, InMemoryWorkspaceStore
from cas_hosting_adapter.workspace_store import (
    create_workspace_snapshot,
    request_directories,
    restore_workspace_snapshot,
)


@pytest.mark.asyncio
async def test_user_can_resume_snapshot_then_start_after_cancel() -> None:
    """Exercise reservation, live events, a committed snapshot, and cancellation."""
    chat_store = InMemoryChatStore()
    execution_backend = InMemoryExecutionBackend()
    workspace_store = InMemoryWorkspaceStore()
    client = ControlClient(chat_store, execution_backend)
    session = client.create_session("user-1")

    received_event_ids: list[str] = []
    first_run = client.reserve_and_start(
        Run(
            user_id="user-1",
            session_id=session.id,
            workspace_id=session.workspace_id,
            idempotency_key="first",
        ),
        "remember this",
    )
    unsubscribe = client.subscribe_from_cursor(
        first_run.id, None, lambda event: received_event_ids.append(event.id)
    )
    invocation = JobInvocation(first_run.id, "execution-1")

    async def agent_events():
        yield {"event_id": "agent-1", "event_type": "agent", "payload": {"content": "ok"}}

    state = await JobRunner(chat_store).persist_events(invocation, agent_events())
    assert state is RunState.RUNNING
    with request_directories() as source:
        (source.workspace / "memory.txt").write_text("remembered", encoding="utf-8")
        snapshot, manifest = create_workspace_snapshot(
            workspace_store,
            object_key=f"snapshots/{first_run.id}.tar.gz",
            source=source,
            run_id=first_run.id,
            sdk_version="test-sdk",
            max_bytes=1024 * 1024,
        )
    committed = JobRunner(chat_store).commit_success(
        invocation,
        result="ok",
        snapshot=snapshot,
        claude_session_id="claude-1",
    )
    unsubscribe()

    assert committed.state is RunState.COMPLETED
    persisted_session = chat_store.get_session("user-1", session.id)
    assert persisted_session.claude_session_id == "claude-1"
    assert persisted_session.snapshot == snapshot
    assert received_event_ids == [f"user:{first_run.id}", "agent-1", f"final:{first_run.id}"]

    with request_directories() as resumed:
        restore_workspace_snapshot(
            workspace_store,
            committed.snapshot,
            manifest,
            resumed,
            max_bytes=1024 * 1024,
            expected_schema_version="1",
            expected_sdk_version="test-sdk",
        )
        assert (resumed.workspace / "memory.txt").read_text(encoding="utf-8") == "remembered"

    cancelled_run = client.reserve_and_start(
        Run(
            user_id="user-1",
            session_id=session.id,
            workspace_id=session.workspace_id,
            idempotency_key="cancelled",
        ),
        "cancel me",
    )
    assert client.cancel(cancelled_run.id).state is RunState.CANCELLED
    assert JobRunner(chat_store).claim(JobInvocation(cancelled_run.id, "execution-2")) is None

    next_run = client.reserve_and_start(
        Run(
            user_id="user-1",
            session_id=session.id,
            workspace_id=session.workspace_id,
            idempotency_key="after-cancel",
        ),
        "continue",
    )
    assert next_run.id != cancelled_run.id
    assert next_run.state is RunState.PENDING
