"""Public, SDK-independent domain models."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

_ID_NAMESPACES = {
    "session": b"cas/session/v1",
    "workspace": b"cas/workspace/v1",
    "run": b"cas/run/v1",
    "question": b"cas/question/v1",
}


def _deterministic_uuid(namespace: str, user_id: str, idempotency_key: str) -> UUID:
    if not user_id.strip():
        raise ValueError("user_id must not be blank")
    if not idempotency_key.strip():
        raise ValueError("idempotency_key must not be blank")
    digest = hashlib.sha256(
        _ID_NAMESPACES[namespace]
        + b"\0"
        + user_id.strip().encode("utf-8")
        + b"\0"
        + idempotency_key.encode("utf-8")
    ).digest()
    return UUID(bytes=digest[:16], version=4)


def derive_session_id(user_id: str, idempotency_key: str) -> str:
    """Derive the durable session ID for one initial request."""
    return str(_deterministic_uuid("session", user_id, idempotency_key))


def derive_workspace_id(user_id: str, idempotency_key: str) -> str:
    """Derive the workspace ID paired with an initial session."""
    return str(_deterministic_uuid("workspace", user_id, idempotency_key))


def derive_run_id(user_id: str, idempotency_key: str) -> UUID:
    """Derive the first run ID without embedding user input in the ID."""
    return _deterministic_uuid("run", user_id, idempotency_key)


def derive_question_id(run_id: UUID, request_key: str, ordinal: int) -> str:
    """Derive a stable question ID for retries of one SDK request."""
    if not request_key.strip():
        raise ValueError("request_key must not be blank")
    if ordinal < 1:
        raise ValueError("ordinal must be positive")
    digest = hashlib.sha256(
        _ID_NAMESPACES["question"]
        + b"\0"
        + str(run_id).encode("utf-8")
        + b"\0"
        + request_key.strip().encode("utf-8")
        + b"\0"
        + str(ordinal).encode("ascii")
    ).hexdigest()
    return f"question-{digest[:32]}"


def normalize_session_title(prompt: str, *, max_length: int = 80) -> str:
    """Create a stable, human-readable title from the first non-empty line."""
    if max_length < 1:
        raise ValueError("max_length must be positive")
    for line in prompt.splitlines():
        normalized = re.sub(r"\s+", " ", line.strip())
        if normalized:
            return normalized[:max_length]
    raise ValueError("prompt must contain a non-whitespace line")


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


class QuestionState(StrEnum):
    PENDING = "pending"
    ANSWERED = "answered"


class QuestionOption(BaseModel):
    """Provider-neutral option shown to a user."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str = Field(min_length=1, max_length=1000)
    description: str = Field(default="", max_length=4000)

    @field_validator("label")
    @classmethod
    def label_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("option label must not be blank")
        return value.strip()


class QuestionRequest(BaseModel):
    """Durable, provider-neutral representation of an SDK question."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    run_id: UUID
    ordinal: int = Field(ge=1)
    question: str = Field(min_length=1, max_length=10000)
    header: str = Field(default="", max_length=200)
    options: list[QuestionOption] = Field(min_length=2, max_length=4)
    multi_select: bool = False
    state: QuestionState = QuestionState.PENDING
    answers: list[str] | None = None
    answer_idempotency_key: str | None = None
    idempotency_key: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    answered_at: datetime | None = None
    expires_at: datetime | None = None

    @field_validator("question", "idempotency_key")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question text must not be blank")
        return value.strip()

    @field_validator("options")
    @classmethod
    def options_must_be_unique(cls, value: list[QuestionOption]) -> list[QuestionOption]:
        if len({option.label for option in value}) != len(value):
            raise ValueError("option labels must be unique")
        return value

    @classmethod
    def from_input(
        cls,
        *,
        run_id: UUID,
        request_key: str,
        ordinal: int,
        question: str,
        header: str = "",
        options: list[QuestionOption | dict[str, Any]],
        multi_select: bool = False,
        created_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> QuestionRequest:
        return cls(
            id=derive_question_id(run_id, request_key, ordinal),
            run_id=run_id,
            ordinal=ordinal,
            question=question,
            header=header,
            options=[QuestionOption.model_validate(option) for option in options],
            multi_select=multi_select,
            idempotency_key=request_key,
            created_at=created_at or datetime.now(UTC),
            expires_at=expires_at,
        )

    @property
    def pending(self) -> bool:
        return self.state is QuestionState.PENDING

    @property
    def status(self) -> QuestionState:
        return self.state

    @property
    def text(self) -> str:
        return self.question

    @property
    def answer(self) -> str | list[str] | None:
        if self.answers is None:
            return None
        return self.answers[0] if not self.multi_select and self.answers else self.answers

    def validate_answers(self, values: str | list[str] | tuple[str, ...]) -> list[str]:
        """Validate UI/Slack answers and discard the presentation-only その他 label."""
        raw = [values] if isinstance(values, str) else list(values)
        normalized = [value.strip() for value in raw if isinstance(value, str) and value.strip()]
        if not normalized:
            raise ValueError("answer must not be blank")
        if len(set(normalized)) != len(normalized):
            raise ValueError("answers must not contain duplicates")
        labels = {option.label for option in self.options}
        if all(value in labels for value in normalized):
            if not self.multi_select and len(normalized) != 1:
                raise ValueError("single-select question accepts exactly one answer")
            return normalized
        if len(normalized) != 1:
            raise ValueError("free-text answer must contain exactly one value")
        return normalized


def validate_question_batch(questions: list[QuestionRequest]) -> list[QuestionRequest]:
    if not 1 <= len(questions) <= 4:
        raise ValueError("one SDK question request must contain between 1 and 4 questions")
    ordinals = [question.ordinal for question in questions]
    if len(set(ordinals)) != len(ordinals) or any(ordinal > 4 for ordinal in ordinals):
        raise ValueError("question ordinals must be unique")
    return sorted(questions, key=lambda question: question.ordinal)


Question = QuestionRequest


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


class RunPage(BaseModel):
    """Stable, ascending page of runs belonging to one session."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    runs: list[Run]
    next_cursor: str | None = None


class InitialSessionResult(BaseModel):
    """Provider-neutral result of reserving a new session and its first run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    session: Session
    run: Run


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
