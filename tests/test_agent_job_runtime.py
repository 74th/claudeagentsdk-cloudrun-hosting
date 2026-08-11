import asyncio
import logging
import sys
from datetime import timedelta
from pathlib import Path
from types import ModuleType

import pytest

from cas_hosting_adapter import ClaudeAgentConfig, RuntimePolicy
from cas_hosting_adapter.agent_adapter import ClaudeAgentAdapter
from cas_hosting_adapter.in_memory_chat_store import InMemoryChatStore
from cas_hosting_adapter.job_runner import JobInvocation
from cas_hosting_adapter.models import ChatEvent, Run, RunState, Session
from cas_hosting_adapter.protocols import InMemoryWorkspaceStore


class ResultMessage:
    uuid = "result"
    session_id = "claude-session"
    result = "done"
    is_error = False
    total_cost_usd = None
    duration_ms = None


class FakeOptions:
    captured: list[dict[str, object]] = []

    def __init__(self, **values: object) -> None:
        self.values = values
        self.captured.append(values)


@pytest.fixture
def fake_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("claude_agent_sdk")
    module.ClaudeAgentOptions = FakeOptions  # type: ignore[attr-defined]
    module.ResultMessage = ResultMessage  # type: ignore[attr-defined]

    async def query(*, prompt: str, options: FakeOptions):
        assert prompt
        yield ResultMessage()

    module.query = query  # type: ignore[attr-defined]
    FakeOptions.captured.clear()
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", module)


def _new_run(store: InMemoryChatStore, *, session: Session, key: str) -> Run:
    run = Run(
        user_id=session.user_id,
        session_id=session.id,
        workspace_id=session.workspace_id,
        idempotency_key=key,
    )
    return store.reserve_run(
        run,
        ChatEvent(
            id=f"input-{key}",
            run_id=run.id,
            sequence=0,
            type="user",
            payload={"content": "hello"},
        ),
    )


@pytest.mark.asyncio
async def test_run_job_owns_lifecycle_and_applies_setup_once_per_run(fake_sdk: None) -> None:
    store = InMemoryChatStore()
    workspace_store = InMemoryWorkspaceStore()
    session = store.put_session(Session(id="s", user_id="u", workspace_id="w"))
    calls: list[tuple[str, Path]] = []

    def initializer(path: Path) -> None:
        calls.append(("initializer", path))

    def setup(path: Path) -> None:
        calls.append(("setup", path))

    config = ClaudeAgentConfig(system_prompt="system", model="model", allowed_tools=("Read",))
    adapter = ClaudeAgentAdapter(
        chat_store=store,
        workspace_store=workspace_store,
        agent_config=config,
        runtime_policy=RuntimePolicy(),
        workspace_initializer=initializer,
        workspace_setup=setup,
    )
    first = _new_run(store, session=session, key="first")
    assert await adapter.run_job(JobInvocation(first.id, "execution-1")) == 0
    assert [name for name, _ in calls] == ["initializer", "setup"]
    assert all(not path.exists() for _, path in calls)

    second = _new_run(store, session=session, key="second")
    assert await adapter.run_job(JobInvocation(second.id, "execution-2")) == 0
    assert [name for name, _ in calls] == ["initializer", "setup", "setup"]
    assert store.get_run_for_job(second.id).state is RunState.COMPLETED
    assert FakeOptions.captured[-1]["system_prompt"] == "system"
    assert FakeOptions.captured[-1]["allowed_tools"] == ["Read"]


@pytest.mark.asyncio
async def test_run_job_notifies_usage_after_terminal_commit(
    fake_sdk: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ResultMessage, "total_cost_usd", 0.012345)
    monkeypatch.setattr(ResultMessage, "duration_ms", 1234)
    store = InMemoryChatStore()
    workspace_store = InMemoryWorkspaceStore()
    session = store.put_session(
        Session(id="s", user_id="user@example.com", workspace_id="w", title="Repo task")
    )
    run = _new_run(store, session=session, key="usage")
    records = []

    adapter = ClaudeAgentAdapter(
        chat_store=store,
        workspace_store=workspace_store,
        agent_config=ClaudeAgentConfig(model="model"),
        usage_hook=records.append,
    )

    assert await adapter.run_job(JobInvocation(run.id, "execution-1")) == 0

    committed = store.get_run_for_job(run.id)
    assert committed.state is RunState.COMPLETED
    assert len(records) == 1
    record = records[0]
    assert record.user_name == "user@example.com"
    assert record.run_id == run.id
    assert record.session_name == "Repo task"
    assert record.estimated_cost_usd == 0.012345
    assert record.duration_ms == 1234
    assert record.recorded_at == committed.finished_at
    assert record.recorded_at.tzinfo is not None


