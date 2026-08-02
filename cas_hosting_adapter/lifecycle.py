"""Prepare, execute, commit, and cleanup lifecycle boundary."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from .errors import AgentError, ConfigurationError, WorkspaceError
from .models import SessionEvent
from .protocols import SnapshotStore
from .workspace_store import (
    archive_snapshot,
    request_directories,
    save_immutable_snapshot,
    snapshot_manifest,
)


class RunLifecycle:
    """Coordinates one request without retaining state in the process."""

    def __init__(self, *, append_event: Callable[[SessionEvent], None],
                 execute_agent: Callable[[Path, Path], Awaitable[dict[str, Any]]],
                 snapshot_store: SnapshotStore | None = None,
                 snapshot_path: Callable[[UUID], str] | None = None,
                 claude_sdk_version: str = "0.2.128",
                 snapshot_max_bytes: int = 100 * 1024 * 1024) -> None:
        self.append_event = append_event
        self.execute_agent = execute_agent
        if (snapshot_store is None) != (snapshot_path is None):
            raise ConfigurationError("snapshot store and path must be configured together")
        self.snapshot_store = snapshot_store
        self.snapshot_path = snapshot_path
        self.claude_sdk_version = claude_sdk_version
        self.snapshot_max_bytes = snapshot_max_bytes

    async def run(self, *, run_id: UUID, message: str) -> str:
        if not message.strip():
            raise AgentError("message must not be blank")
        sequence = 0

        def emit(event_type: str, payload: dict[str, Any]) -> None:
            nonlocal sequence
            self.append_event(
                SessionEvent(
                    run_id=run_id,
                    sequence=sequence,
                    event_type=event_type,
                    payload=payload,
                )
            )
            sequence += 1

        with request_directories() as directories:
            emit("run_started", {"message": message})
            try:
                result = await self.execute_agent(directories.workspace, directories.claude_session)
                output = result.get("output")
                if not isinstance(output, str):
                    raise AgentError("agent did not return text output")
                emit("agent_message", {"text": output})
            except Exception as error:
                emit("failed", {"error_type": type(error).__name__})
                if isinstance(error, AgentError):
                    raise
                raise AgentError("agent execution failed") from error

            if self.snapshot_store is not None and self.snapshot_path is not None:
                try:
                    archive_path = directories.root / "snapshot.tar.gz"
                    uncompressed_bytes, _ = archive_snapshot(
                        directories, archive_path, max_bytes=self.snapshot_max_bytes
                    )
                    manifest = snapshot_manifest(
                        run_id=run_id,
                        sdk_version=self.claude_sdk_version,
                        archive_path=archive_path,
                        uncompressed_bytes=uncompressed_bytes,
                    )
                    reference = save_immutable_snapshot(
                        self.snapshot_store,
                        object_path=self.snapshot_path(run_id),
                        manifest=manifest,
                        archive_path=archive_path,
                    )
                except Exception as error:
                    emit("persistence_failed", {"error_type": type(error).__name__})
                    if isinstance(error, WorkspaceError):
                        raise
                    raise WorkspaceError("snapshot commit failed") from error
                emit(
                    "snapshot_committed",
                    {
                        "object_path": reference.object_path,
                        "generation": reference.generation,
                        "sha256": reference.sha256,
                    },
                )
            emit("completed", {"output": output})
            return output
