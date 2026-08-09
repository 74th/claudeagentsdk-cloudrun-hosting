from cas_hosting_adapter.control_client import ControlClient
from cas_hosting_adapter.factory import GoogleCloudSettings
from cas_hosting_adapter.in_memory_chat_store import InMemoryChatStore
from cas_hosting_adapter.protocols import InMemoryExecutionBackend
from sample_frontend.app import (
    DRAFT_STATE_KEY,
    SELECTED_SESSION_KEY,
    ChatViewModel,
    ManualIdentity,
    create_view_from_release_config,
    session_label,
)


def test_manual_identity_is_replaceable_boundary() -> None:
    assert ManualIdentity(" user ").user_id() == "user"
    assert SELECTED_SESSION_KEY == "selected-session-id"
    assert DRAFT_STATE_KEY == "session-draft"


def test_session_label_uses_utc_time_and_legacy_title_fallback() -> None:
    from datetime import UTC, datetime

    from cas_hosting_adapter.models import Session

    session = Session(
        id="session",
        user_id="user",
        workspace_id="workspace",
        updated_at=datetime(2026, 8, 9, 1, 2, 3, tzinfo=UTC),
    )
    assert session_label(session) == "2026-08-09 01:02:03 UTC · Untitled session"


def test_view_model_creates_session_and_starts_run() -> None:
    view = ChatViewModel(
        ControlClient(InMemoryChatStore(), InMemoryExecutionBackend()), ManualIdentity("user")
    )
    session = view.create_session()
    run = view.start(session, "hello", "key")
    assert run.execution is not None
    assert view.session(session.id).active_run_id == run.id
    received: list[str] = []
    unsubscribe = view.subscribe(run.id, lambda event: received.append(event.id))
    unsubscribe()
    assert received == [f"user:{run.id}"]
    assert [event.id for event in view.events(run.id)] == [f"user:{run.id}"]


def test_release_config_builds_the_google_cloud_settings(monkeypatch, tmp_path) -> None:
    release = tmp_path / "release.yaml"
    release.write_text(
        "project_id: project\nregion: us-central1\nfirestore_location: us-central1\n"
        "firestore_database: claude-agent-chat\nbucket_name: bucket\nimage: image\njob_name: job\n"
    )
    captured: list[GoogleCloudSettings] = []

    def fake_client(settings: GoogleCloudSettings) -> ControlClient:
        captured.append(settings)
        return ControlClient(InMemoryChatStore(), InMemoryExecutionBackend())

    monkeypatch.setattr("sample_frontend.app.create_google_cloud_control_client", fake_client)
    view = create_view_from_release_config(release, ManualIdentity("user"))
    assert view.sessions().sessions == []
    assert captured == [
        GoogleCloudSettings(
            project="project",
            region="us-central1",
            firestore_database="claude-agent-chat",
            bucket_name="bucket",
            job_name="job",
        )
    ]
