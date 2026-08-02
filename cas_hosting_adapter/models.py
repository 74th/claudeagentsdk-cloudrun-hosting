"""Public, SDK-independent domain models."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RunState(StrEnum):
    REQUESTED = "requested"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    PERSISTENCE_FAILED = "persistence_failed"

    @property
    def active(self) -> bool:
        return self in {self.REQUESTED, self.RUNNING, self.CANCEL_REQUESTED}


class HostingSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project: str
    location: str
    agent_engine: str
    bucket_name: str
    claude_sdk_version: str = "0.2.128"
    schema_version: Literal["1"] = "1"
    max_message_chars: int = Field(default=1000, ge=1)
    max_execution_seconds: int = Field(default=1800, ge=1)
    idle_timeout_seconds: int = Field(default=1800, ge=1)
    restore_ttl: timedelta = Field(default=timedelta(days=1))
    snapshot_max_bytes: int = Field(default=100 * 1024 * 1024, ge=1)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


class RunIdentifiers(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str
    run_id: UUID = Field(default_factory=uuid4)
    operation_name: str | None = None
    claude_session_id: str | None = None
    workspace_id: str


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


class RunStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    identifiers: RunIdentifiers
    state: RunState
    events: list[SessionEvent] = Field(default_factory=list)
    output: str | None = None
    error_code: str | None = None


class AsyncRun(BaseModel):
    """Identifiers returned after an async Agent Platform run has started."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str
    run_id: UUID
    operation_name: str
    workspace_id: str


class TranscriptComparison(BaseModel):
    """Read-only comparison between the Session mirror and Claude transcript."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    matched_count: int
    missing_in_transcript: list[dict[str, str]] = Field(default_factory=list)
    extra_in_transcript: list[dict[str, str]] = Field(default_factory=list)
    ordering_difference: bool = False
    content_differences: list[dict[str, str]] = Field(default_factory=list)


class InvocationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1)
    message: str = Field(min_length=1, max_length=1000)
    session_id: str | None = None
    run_id: UUID | None = None

    @field_validator("message")
    @classmethod
    def no_blank_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value
