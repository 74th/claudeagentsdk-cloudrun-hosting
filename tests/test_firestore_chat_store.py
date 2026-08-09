from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

from cas_hosting_adapter.firestore_chat_store import FirestoreChatStore
from cas_hosting_adapter.models import ReconciliationLease, Run, RunState


class FakeDocument:
    def __init__(self, data=None) -> None:
        self.data = data
        self.exists = data is not None
        self.collections = {}
    def collection(self, key): return self.collections.setdefault(key, FakeCollection())
    def create(self, data) -> None:
        self.data, self.exists = data, True
    def get(self): return self
    def to_dict(self): return self.data
    def update(self, values) -> None: self.data.update(values)


class FakeCollection:
    def __init__(self) -> None: self.docs = {}
    def document(self, key): return self.docs.setdefault(key, FakeDocument())


class FakeClient:
    def __init__(self) -> None: self.collections = {}
    def collection(self, key): return self.collections.setdefault(key, FakeCollection())


def test_create_get_and_update_session() -> None:
    store = FirestoreChatStore(FakeClient())
    session = store.create_session("user", title="first")
    assert store.get_session("user", session.id).title == "first"
    store.update_session_summary("user", session.id, latest_run_state="requested")
    assert store.get_session("user", session.id).latest_run_state == "requested"


def test_run_codec_excludes_firestore_transaction_metadata() -> None:
    run = Run(user_id="user", session_id="session", workspace_id="workspace", idempotency_key="key")
    payload = run.model_dump(mode="json") | {
        "schema_version": "1",
        "next_sequence": 2,
        "execution_owner": "execution",
        "heartbeat_at": "2026-01-01T00:00:00Z",
        "cancel_requested_at": "2026-01-01T00:00:00Z",
    }
    assert FirestoreChatStore._decode_run(payload) == run


def test_reconcile_terminal_updates_run_and_session_in_one_fake_transaction(monkeypatch) -> None:
    import google.cloud.firestore

    run = Run(
        id=uuid4(),
        user_id="user",
        session_id="session",
        workspace_id="workspace",
        idempotency_key="key",
    )
    run_ref = MagicMock()
    run_ref.get.return_value.to_dict.return_value = run.model_dump(mode="json")
    session_ref = MagicMock()
    run_ref.parent.parent = session_ref
    query = MagicMock()
    query.where.return_value.limit.return_value.stream.return_value = [
        MagicMock(reference=run_ref)
    ]
    client = MagicMock()
    client.collection_group.return_value = query
    transaction = MagicMock()
    client.transaction.return_value = transaction
    store = FirestoreChatStore(client)
    now = datetime.now(UTC)
    monkeypatch.setattr(
        store,
        "acquire_reconciliation_lease",
        lambda run_id, holder: ReconciliationLease(run_id=run_id, holder=holder, expires_at=now),
    )
    monkeypatch.setattr(google.cloud.firestore, "transactional", lambda function: function)

    reconciled = store.reconcile_terminal(
        run.id, "holder", RunState.FAILED, error_code="cloud_run_execution_failed"
    )

    assert reconciled.state is RunState.FAILED
    assert reconciled.error_code == "cloud_run_execution_failed"
    assert transaction.update.call_count == 2
