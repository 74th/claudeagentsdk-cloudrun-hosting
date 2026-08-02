from uuid import uuid4

from cas_hosting_adapter.models import (
    ChatEvent,
    EventCursor,
    ExecutionReference,
    Run,
    RunState,
    Session,
    User,
    WorkspaceReference,
    can_transition,
)


def test_provider_neutral_models_keep_all_identifiers_separate() -> None:
    run_id = uuid4()
    user = User(id="alice@example.test", key="a" * 64)
    session = Session(id="session-1", user_id=user.id, workspace_id="workspace-1")
    run = Run(
        id=run_id,
        user_id=user.id,
        session_id=session.id,
        workspace_id=session.workspace_id,
        idempotency_key="message-1",
        execution=ExecutionReference(backend="cloud-run-jobs", name="execution-1"),
        claude_session_id="claude-1",
        snapshot=WorkspaceReference(
            object_key="snapshots/1", version="42", sha256="b" * 64, size=12
        ),
        event_cursor=EventCursor(sequence=3, event_id="event-3"),
    )
    event = ChatEvent(id="event-4", run_id=run_id, sequence=4, type="agent")

    assert run.state is RunState.REQUESTED
    assert run.execution is not None and run.execution.name == "execution-1"
    assert event.run_id == run.id


def test_run_state_machine_has_active_and_terminal_states() -> None:
    assert RunState.DISPATCHING.active
    assert RunState.PENDING.active
    assert RunState.COMPLETED.terminal
    assert can_transition(RunState.REQUESTED, RunState.DISPATCHING)
    assert can_transition(RunState.RUNNING, RunState.COMPLETED)
    assert can_transition(RunState.CANCEL_REQUESTED, RunState.CANCELLED)
    assert not can_transition(RunState.COMPLETED, RunState.RUNNING)
    assert not can_transition(RunState.REQUESTED, RunState.COMPLETED)
