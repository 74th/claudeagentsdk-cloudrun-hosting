"""Opt-in integration checks for the Firestore ChatStore contract."""
from __future__ import annotations

import os
from uuid import uuid4

import pytest

from cas_hosting_adapter.firestore_chat_store import FirestoreChatStore
from cas_hosting_adapter.models import ChatEvent, Run


@pytest.mark.live_gcp
def test_firestore_session_run_claim_and_event_contract() -> None:
    project = os.environ.get("CAS_HOSTING_FIRESTORE_TEST_PROJECT")
    if not project:
        pytest.skip("set CAS_HOSTING_FIRESTORE_TEST_PROJECT to run live Firestore checks")
    from google.cloud import firestore

    store = FirestoreChatStore(firestore.Client(project=project, database="(default)"))
    user_id = f"openspec-{uuid4()}"
    session = store.create_session(user_id)
    run = Run(
        user_id=user_id,
        session_id=session.id,
        workspace_id=session.workspace_id,
        idempotency_key=str(uuid4()),
    )
    event = ChatEvent(id=str(uuid4()), run_id=run.id, sequence=0, type="user")
    assert store.reserve_run(run, event).id == run.id
    assert store.claim_run(run.id, "execution-a")
    assert not store.claim_run(run.id, "execution-b")
    appended = store.append_event(
        ChatEvent(id=str(uuid4()), run_id=run.id, sequence=0, type="agent")
    )
    assert appended.sequence == 1
