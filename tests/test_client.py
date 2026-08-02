import json

import pytest

from cas_hosting_adapter.client import HostingClient
from cas_hosting_adapter.models import HostingSettings
from cas_hosting_adapter.protocols import InMemoryOperations, InMemorySnapshotStore
from cas_hosting_adapter.session_store import GoogleSessionStore


class FakeSessions:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, str]] = {}

    def create(self, *, name: str, user_id: str, config: object) -> dict[str, object]:
        del config
        session_name = f"{name}/sessions/{len(self.items) + 1}"
        item = {"name": session_name, "user_id": user_id}
        self.items[session_name] = item
        return {"response": item}

    def get(self, *, name: str) -> dict[str, str]:
        return self.items[name]

    def list(self, *, name: str, config: object = None) -> list[dict[str, str]]:
        del name, config
        return list(self.items.values())


class FakeEvents:
    def __init__(self) -> None:
        self.items: dict[str, list[dict[str, object]]] = {}

    def append(self, **kwargs: object) -> None:
        self.items.setdefault(str(kwargs["name"]), []).append(kwargs)

    def list(self, *, name: str, config: object = None) -> list[dict[str, object]]:
        del config
        return self.items.get(name, [])


def make_client() -> tuple[HostingClient, InMemorySnapshotStore, FakeEvents, InMemoryOperations]:
    events = FakeEvents()
    sessions = GoogleSessionStore(
        project="project", location="us-central1",
        agent_engine="projects/project/locations/us-central1/reasoningEngines/engine",
        sessions=FakeSessions(), events=events,
    )
    storage = InMemorySnapshotStore()
    operations = InMemoryOperations()
    client = HostingClient(
        session_store=sessions,
        snapshot_store=storage,
        operations=operations,
        settings=HostingSettings(
            project="project", location="us-central1", agent_engine="engine", bucket_name="bucket"
        ),
        gcs_uri=lambda path: f"gs://bucket/{path}",
    )
    return client, storage, events, operations


def test_start_run_creates_session_input_lock_and_operation_event() -> None:
    client, storage, events, operations = make_client()
    started = client.start_run(user_id="alice", message="hello")

    event_types = [json.loads(item["config"]["content"]["parts"][0]["text"])["event_type"]
                   for item in events.items[started.session_id]]
    assert event_types == ["run_requested", "operation_bound"]
    first_event = events.items[started.session_id][0]
    input_event = json.loads(first_event["config"]["content"]["parts"][0]["text"])
    payload = json.loads(storage.objects[input_event["payload"]["input_path"]][1])
    assert payload["run_id"] == str(started.run_id)
    assert payload["workspace_id"] == started.workspace_id
    assert started.operation_name == "operations/1"
    request = json.loads(operations.requests[started.operation_name]["query"])
    assert request == {"input": payload}


def test_start_run_releases_pending_lock_when_start_fails() -> None:
    class FailingOperations(InMemoryOperations):
        def start(self, *, input_payload):
            del input_payload
            raise RuntimeError("unavailable")

    client, storage, _, _ = make_client()
    client.operations = FailingOperations()
    with pytest.raises(RuntimeError):
        client.start_run(user_id="alice", message="hello")
    assert not any(path.startswith("locks/") for path in storage.objects)


def test_status_reconciles_operation_and_cancel_is_recorded() -> None:
    client, storage, events, _ = make_client()
    started = client.start_run(user_id="alice", message="hello")

    status = client.get_run_status(
        user_id="alice", session_id=started.session_id, run_id=started.run_id
    )
    assert status.state.value == "running"
    client.cancel_run(user_id="alice", session_id=started.session_id, run_id=started.run_id)
    assert client.operations.get(started.operation_name) == "CANCELLED"
    stored_last_event = events.items[started.session_id][-1]
    requested_event = json.loads(stored_last_event["config"]["content"]["parts"][0]["text"])
    assert requested_event["event_type"] == "cancel_requested"
    reconciled = client.get_run_status(
        user_id="alice", session_id=started.session_id, run_id=started.run_id
    )
    assert reconciled.state.value == "cancelled"
    stored_terminal_event = events.items[started.session_id][-1]
    terminal_event = json.loads(stored_terminal_event["config"]["content"]["parts"][0]["text"])
    assert terminal_event["event_type"] == "cancelled"
    assert not any(path.startswith("locks/") for path in storage.objects)
