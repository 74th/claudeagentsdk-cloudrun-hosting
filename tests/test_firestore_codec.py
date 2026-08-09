from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from cas_hosting_adapter.firestore_codec import (
    encode_event,
    encode_run,
    encode_session,
    is_expired,
    user_key,
)
from cas_hosting_adapter.models import ChatEvent, Run, Session


def test_user_key_is_normalized_and_never_a_document_path() -> None:
    assert user_key(" alice ") == user_key("alice")
    assert len(user_key("a/b")) == 64
    with pytest.raises(ValueError):
        user_key("  ")


def test_event_codec_is_versioned() -> None:
    encoded = encode_event(ChatEvent(id="event", run_id=uuid4(), sequence=0, type="user"))
    assert encoded["schema_version"] == "1"


def test_session_run_and_event_use_default_and_configured_retention() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    session = Session(
        id="session",
        user_id="user",
        workspace_id="workspace",
        created_at=base,
        updated_at=base,
    )
    run = Run(
        id=uuid4(),
        user_id="user",
        session_id="session",
        workspace_id="workspace",
        idempotency_key="key",
        created_at=base,
    )
    event = ChatEvent(id="event", run_id=run.id, sequence=0, type="user", occurred_at=base)

    assert encode_session(session)["expires_at"] == base + timedelta(days=30)
    assert encode_run(run, retention_days=45)["expires_at"] == base + timedelta(days=45)
    assert encode_event(event, retention_days=45)["expires_at"] == base + timedelta(days=45)


def test_expired_firestore_payload_is_hidden_until_ttl_deletes_it() -> None:
    now = datetime(2026, 1, 31, tzinfo=UTC)
    payload = {"expires_at": now}
    assert is_expired(payload, now)
    assert is_expired(payload, now + timedelta(minutes=1))
    assert not is_expired(payload, now - timedelta(seconds=1))
