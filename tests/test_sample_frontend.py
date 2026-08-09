from cas_hosting_adapter.control_client import ControlClient
from cas_hosting_adapter.errors import ExecutionTemporaryError
from cas_hosting_adapter.in_memory_chat_store import InMemoryChatStore
from cas_hosting_adapter.models import ChatEvent, ExecutionState, RunState
from cas_hosting_adapter.protocols import InMemoryExecutionBackend
from example.chat import ChatService
from example.chat.events import InteractionState, normalize_events
from example.streamlit_frontend.app import (
    DRAFT_STATE_KEY,
    SELECTED_SESSION_KEY,
    ChatViewModel,
    ManualIdentity,
    auto_refresh_allowed,
    create_view_from_release_config,
    order_sessions,
    session_label,
)


def test_manual_identity_is_replaceable_boundary() -> None:
    assert ManualIdentity(" user ").user_id() == "user"
    assert SELECTED_SESSION_KEY == "selected-session-id"
    assert DRAFT_STATE_KEY == "session-draft"


def test_session_label_uses_jst_time_and_legacy_title_fallback() -> None:
    from datetime import UTC, datetime

    from cas_hosting_adapter.models import Session

    session = Session(
        id="session",
        user_id="user",
        workspace_id="workspace",
        updated_at=datetime(2026, 8, 9, 1, 2, 3, tzinfo=UTC),
    )
    assert session_label(session) == "2026-08-09 10:02:03 JST · Untitled session"


def test_order_sessions_returns_newest_updated_session_first() -> None:
    from datetime import UTC, datetime, timedelta

    from cas_hosting_adapter.models import Session

    now = datetime(2026, 8, 9, 1, 2, 3, tzinfo=UTC)
    older = Session(
        id="older",
        user_id="user",
        workspace_id="workspace-older",
        updated_at=now - timedelta(minutes=1),
    )
    newer = Session(
        id="newer",
        user_id="user",
        workspace_id="workspace-newer",
        updated_at=now,
    )

    assert [session.id for session in order_sessions([older, newer])] == ["newer", "older"]


def test_streamlit_auto_refresh_stops_while_a_question_is_pending() -> None:
    assert auto_refresh_allowed(InteractionState()) is True
    assert auto_refresh_allowed(InteractionState(pending_questions=(object(),))) is False


def test_view_model_creates_session_and_starts_run() -> None:
    view = ChatViewModel(
        ChatService(ControlClient(InMemoryChatStore(), InMemoryExecutionBackend()), "user")
    )
    session = view.create_session()
    run = view.start(session, "hello", "key")
    assert run.execution is not None
    assert view.session(session.id).active_run_id == run.id
    assert [event.id for event in view.events(run.id)] == [f"user:{run.id}"]


def test_view_model_reconciles_active_run_without_provider_dependency() -> None:
    store = InMemoryChatStore()
    backend = InMemoryExecutionBackend()
    view = ChatViewModel(ChatService(ControlClient(store, backend), "user"))
    session = view.create_session()
    run = view.start(session, "hello", "key")
    assert run.execution is not None
    backend.set_state(run.execution, ExecutionState.FAILED)
    reconciled = view.reconcile(run.id)
    assert (reconciled.state, reconciled.error_code) == (
        RunState.FAILED,
        "cloud_run_execution_failed",
    )


def test_view_model_reconcile_preserves_active_run_after_temporary_error() -> None:
    class TemporaryBackend(InMemoryExecutionBackend):
        def get(self, reference):
            raise ExecutionTemporaryError("temporary")

    store = InMemoryChatStore()
    view = ChatViewModel(ChatService(ControlClient(store, TemporaryBackend()), "user"))
    session = view.create_session()
    run = view.start(session, "hello", "key")
    assert view.reconcile(run.id).state is RunState.PENDING
    assert view.session(session.id).active_run_id == run.id


def test_history_does_not_render_streamed_answer_again_as_final_result() -> None:
    store = InMemoryChatStore()
    view = ChatViewModel(ChatService(ControlClient(store, InMemoryExecutionBackend()), "user"))
    session = view.create_session()
    run = view.start(session, "summarize this", "key")
    answer = "GitHub プロフィール要約: 74th (Atsushi Morimoto)"
    store.append_event(
        ChatEvent(
            id="agent-answer",
            run_id=run.id,
            sequence=0,
            type="agent",
            payload={"content": answer},
        )
    )
    store.append_event(
        ChatEvent(
            id="final-answer",
            run_id=run.id,
            sequence=0,
            type="final",
            payload={"output": answer},
        )
    )

    assert [(event.type, event.payload) for event in view.history(session.id)] == [
        ("user", {"content": "summarize this"}),
        ("final", {"output": answer}),
    ]


def test_history_reclassifies_legacy_tool_result_stored_as_user_event() -> None:
    from uuid import uuid4

    run_id = uuid4()
    legacy = ChatEvent(
        id="sdk:legacy-tool-result",
        run_id=run_id,
        sequence=0,
        type="user",
        payload={
            "content": [
                {
                    "tool_use_id": "tool-1",
                    "content": [{"type": "text", "text": "result"}],
                    "is_error": None,
                }
            ]
        },
    )

    converted = normalize_events([legacy])

    assert [(event.type, event.payload) for event in converted] == [
        (
            "tool_completed",
            {
                "tool_id": "tool-1",
                "content": [{"type": "text", "text": "result"}],
                "is_error": False,
            },
        )
    ]


def test_release_config_builds_the_google_cloud_settings(monkeypatch, tmp_path) -> None:
    release = tmp_path / "release.yaml"
    release.write_text(
        "project_id: project\nregion: us-central1\nfirestore_location: us-central1\n"
        "firestore_database: claude-agent-chat\nbucket_name: bucket\nimage: image\njob_name: job\n"
    )
    def fake_client(_path) -> ControlClient:
        return ControlClient(InMemoryChatStore(), InMemoryExecutionBackend())

    monkeypatch.setattr(
        "example.streamlit_frontend.app.create_control_client_from_release_config", fake_client
    )
    view = create_view_from_release_config(release, ManualIdentity("user"))
    assert view.sessions().sessions == []
