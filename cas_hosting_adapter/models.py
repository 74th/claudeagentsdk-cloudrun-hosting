"""Public, SDK-independent domain models."""
from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class RunState(StrEnum):
    REQUESTED = "requested"
    DISPATCHING = "dispatching"
    PENDING = "pending"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    DISPATCH_FAILED = "dispatch_failed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    PERSISTENCE_FAILED = "persistence_failed"

    @property
    def active(self) -> bool:
        return self in {
            self.REQUESTED,
            self.DISPATCHING,
            self.PENDING,
            self.RUNNING,
            self.CANCEL_REQUESTED,
        }

    @property
    def terminal(self) -> bool:
        return not self.active


RUN_STATE_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.REQUESTED: frozenset(
        {RunState.DISPATCHING, RunState.DISPATCH_FAILED, RunState.CANCELLED}
    ),
    RunState.DISPATCHING: frozenset(
        {RunState.PENDING, RunState.DISPATCH_FAILED, RunState.CANCEL_REQUESTED}
    ),
    RunState.PENDING: frozenset(
        {RunState.RUNNING, RunState.CANCEL_REQUESTED, RunState.CANCELLED, RunState.FAILED}
    ),
    RunState.RUNNING: frozenset(
        {RunState.COMPLETED, RunState.FAILED, RunState.CANCEL_REQUESTED, RunState.TIMED_OUT}
    ),
    RunState.CANCEL_REQUESTED: frozenset({RunState.CANCELLED, RunState.FAILED, RunState.TIMED_OUT}),
    RunState.DISPATCH_FAILED: frozenset(),
    RunState.CANCELLED: frozenset(),
    RunState.COMPLETED: frozenset(),
    RunState.FAILED: frozenset(),
    RunState.TIMED_OUT: frozenset(),
    RunState.PERSISTENCE_FAILED: frozenset(),
}


def can_transition(current: RunState, target: RunState) -> bool:
    """Return whether a durable run state transition is allowed."""
    return target in RUN_STATE_TRANSITIONS[current]


class ExecutionState(StrEnum):
    """Provider execution states normalized at the port boundary."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class User(BaseModel):
    """Caller identity kept separate from its storage-safe key."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    key: str = Field(pattern=r"^[0-9a-f]{64}$")


class EventCursor(BaseModel):
    """Stable resume point for an ordered event stream."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sequence: int = Field(ge=0)
    event_id: str = Field(min_length=1)


class ExecutionReference(BaseModel):
    """Opaque execution backend reference; never a provider SDK object."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    backend: str = Field(min_length=1)
    name: str = Field(min_length=1)
    identity: str | None = None


class WorkspaceReference(BaseModel):
    """Provider-neutral immutable workspace snapshot reference."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    object_key: str = Field(min_length=1)
    version: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)


class ChatEvent(BaseModel):
    """An idempotent event persisted for one run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    run_id: UUID
    sequence: int = Field(ge=0)
    type: str = Field(min_length=1)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any] = Field(default_factory=dict)


class Session(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    title: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    active_run_id: UUID | None = None
    latest_run_state: str | None = None
    claude_session_id: str | None = None
    snapshot: WorkspaceReference | None = None


class Run(BaseModel):
    """Provider-neutral durable unit of agent work."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1"] = "1"
    id: UUID = Field(default_factory=uuid4)
    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    state: RunState = RunState.REQUESTED
    execution: ExecutionReference | None = None
    claude_session_id: str | None = None
    snapshot: WorkspaceReference | None = None
    event_cursor: EventCursor | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: str | None = None
    error_code: str | None = None


class SessionPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sessions: list[Session]
    next_cursor: str | None = None


class ReconciliationLease(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: UUID
    holder: str
    expires_at: datetime


class SessionEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1"] = "1"
    run_id: UUID
    sequence: int = Field(ge=0)
    event_type: str = Field(min_length=1)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any] = Field(default_factory=dict)


class SnapshotManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1"] = "1"
    claude_sdk_version: str
    run_id: UUID
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    uncompressed_bytes: int = Field(ge=0)
    compressed_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SnapshotReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    object_path: str
    generation: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
