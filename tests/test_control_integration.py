from cas_hosting_adapter.control_client import ControlClient
from cas_hosting_adapter.in_memory_chat_store import InMemoryChatStore
from cas_hosting_adapter.models import ExecutionState, Run, RunState
from cas_hosting_adapter.protocols import InMemoryExecutionBackend, InMemoryWorkspaceStore


def test_control_plane_revisit_duplicate_dispatch_cancel_and_reconcile() -> None:
    store = InMemoryChatStore()
    backend = InMemoryExecutionBackend()
    workspace = InMemoryWorkspaceStore()
    client = ControlClient(store, backend)
    session = client.create_session("user")
    requested = Run(user_id="user", session_id=session.id, workspace_id=session.workspace_id,
                    idempotency_key="key")

    started = client.reserve_and_start(requested, "hello")
    revisited = client.get_run("user", session.id, started.id)
    duplicate = client.reserve_and_start(
        Run(user_id="user", session_id=session.id, workspace_id=session.workspace_id,
            idempotency_key="key"),
        "hello",
    )
    assert duplicate.id == revisited.id
    assert workspace.create("test", b"snapshot").size == len(b"snapshot")

    assert started.execution is not None
    backend.set_state(started.execution, ExecutionState.FAILED)
    assert client.reconcile(started.id, holder="reconciler").state is RunState.FAILED
    assert client.get_session("user", session.id).active_run_id is None
