from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from cas_hosting_adapter.errors import (
    ConfigurationError,
    SessionExpiredError,
    SessionNotFoundError,
    SessionOwnershipError,
)
from cas_hosting_adapter.models import SessionEvent
from cas_hosting_adapter.session_store import GoogleSessionStore, compare_mirror_to_transcript


class FakeSessions:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, str]] = {}

    def create(self, *, name: str, user_id: str, config: object) -> dict[str, object]:
        session_name = f"{name}/sessions/{len(self.items) + 1}"
        item = {"name": session_name, "user_id": user_id}
        self.items[session_name] = item
        return {"response": item}

    def get(self, *, name: str) -> dict[str, str]:
        if name not in self.items:
            raise RuntimeError("404 not found")
        return self.items[name]

    def list(self, *, name: str, config: object = None) -> list[dict[str, str]]:
        return list(self.items.values())


class FakeEvents:
    def __init__(self) -> None:
        self.items: dict[str, list[dict[str, object]]] = {}

    def append(self, **kwargs: object) -> None:
        self.items.setdefault(str(kwargs["name"]), []).append(kwargs)

    def list(self, *, name: str, config: object = None) -> list[dict[str, object]]:
        return self.items.get(name, [])


@pytest.fixture
def store() -> GoogleSessionStore:
    return GoogleSessionStore(
        project="project", location="us-central1",
        agent_engine="projects/project/locations/us-central1/reasoningEngines/engine",
        sessions=FakeSessions(), events=FakeEvents(),
    )


def test_create_get_and_owner_validation(store: GoogleSessionStore) -> None:
    session_id = store.create("alice")
    assert store.get(session_id, "alice")["name"] == session_id
    with pytest.raises(SessionOwnershipError):
        store.get(session_id, "bob")
    with pytest.raises(SessionNotFoundError):
        store.get("projects/other/locations/us-central1/reasoningEngines/x/sessions/1", "alice")


def test_session_store_rejects_engine_from_another_project_or_location() -> None:
    with pytest.raises(ConfigurationError):
        GoogleSessionStore(
            project="project", location="us-central1",
            agent_engine="projects/other/locations/us-central1/reasoningEngines/engine",
            sessions=FakeSessions(), events=FakeEvents(),
        )


def test_events_are_deduplicated_and_state_is_derived(store: GoogleSessionStore) -> None:
    session_id = store.create("alice")
    run_id = uuid4()
    started = SessionEvent(run_id=run_id, sequence=0, event_type="run_started")
    store.append(session_id, started)
    store.append(session_id, started)
    store.append(session_id, SessionEvent(run_id=run_id, sequence=1, event_type="completed"))
    events = store.events_for_run(session_id, run_id)
    assert [event.sequence for event in events] == [0, 1]
    assert store.state_for_run(session_id, run_id).value == "completed"


def test_expired_session_is_rejected(store: GoogleSessionStore) -> None:
    with pytest.raises(SessionExpiredError):
        store.ensure_fresh(datetime.now(UTC) - timedelta(days=2))


def test_operation_reconciliation_requires_snapshot_for_success(store: GoogleSessionStore) -> None:
    session_id = store.create("alice")
    run_id = uuid4()
    store.append(session_id, SessionEvent(run_id=run_id, sequence=0, event_type="run_started"))

    assert store.reconcile_run(session_id, run_id, operation_status="SUCCEEDED").value == (
        "persistence_failed"
    )
    store.append(
        session_id,
        SessionEvent(
            run_id=run_id,
            sequence=1,
            event_type="snapshot_committed",
            payload={"object_path": "snapshots/a", "generation": 1, "sha256": "a" * 64},
        ),
    )
    assert store.reconcile_run(session_id, run_id, operation_status="SUCCEEDED").value == (
        "completed"
    )
    assert store.reconcile_run(session_id, run_id, operation_status="CANCELLED").value == (
        "cancelled"
    )


def test_compare_mirror_to_transcript_reports_content_and_missing_records() -> None:
    run_id = uuid4()
    events = [
        SessionEvent(run_id=run_id, sequence=0, event_type="user_message", payload={"text": "Hi"}),
        SessionEvent(
            run_id=run_id, sequence=1, event_type="agent_message", payload={"text": "Hello"}
        ),
        SessionEvent(
            run_id=run_id, sequence=2, event_type="tool_started", payload={"tool": "search"}
        ),
    ]
    transcript = '\n'.join([
        '{"role":"user","message":{"content":"Hi"}}',
        '{"role":"assistant","message":{"content":"Different"}}',
    ])

    result = compare_mirror_to_transcript(events, transcript)

    assert result.matched_count == 1
    assert result.missing_in_transcript == [{"kind": "tool_started", "text": "search"}]
    assert result.content_differences == [{
        "mirror": "{'kind': 'assistant', 'text': 'Hello'}",
        "transcript": "{'kind': 'assistant', 'text': 'Different'}",
    }]


def test_compare_mirror_to_transcript_reports_order_only_difference() -> None:
    run_id = uuid4()
    events = [
        SessionEvent(run_id=run_id, sequence=0, event_type="user_message", payload={"text": "Hi"}),
        SessionEvent(
            run_id=run_id, sequence=1, event_type="agent_message", payload={"text": "Hello"}
        ),
    ]
    transcript = '\n'.join([
        '{"role":"assistant","message":{"content":"Hello"}}',
        '{"role":"user","message":{"content":"Hi"}}',
    ])

    result = compare_mirror_to_transcript(events, transcript)

    assert result.ordering_difference
    assert result.content_differences == []
