import inspect
from uuid import uuid4

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
    DynamicRenderState,
    ManualIdentity,
    RefreshScope,
    auto_refresh_allowed,
    create_view_from_release_config,
    decide_refresh_scope,
    dynamic_state_cache_hit,
    make_dynamic_revision,
    monitored_run_id_for_fragment,
    order_sessions,
    session_label,
    should_open_initial_draft,
    split_history,
)


def _session_and_run(state: RunState = RunState.RUNNING):
    from cas_hosting_adapter.models import Run, Session

    run = Run(
        id=uuid4(),
        user_id="user",
        session_id="session",
        workspace_id="workspace",
        idempotency_key="key",
        state=state,
    )
    session = Session(
        id="session",
        user_id="user",
        workspace_id="workspace",
        active_run_id=run.id,
    )
    return session, run


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


def test_first_page_opens_the_same_draft_as_new_session() -> None:
    assert should_open_initial_draft(None, draft_initialized=False) is True
    assert should_open_initial_draft("session", draft_initialized=False) is False
    assert should_open_initial_draft(None, draft_initialized=True) is False


def test_refresh_scope_polls_active_run_inside_the_fragment() -> None:
    session, run = _session_and_run()

    decision = decide_refresh_scope(run.id, session, run, InteractionState())

    assert decision.scope is RefreshScope.FRAGMENT


def test_refresh_scope_stops_polling_while_question_is_pending() -> None:
    session, run = _session_and_run()

    decision = decide_refresh_scope(
        run.id,
        session,
        run,
        InteractionState(pending_questions=(object(),)),
    )

    assert decision.scope is RefreshScope.NONE


def test_refresh_scope_promotes_finish_and_terminal_transitions_to_app() -> None:
    session, run = _session_and_run(RunState.COMPLETED)

    terminal = decide_refresh_scope(run.id, session, run, InteractionState())
    finish = decide_refresh_scope(
        run.id,
        session,
        run.model_copy(update={"state": RunState.RUNNING}),
        InteractionState(),
        finish_event_seen=True,
    )

    assert terminal.scope is RefreshScope.APP
    assert finish.scope is RefreshScope.APP


def test_refresh_scope_synchronizes_a_cleared_run_after_an_earlier_app_sync() -> None:
    session, run = _session_and_run()
    cleared = session.model_copy(update={"active_run_id": None})

    decision = decide_refresh_scope(
        run.id,
        cleared,
        run,
        InteractionState(),
        app_sync_already_requested=True,
    )

    assert decision.scope is RefreshScope.APP


def test_fragment_keeps_monitoring_a_run_after_session_clears_it() -> None:
    _session, run = _session_and_run()

    monitored = monitored_run_id_for_fragment(
        "session",
        None,
        ("session", run.id),
    )

    assert monitored == run.id


def test_fragment_does_not_reuse_a_run_from_another_session() -> None:
    _session, run = _session_and_run()
    next_run_id = uuid4()

    monitored = monitored_run_id_for_fragment(
        "next-session",
        next_run_id,
        ("session", run.id),
    )

    assert monitored == next_run_id


def test_refresh_scope_keeps_fragment_polling_after_temporary_status_error() -> None:
    session, run = _session_and_run()

    decision = decide_refresh_scope(
        run.id,
        session,
        run,
        InteractionState(),
        temporary_error=True,
    )

    assert decision.scope is RefreshScope.FRAGMENT


def test_dynamic_revision_cache_invalidates_only_when_durable_state_changes() -> None:
    session, run = _session_and_run()
    first_event = ChatEvent(id="event-1", run_id=run.id, sequence=1, type="progress")
    first_revision = make_dynamic_revision(session, run, first_event)
    cached = DynamicRenderState(
        session_id=session.id,
        revision=first_revision,
        selected=session,
        current_run=run,
        history=[],
        interaction=None,
        finish_event_seen=False,
    )

    assert dynamic_state_cache_hit(cached, session.id, first_revision) is True
    assert dynamic_state_cache_hit(
        cached,
        session.id,
        make_dynamic_revision(
            session,
            run,
            first_event.model_copy(update={"id": "event-2", "sequence": 2}),
        ),
    ) is False


def test_view_model_reads_only_the_latest_event_marker() -> None:
    store = InMemoryChatStore()
    view = ChatViewModel(ChatService(ControlClient(store, InMemoryExecutionBackend()), "user"))
    session = view.create_session()
    run = view.start(session, "hello", "key")
    store.append_event(
        ChatEvent(id="progress", run_id=run.id, sequence=0, type="progress")
    )

    assert view.latest_event(run.id).id == "progress"


def test_user_history_is_separated_from_fragment_owned_events() -> None:
    run_id = uuid4()
    user, progress = normalize_events(
        [
            ChatEvent(
                id="user",
                run_id=run_id,
                sequence=0,
                type="user",
                payload={"content": "hello"},
            ),
            ChatEvent(
                id="progress",
                run_id=run_id,
                sequence=1,
                type="progress",
                payload={"description": "working"},
            ),
        ]
    )

    user_events, dynamic_events = split_history([user, progress])

    assert [event.type for event in user_events] == ["user"]
    assert [event.type for event in dynamic_events] == ["progress"]


def test_fragment_uses_fragment_scope_and_terminal_sync_uses_app_scope() -> None:
    from example.streamlit_frontend.app import (
        render,
        render_dynamic_area,
        request_fragment_rerun,
    )

    fragment_source = inspect.getsource(request_fragment_rerun)
    render_source = inspect.getsource(render)
    dynamic_source = inspect.getsource(render_dynamic_area)

    assert 'st.rerun(scope="fragment")' in fragment_source
    assert 'st.rerun(scope="app")' in dynamic_source
    assert "view.reconcile" not in dynamic_source
    assert "view.latest_event" in dynamic_source
    assert "run_every=2 if auto_refresh_scheduled else None" in render_source


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
