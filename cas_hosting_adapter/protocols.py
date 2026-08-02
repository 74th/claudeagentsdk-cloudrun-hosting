"""Provider seams and deterministic in-memory fakes."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from .models import SessionEvent


class Clock(Protocol):
    def now(self) -> datetime: ...


class SessionEvents(Protocol):
    def append(self, session_id: str, event: SessionEvent) -> None: ...
    def list(self, session_id: str) -> list[SessionEvent]: ...


class SnapshotStore(Protocol):
    def upload(self, object_path: str, data: bytes, *, if_generation_match: int) -> int: ...
    def download(self, object_path: str, generation: int) -> bytes: ...
    def delete(self, object_path: str, generation: int) -> None: ...


class ActiveRunLockStore(Protocol):
    def acquire(self, session_id: str, run_id: UUID) -> int: ...
    def release(self, session_id: str, generation: int) -> None: ...


class Operations(Protocol):
    def start(self, *, input_payload: dict[str, Any]) -> str: ...
    def get(self, operation_name: str) -> str: ...
    def cancel(self, operation_name: str) -> None: ...


class AgentFactory(Protocol):
    async def run(self, *, prompt: str, workspace: Path, transcript_dir: Path,
                  resume: str | None = None) -> str: ...


class InMemoryClock:
    def __init__(self, current: datetime | None = None) -> None:
        self.current = current or datetime.now(UTC)
    def now(self) -> datetime:
        return self.current


class InMemoryEvents:
    def __init__(self) -> None:
        self.events: dict[str, list[SessionEvent]] = {}
    def append(self, session_id: str, event: SessionEvent) -> None:
        self.events.setdefault(session_id, []).append(event)
    def list(self, session_id: str) -> list[SessionEvent]:
        return list(self.events.get(session_id, []))


class InMemorySnapshotStore:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[int, bytes]] = {}
        self.next_generation = 1

    def upload(self, object_path: str, data: bytes, *, if_generation_match: int) -> int:
        current = self.objects.get(object_path)
        if if_generation_match == 0:
            if current is not None:
                raise FileExistsError(object_path)
        elif current is None or current[0] != if_generation_match:
            raise FileNotFoundError(object_path)
        generation = self.next_generation
        self.next_generation += 1
        self.objects[object_path] = (generation, data)
        return generation

    def download(self, object_path: str, generation: int) -> bytes:
        found_generation, data = self.objects[object_path]
        if found_generation != generation:
            raise FileNotFoundError(object_path)
        return data

    def delete(self, object_path: str, generation: int) -> None:
        self.download(object_path, generation)
        del self.objects[object_path]


class InMemoryActiveRunLocks:
    def __init__(self) -> None:
        self.locks: dict[str, tuple[int, UUID]] = {}
        self.next_generation = 1

    def acquire(self, session_id: str, run_id: UUID) -> int:
        if session_id in self.locks:
            raise FileExistsError(session_id)
        generation = self.next_generation
        self.next_generation += 1
        self.locks[session_id] = (generation, run_id)
        return generation

    def release(self, session_id: str, generation: int) -> None:
        if self.locks[session_id][0] != generation:
            raise FileNotFoundError(session_id)
        del self.locks[session_id]


class InMemoryOperations:
    def __init__(self) -> None:
        self.statuses: dict[str, str] = {}
        self.requests: dict[str, dict[str, Any]] = {}

    def start(self, *, input_payload: dict[str, Any]) -> str:
        name = f"operations/{len(self.statuses) + 1}"
        self.statuses[name] = "RUNNING"
        self.requests[name] = input_payload
        return name

    def get(self, operation_name: str) -> str:
        return self.statuses[operation_name]

    def cancel(self, operation_name: str) -> None:
        self.statuses[operation_name] = "CANCELLED"
