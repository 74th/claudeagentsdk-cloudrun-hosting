"""Destructive-but-cleaned Google Cloud checks, enabled only by explicit opt-in."""
from __future__ import annotations

import os
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest

from cas_hosting_adapter.cloud_run_backend import CloudRunJobsBackend
from cas_hosting_adapter.firestore_chat_store import FirestoreChatStore
from cas_hosting_adapter.firestore_codec import user_key
from cas_hosting_adapter.google_adapters import GCSWorkspaceStore
from cas_hosting_adapter.models import ChatEvent, Run, RunState
from cas_hosting_adapter.workspace_store import (
    create_workspace_snapshot,
    request_directories,
    restore_workspace_snapshot,
)


def _required_live_settings() -> tuple[str, str, str, str]:
    if os.environ.get("CAS_HOSTING_LIVE_GCP") != "1":
        pytest.skip("set CAS_HOSTING_LIVE_GCP=1 to run Google Cloud integration checks")
    names = ("PROJECT", "REGION", "JOB", "BUCKET")
    values = tuple(os.environ.get(f"CAS_HOSTING_LIVE_GCP_{name}", "") for name in names)
    if not all(values):
        pytest.skip("set CAS_HOSTING_LIVE_GCP_PROJECT/REGION/JOB/BUCKET")
    return values[0], values[1], values[2], values[3]


@pytest.mark.live_gcp
def test_google_cloud_ports_support_chat_snapshot_job_cancel_and_deduplication() -> None:
    project, region, job_name, bucket_name = _required_live_settings()
    from google.cloud import firestore, storage
    from google.cloud.run_v2 import ExecutionsClient, JobsClient

    firestore_client = firestore.Client(project=project, database="(default)")
    chat_store = FirestoreChatStore(firestore_client)
    workspace_store = GCSWorkspaceStore(storage.Client(project=project).bucket(bucket_name))
    backend = CloudRunJobsBackend(
        JobsClient(), ExecutionsClient(), project=project, region=region, job_name=job_name
    )
    user_id = f"live-test-{uuid4()}"
    session = chat_store.create_session(user_id)
    run = Run(
        user_id=user_id,
        session_id=session.id,
        workspace_id=session.workspace_id,
        idempotency_key=str(uuid4()),
    )
    event = ChatEvent(id=f"user:{run.id}", run_id=run.id, sequence=0, type="user")
    snapshot = None
    unsubscribe = None
    try:
        assert chat_store.reserve_run(run, event).id == run.id
        assert chat_store.list_sessions(user_id, cursor=None, limit=10).sessions[0].id == session.id

        delivered = Event()
        unsubscribe = chat_store.subscribe(run.id, None, lambda _event: delivered.set())
        appended = chat_store.append_event(
            ChatEvent(id=f"agent:{run.id}", run_id=run.id, sequence=0, type="agent")
        )
        assert appended.sequence == 1
        assert delivered.wait(timeout=30), "Firestore listener did not receive the appended event"

        with request_directories() as source:
            (source.workspace / "resume.txt").write_text("live resume", encoding="utf-8")
            snapshot, manifest = create_workspace_snapshot(
                workspace_store,
                object_key=f"cas/live-tests/{run.id}/snapshot.tar.gz",
                source=source,
                run_id=run.id,
                sdk_version="live-test",
                max_bytes=1024 * 1024,
            )
        with request_directories() as destination:
            restore_workspace_snapshot(
                workspace_store,
                snapshot,
                manifest,
                destination,
                max_bytes=1024 * 1024,
                expected_schema_version="1",
                expected_sdk_version="live-test",
            )
            restored = (destination.workspace / "resume.txt").read_text(encoding="utf-8")
            assert restored == "live resume"

        execution = backend.start(run.id)
        assert backend.dispatch_once(run.id, existing=execution) == execution
        assert chat_store.request_cancel(run.id).state is RunState.CANCEL_REQUESTED
        backend.cancel(execution)
        # A cold Cloud Run Job can remain pending for more than one minute before
        # a cancellation request is observed or the execution reaches a terminal state.
        deadline = monotonic() + 180
        while monotonic() < deadline:
            if backend.get(execution).value in {"cancelled", "failed", "succeeded"}:
                break
            sleep(2)
        else:
            pytest.fail("Cloud Run Execution did not reach a terminal state after cancel")
    finally:
        if unsubscribe is not None:
            unsubscribe()
        if snapshot is not None:
            workspace_store.delete(snapshot)
        firestore_client.recursive_delete(
            firestore_client.collection("users").document(user_key(user_id)).collection("sessions").document(
                session.id
            )
        )