@pytest.mark.asyncio
async def test_usage_hook_failure_does_not_change_success_or_exit_code(
    fake_sdk: None, caplog: pytest.LogCaptureFixture
) -> None:
    store = InMemoryChatStore()
    workspace_store = InMemoryWorkspaceStore()
    session = store.put_session(Session(id="s", user_id="u", workspace_id="w"))
    run = _new_run(store, session=session, key="hook-failure")

    def failing_hook(_record: object) -> None:
        raise RuntimeError("telemetry unavailable")

    caplog.set_level(logging.ERROR, logger="cas_hosting_adapter.agent_adapter")
    adapter = ClaudeAgentAdapter(
        chat_store=store,
        workspace_store=workspace_store,
        agent_config=ClaudeAgentConfig(model="model"),
        usage_hook=failing_hook,
    )

    assert await adapter.run_job(JobInvocation(run.id, "execution-1")) == 0
    assert store.get_run_for_job(run.id).state is RunState.COMPLETED
    assert f"agent.usage_hook.failed run_id={run.id}" in caplog.text


@pytest.mark.asyncio
async def test_failed_run_notifies_usage_from_persisted_error_event(
    fake_sdk: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ResultMessage, "is_error", True)
    monkeypatch.setattr(ResultMessage, "total_cost_usd", 0.045)
    monkeypatch.setattr(ResultMessage, "duration_ms", 2500)
    store = InMemoryChatStore()
    workspace_store = InMemoryWorkspaceStore()
    session = store.put_session(Session(id="s", user_id="u", workspace_id="w", title="Failed task"))
    run = _new_run(store, session=session, key="failed-usage")
    records = []
    adapter = ClaudeAgentAdapter(
        chat_store=store,
        workspace_store=workspace_store,
        agent_config=ClaudeAgentConfig(model="model"),
        usage_hook=records.append,
    )

    assert await adapter.run_job(JobInvocation(run.id, "execution-1")) == 1
    assert store.get_run_for_job(run.id).state is RunState.FAILED
    assert len(records) == 1
    assert records[0].session_name == "Failed task"
    assert records[0].estimated_cost_usd == 0.045
    assert records[0].duration_ms == 2500


@pytest.mark.asyncio
async def test_cancelled_run_notifies_once_with_missing_sdk_usage(fake_sdk: None) -> None:
    store = InMemoryChatStore()
    workspace_store = InMemoryWorkspaceStore()
    session = store.put_session(Session(id="s", user_id="u", workspace_id="w", title="Cancelled"))
    run = _new_run(store, session=session, key="cancelled-usage")
    records = []

    def request_cancel(_path: Path) -> None:
        store.request_cancel(run.id)

    adapter = ClaudeAgentAdapter(
        chat_store=store,
        workspace_store=workspace_store,
        agent_config=ClaudeAgentConfig(model="model"),
        workspace_setup=request_cancel,
        usage_hook=records.append,
    )

    assert await adapter.run_job(JobInvocation(run.id, "execution-1")) == 1
    assert store.get_run_for_job(run.id).state is RunState.CANCELLED
    assert len(records) == 1
    assert records[0].estimated_cost_usd is None
    assert records[0].duration_ms is None


@pytest.mark.asyncio
async def test_timed_out_run_notifies_with_missing_sdk_usage(
    fake_sdk: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = sys.modules["claude_agent_sdk"]

    async def delayed_query(*, prompt: str, options: FakeOptions):
        del prompt, options
        await asyncio.sleep(0.1)
        yield ResultMessage()

    monkeypatch.setattr(module, "query", delayed_query)
    store = InMemoryChatStore()
    workspace_store = InMemoryWorkspaceStore()
    session = store.put_session(Session(id="s", user_id="u", workspace_id="w", title="Timeout"))
    run = _new_run(store, session=session, key="timeout-usage")
    records = []
    adapter = ClaudeAgentAdapter(
        chat_store=store,
        workspace_store=workspace_store,
        agent_config=ClaudeAgentConfig(model="model"),
        runtime_policy=RuntimePolicy(
            max_runtime=timedelta(milliseconds=1), idle_timeout=timedelta(seconds=1)
        ),
        usage_hook=records.append,
    )

    assert await adapter.run_job(JobInvocation(run.id, "execution-1")) == 1
    assert store.get_run_for_job(run.id).state is RunState.TIMED_OUT
    assert len(records) == 1
    assert records[0].estimated_cost_usd is None
    assert records[0].duration_ms is None


@pytest.mark.asyncio
async def test_setup_failure_is_failed_without_success_snapshot(fake_sdk: None) -> None:
    store = InMemoryChatStore()
    workspace_store = InMemoryWorkspaceStore()
    session = store.put_session(Session(id="s", user_id="u", workspace_id="w"))
    run = _new_run(store, session=session, key="failure")

    def setup(_path: Path) -> None:
        raise RuntimeError("setup failed")

    adapter = ClaudeAgentAdapter(
        chat_store=store,
        workspace_store=workspace_store,
        agent_config=ClaudeAgentConfig(model="model"),
        workspace_setup=setup,
    )
    assert await adapter.run_job(JobInvocation(run.id, "execution-1")) == 1
    failed = store.get_run_for_job(run.id)
    assert failed.state is RunState.FAILED
    assert failed.snapshot is None
    assert not FakeOptions.captured
