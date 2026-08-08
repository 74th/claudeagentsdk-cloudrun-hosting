from cas_hosting_adapter.control_client import ControlClient
from cas_hosting_adapter.factory import GoogleCloudSettings
from cas_hosting_adapter.in_memory_chat_store import InMemoryChatStore
from cas_hosting_adapter.protocols import InMemoryExecutionBackend
from sample_frontend.app import (
    SELECTED_SESSION_KEY,
    ChatViewModel,
    ManualIdentity,
    create_view_from_release_config,
)


def test_manual_identity_is_replaceable_boundary() -> None:
    assert ManualIdentity(" user ").user_id() == "user"
    assert SELECTED_SESSION_KEY == "selected-session-id"


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
        "bucket_name: bucket\nimage: image\njob_name: job\n"
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
            firestore_database="(default)",
            bucket_name="bucket",
            job_name="job",
        )
    ]
