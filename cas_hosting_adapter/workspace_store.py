"""GCS workspace, transcript snapshot, and active-run lock boundary."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tarfile
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from uuid import UUID

from .errors import WorkspaceCorruptedError, WorkspaceTooLargeError
from .models import SnapshotManifest, SnapshotReference
from .protocols import SnapshotStore


def opaque_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StoragePaths:
    user_hash: str
    session_hash: str
    schema_version: str
    sdk_version: str

    @classmethod
    def for_session(cls, *, user_id: str, session_id: str, schema_version: str,
                    sdk_version: str) -> StoragePaths:
        return cls(opaque_id(user_id), opaque_id(session_id), schema_version, sdk_version)

    @property
    def prefix(self) -> str:
        return (
            f"cas/v{self.schema_version}/sdk-{self.sdk_version}/users/{self.user_hash}/"
            f"sessions/{self.session_hash}"
        )

    @property
    def lock_path(self) -> str:
        return f"locks/v{self.schema_version}/{self.user_hash}/{self.session_hash}.json"

    def input_path(self, run_id: UUID) -> str:
        return f"{self.prefix}/runs/{run_id}/input.json"

    def output_path(self, run_id: UUID) -> str:
        return f"{self.prefix}/runs/{run_id}/output.json"

    def snapshot_path(self, run_id: UUID) -> str:
        return f"{self.prefix}/snapshots/{run_id}/snapshot.tar.gz"


@dataclass(frozen=True)
class RequestDirectories:
    root: Path
    workspace: Path
    claude_session: Path


@dataclass(frozen=True)
class ActiveRunLock:
    run_id: UUID
    state: str
    created_at: datetime
    expires_at: datetime
    operation_name: str | None = None

    def to_bytes(self) -> bytes:
        return json.dumps(
            {
                "run_id": str(self.run_id),
                "state": self.state,
                "created_at": self.created_at.isoformat(),
                "expires_at": self.expires_at.isoformat(),
                "operation_name": self.operation_name,
            }, sort_keys=True,
        ).encode()


class RunLockStore:
    def __init__(self, store: SnapshotStore) -> None:
        self.store = store

    def acquire(
        self, object_path: str, run_id: UUID, *, pending_ttl: timedelta
    ) -> tuple[ActiveRunLock, int]:
        now = datetime.now(UTC)
        lock = ActiveRunLock(run_id, "pending", now, now + pending_ttl)
        generation = self.store.upload(object_path, lock.to_bytes(), if_generation_match=0)
        return lock, generation

    def bind_operation(
        self,
        object_path: str,
        generation: int,
        lock: ActiveRunLock,
        *,
        operation_name: str,
        running_ttl: timedelta,
    ) -> tuple[ActiveRunLock, int]:
        now = datetime.now(UTC)
        bound = ActiveRunLock(
            lock.run_id, "running", lock.created_at, now + running_ttl, operation_name
        )
        # Keep the lock continuously present.  Deleting and recreating it leaves a
        # window where a second invocation can acquire the same session.
        new_generation = self.store.upload(
            object_path, bound.to_bytes(), if_generation_match=generation
        )
        return bound, new_generation

    def release(self, object_path: str, generation: int) -> None:
        self.store.delete(object_path, generation)


@contextmanager
def request_directories() -> Iterator[RequestDirectories]:
    root = Path(tempfile.mkdtemp(prefix="cas-hosting-"))
    directories = RequestDirectories(root, root / "workspace", root / "claude-session")
    directories.workspace.mkdir()
    directories.claude_session.mkdir()
    try:
        yield directories
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _validate_member(member: tarfile.TarInfo) -> PurePosixPath:
    path = PurePosixPath(member.name)
    if path.is_absolute() or ".." in path.parts or member.isdev() or member.isfifo():
        raise WorkspaceCorruptedError("unsafe archive member")
    if member.issym() or member.islnk():
        target = PurePosixPath(member.linkname)
        if target.is_absolute() or ".." in target.parts:
            raise WorkspaceCorruptedError("unsafe archive link")
    return path


def archive_snapshot(
    source: RequestDirectories, destination: Path, *, max_bytes: int
) -> tuple[int, int]:
    total = sum(path.stat().st_size for root in (source.workspace, source.claude_session)
                for path in root.rglob("*") if path.is_file())
    if total > max_bytes:
        raise WorkspaceTooLargeError("uncompressed snapshot is too large")
    with tarfile.open(destination, "w:gz") as archive:
        archive.add(source.workspace, arcname="workspace", recursive=True)
        archive.add(source.claude_session, arcname="claude-session", recursive=True)
    compressed = destination.stat().st_size
    if compressed > max_bytes:
        destination.unlink(missing_ok=True)
        raise WorkspaceTooLargeError("compressed snapshot is too large")
    return total, compressed


def extract_snapshot(
    archive_path: Path, destination: RequestDirectories, *, max_bytes: int
) -> None:
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        total = sum(member.size for member in members if member.isfile())
        if total > max_bytes:
            raise WorkspaceTooLargeError("uncompressed snapshot is too large")
        for member in members:
            member_path = _validate_member(member)
            if not member_path.parts or member_path.parts[0] not in {"workspace", "claude-session"}:
                raise WorkspaceCorruptedError("unexpected archive root")
        archive.extractall(destination.root, members=members, filter="data")
    for root in (destination.workspace, destination.claude_session):
        for path in root.rglob("*"):
            if path.is_symlink() or not os.path.realpath(path).startswith(f"{root}{os.sep}"):
                raise WorkspaceCorruptedError("extracted path escapes root")
            if path.exists() and stat.S_ISFIFO(path.stat().st_mode):
                raise WorkspaceCorruptedError("FIFO is not allowed")


def snapshot_manifest(
    *, run_id: UUID, sdk_version: str, archive_path: Path, uncompressed_bytes: int
) -> SnapshotManifest:
    data = archive_path.read_bytes()
    return SnapshotManifest(
        claude_sdk_version=sdk_version,
        run_id=run_id,
        uncompressed_bytes=uncompressed_bytes,
        compressed_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def save_immutable_snapshot(
    store: SnapshotStore, *, object_path: str, manifest: SnapshotManifest, archive_path: Path
) -> SnapshotReference:
    data = archive_path.read_bytes()
    generation = store.upload(object_path, data, if_generation_match=0)
    return SnapshotReference(object_path=object_path, generation=generation, sha256=manifest.sha256)


def restore_verified_snapshot(
    store: SnapshotStore,
    reference: SnapshotReference,
    destination: RequestDirectories,
    *,
    max_bytes: int,
) -> None:
    data = store.download(reference.object_path, reference.generation)
    if hashlib.sha256(data).hexdigest() != reference.sha256:
        raise WorkspaceCorruptedError("snapshot SHA-256 does not match committed reference")
    archive_path = destination.root / "download.tar.gz"
    archive_path.write_bytes(data)
    try:
        extract_snapshot(archive_path, destination, max_bytes=max_bytes)
    finally:
        archive_path.unlink(missing_ok=True)


def prepare_workspace(
    directories: RequestDirectories,
    *,
    snapshot_store: SnapshotStore,
    committed_snapshot: SnapshotReference | None,
    initializer: Callable[[Path], None] | None,
    max_bytes: int,
) -> None:
    if committed_snapshot is not None:
        restore_verified_snapshot(
            snapshot_store, committed_snapshot, directories, max_bytes=max_bytes
        )
    elif initializer is not None:
        initializer(directories.workspace)
