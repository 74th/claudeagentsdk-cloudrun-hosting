from datetime import timedelta
from uuid import uuid4

import pytest

from cas_hosting_adapter.errors import WorkspaceCorruptedError
from cas_hosting_adapter.protocols import InMemorySnapshotStore
from cas_hosting_adapter.workspace_store import (
    RunLockStore,
    StoragePaths,
    archive_snapshot,
    prepare_workspace,
    request_directories,
    restore_verified_snapshot,
    save_immutable_snapshot,
    snapshot_manifest,
)


def test_storage_paths_are_hashed_and_run_scoped() -> None:
    paths = StoragePaths.for_session(
        user_id="alice@example.com",
        session_id="../session",
        schema_version="1",
        sdk_version="0.2.128",
    )
    run_id = uuid4()
    assert "alice" not in paths.prefix and ".." not in paths.prefix
    assert str(run_id) in paths.snapshot_path(run_id)
    assert paths.input_path(run_id) != paths.output_path(run_id)


def test_snapshot_round_trip_and_integrity_check(tmp_path) -> None:
    store = InMemorySnapshotStore()
    run_id = uuid4()
    with request_directories() as source:
        (source.workspace / "file.txt").write_text("workspace")
        (source.claude_session / "transcript.jsonl").write_text("transcript")
        archive = tmp_path / "snapshot.tar.gz"
        uncompressed, _ = archive_snapshot(source, archive, max_bytes=1024)
        manifest = snapshot_manifest(
            run_id=run_id, sdk_version="0.2.128", archive_path=archive,
            uncompressed_bytes=uncompressed,
        )
        reference = save_immutable_snapshot(
            store, object_path="snapshot.tar.gz", manifest=manifest, archive_path=archive
        )
    with request_directories() as destination:
        restore_verified_snapshot(store, reference, destination, max_bytes=1024)
        assert (destination.workspace / "file.txt").read_text() == "workspace"
        assert (destination.claude_session / "transcript.jsonl").read_text() == "transcript"


def test_corrupted_snapshot_is_rejected() -> None:
    store = InMemorySnapshotStore()
    generation = store.upload("snapshot.tar.gz", b"not a snapshot", if_generation_match=0)
    from cas_hosting_adapter.models import SnapshotReference

    reference = SnapshotReference(
        object_path="snapshot.tar.gz", generation=generation, sha256="0" * 64
    )
    with request_directories() as destination, pytest.raises(WorkspaceCorruptedError):
        restore_verified_snapshot(store, reference, destination, max_bytes=1024)


def test_initializer_runs_only_without_committed_snapshot() -> None:
    calls: list[str] = []
    with request_directories() as directories:
        prepare_workspace(
            directories,
            snapshot_store=InMemorySnapshotStore(),
            committed_snapshot=None,
            initializer=lambda workspace: calls.append(workspace.name),
            max_bytes=1024,
        )
    assert calls == ["workspace"]


def test_active_run_lock_rejects_conflict_and_requires_its_generation() -> None:
    locks = RunLockStore(InMemorySnapshotStore())
    lock, generation = locks.acquire("locks/session", uuid4(), pending_ttl=timedelta(minutes=1))
    with pytest.raises(FileExistsError):
        locks.acquire("locks/session", uuid4(), pending_ttl=timedelta(minutes=1))
    bound, bound_generation = locks.bind_operation(
        "locks/session",
        generation,
        lock,
        operation_name="operations/1",
        running_ttl=timedelta(minutes=30),
    )
    assert bound.operation_name == "operations/1"
    with pytest.raises(FileNotFoundError):
        locks.release("locks/session", generation)
    with pytest.raises(FileNotFoundError):
        locks.bind_operation(
            "locks/session",
            generation,
            lock,
            operation_name="operations/2",
            running_ttl=timedelta(minutes=30),
        )
    locks.release("locks/session", bound_generation)
