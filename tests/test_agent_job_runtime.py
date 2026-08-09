import sys
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
