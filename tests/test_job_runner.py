import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest

from cas_hosting_adapter.errors import AgentError
from cas_hosting_adapter.in_memory_chat_store import InMemoryChatStore
from cas_hosting_adapter.job_runner import ExecutionLimits, JobInvocation, JobRunner
from cas_hosting_adapter.models import ChatEvent, Run, RunState, Session, WorkspaceReference


def test_job_runner_validates_and_claims_a_run_once() -> None:
    store = InMemoryChatStore()
    session = store.put_session(Session(id="s", user_id="u", workspace_id="w"))
    run = Run(user_id="u", session_id=session.id, workspace_id="w", idempotency_key="k")
    store.reserve_run(run, ChatEvent(id="input", run_id=run.id, sequence=0, type="user"))
    invocation = JobInvocation.from_environment({
        "RUN_ID": str(run.id), "CLOUD_RUN_EXECUTION": "execution-1",
    })

    runner = JobRunner(store)
    assert runner.claim(invocation).state is RunState.RUNNING
    assert runner.claim(JobInvocation(run.id, "execution-2")) is None


def test_job_runner_rejects_invalid_environment() -> None:
    with pytest.raises(AgentError):
        JobInvocation.from_environment({"RUN_ID": str(uuid4())})


@pytest.mark.asyncio
async def test_job_runner_persists_events_and_stops_at_cancel_request() -> None:
    store = InMemoryChatStore()
    store.put_session(Session(id="s", user_id="u", workspace_id="w"))
    run = Run(user_id="u", session_id="s", workspace_id="w", idempotency_key="k")
    store.reserve_run(run, ChatEvent(id="input", run_id=run.id, sequence=0, type="user"))
    invocation = JobInvocation(run.id, "execution-1")

    async def events():
        yield {"event_id": "agent-1", "event_type": "agent", "payload": {"content": "hi"}}

    assert await JobRunner(store).persist_events(invocation, events()) is RunState.RUNNING
    assert [event.id for event in store.list_events(run.id)] == ["input", "agent-1"]


def test_job_runner_reads_prompt_from_durable_user_event() -> None:
    store = InMemoryChatStore()
    store.put_session(Session(id="s", user_id="u", workspace_id="w"))
    run = Run(user_id="u", session_id="s", workspace_id="w", idempotency_key="k")
    store.reserve_run(
        run,
        ChatEvent(
            id="input", run_id=run.id, sequence=0, type="user", payload={"content": "hi"}
        ),
    )
    assert JobRunner(store).prompt_for_run(run.id) == "hi"


@pytest.mark.asyncio
async def test_job_runner_times_out_an_idle_agent() -> None:
    store = InMemoryChatStore()
    store.put_session(Session(id="s", user_id="u", workspace_id="w"))
    run = Run(user_id="u", session_id="s", workspace_id="w", idempotency_key="k")
    store.reserve_run(run, ChatEvent(id="input", run_id=run.id, sequence=0, type="user"))

    async def idle_events():
        await asyncio.sleep(1)
        yield {"event_id": "late", "event_type": "agent", "payload": {}}

    state = await JobRunner(store).persist_events(
        JobInvocation(run.id, "execution-1"),
        idle_events(),
        limits=ExecutionLimits(timedelta(seconds=5), timedelta(milliseconds=1)),
    )
    assert state is RunState.TIMED_OUT


@pytest.mark.asyncio
async def test_job_runner_stops_without_persisting_events_after_sigterm() -> None:
    store = InMemoryChatStore()
    store.put_session(Session(id="s", user_id="u", workspace_id="w"))
    run = Run(user_id="u", session_id="s", workspace_id="w", idempotency_key="k")
    store.reserve_run(run, ChatEvent(id="input", run_id=run.id, sequence=0, type="user"))

    async def events():
        yield {"event_id": "agent-1", "event_type": "agent", "payload": {}}

    runner = JobRunner(store)
    runner.request_shutdown()
    state = await runner.persist_events(JobInvocation(run.id, "execution-1"), events())
    assert state is RunState.CANCELLED
    assert [event.id for event in store.list_events(run.id)] == ["input"]


def test_job_runner_commits_final_event_before_completed_snapshot() -> None:
    store = InMemoryChatStore()
    store.put_session(Session(id="s", user_id="u", workspace_id="w"))
    run = Run(user_id="u", session_id="s", workspace_id="w", idempotency_key="k")
    store.reserve_run(run, ChatEvent(id="input", run_id=run.id, sequence=0, type="user"))
    invocation = JobInvocation(run.id, "execution-1")
    assert JobRunner(store).claim(invocation) is not None

    committed = JobRunner(store).commit_success(
        invocation,
        result="done",
        snapshot=WorkspaceReference(
            object_key="snapshot", version="1", sha256="0" * 64, size=1
        ),
        claude_session_id="sdk-session",
    )

    assert committed.state is RunState.COMPLETED
    assert committed.snapshot is not None
    assert store.list_events(run.id)[-1].type == "final"


def test_job_runner_reuses_sdk_final_event_when_committing_success() -> None:
    store = InMemoryChatStore()
    store.put_session(Session(id="s", user_id="u", workspace_id="w"))
    run = Run(user_id="u", session_id="s", workspace_id="w", idempotency_key="k")
    store.reserve_run(run, ChatEvent(id="input", run_id=run.id, sequence=0, type="user"))
    invocation = JobInvocation(run.id, "execution-1")
    runner = JobRunner(store)
    assert runner.claim(invocation) is not None
    store.append_event(ChatEvent(
        id="sdk-final", run_id=run.id, sequence=0, type="final", payload={"output": "done"}
    ))

    committed = runner.commit_success(
        invocation,
        result=runner.result_for_run(run.id),
        snapshot=WorkspaceReference(object_key="snapshot", version="1", sha256="0" * 64, size=1),
        claude_session_id=None,
    )

    assert committed.state is RunState.COMPLETED
    assert [event.id for event in store.list_events(run.id)] == ["input", "sdk-final"]


def test_unsuccessful_commit_discards_uncommitted_snapshot() -> None:
    store = InMemoryChatStore()
    store.put_session(Session(id="s", user_id="u", workspace_id="w"))
    run = Run(user_id="u", session_id="s", workspace_id="w", idempotency_key="k")
    store.reserve_run(run, ChatEvent(id="input", run_id=run.id, sequence=0, type="user"))
    invocation = JobInvocation(run.id, "execution-1")
    runner = JobRunner(store)
    assert runner.claim(invocation) is not None

    committed = runner.commit_unsuccessful(invocation, RunState.CANCELLED, error_code="sigterm")

    assert committed.state is RunState.CANCELLED
    assert committed.snapshot is None
