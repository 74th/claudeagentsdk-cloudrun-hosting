from uuid import uuid4

import pytest

from cas_hosting_adapter.errors import AgentError, WorkspaceError
from cas_hosting_adapter.lifecycle import RunLifecycle
from cas_hosting_adapter.models import SessionEvent
from cas_hosting_adapter.protocols import InMemorySnapshotStore


async def test_lifecycle_emits_ordered_success_events() -> None:
    events: list[SessionEvent] = []

    async def agent(workspace, transcript):
        (workspace / "result.txt").write_text("done")
        transcript.mkdir(exist_ok=True)
        return {"output": "answer"}

    output = await RunLifecycle(append_event=events.append, execute_agent=agent).run(
        run_id=uuid4(), message="Hello"
    )
    assert output == "answer"
    assert [event.event_type for event in events] == ["run_started", "agent_message", "completed"]
    assert [event.sequence for event in events] == [0, 1, 2]


async def test_lifecycle_emits_failed_without_leaking_exception() -> None:
    events: list[SessionEvent] = []

    async def failing_agent(workspace, transcript):
        del workspace, transcript
        raise RuntimeError("internal detail")

    with pytest.raises(AgentError):
        await RunLifecycle(append_event=events.append, execute_agent=failing_agent).run(
            run_id=uuid4(), message="Hello"
        )
    assert events[-1].event_type == "failed"
    assert "internal detail" not in str(events[-1].payload)


async def test_lifecycle_commits_snapshot_before_completed() -> None:
    events: list[SessionEvent] = []
    store = InMemorySnapshotStore()
    run_id = uuid4()

    async def agent(workspace, transcript):
        (workspace / "state.txt").write_text("restorable")
        (transcript / "conversation.jsonl").write_text("[]")
        return {"output": "answer"}

    output = await RunLifecycle(
        append_event=events.append,
        execute_agent=agent,
        snapshot_store=store,
        snapshot_path=lambda identifier: f"snapshots/{identifier}.tar.gz",
    ).run(run_id=run_id, message="Hello")

    assert output == "answer"
    assert [event.event_type for event in events] == [
        "run_started", "agent_message", "snapshot_committed", "completed"
    ]
    committed = events[-2].payload
    assert store.download(committed["object_path"], committed["generation"])


async def test_lifecycle_does_not_complete_when_snapshot_commit_fails() -> None:
    events: list[SessionEvent] = []

    class FailingStore(InMemorySnapshotStore):
        def upload(self, object_path, data, *, if_generation_match):
            del object_path, data, if_generation_match
            raise OSError("provider detail")

    async def agent(workspace, transcript):
        (workspace / "state.txt").write_text("state")
        del transcript
        return {"output": "answer"}

    with pytest.raises(WorkspaceError):
        await RunLifecycle(
            append_event=events.append,
            execute_agent=agent,
            snapshot_store=FailingStore(),
            snapshot_path=lambda identifier: f"snapshots/{identifier}.tar.gz",
        ).run(run_id=uuid4(), message="Hello")
    assert [event.event_type for event in events][-1] == "persistence_failed"
    assert "completed" not in [event.event_type for event in events]
