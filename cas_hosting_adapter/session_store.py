"""Agent Platform Session and Event adapter boundary."""
from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from .errors import (
    ConfigurationError,
    SessionExpiredError,
    SessionNotFoundError,
    SessionOwnershipError,
    SessionUnsupportedError,
)
from .models import RunState, SessionEvent, SnapshotReference, TranscriptComparison


class SessionsApi(Protocol):
    def create(self, *, name: str, user_id: str, config: dict[str, Any]) -> Any: ...
    def get(self, *, name: str) -> Any: ...
    def list(self, *, name: str, config: dict[str, Any] | None = None) -> Iterable[Any]: ...


class EventsApi(Protocol):
    def append(self, **kwargs: Any) -> Any: ...
    def list(self, *, name: str, config: dict[str, Any] | None = None) -> Iterable[Any]: ...


def _value(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _event_text(event: Any) -> str | None:
    content = _value(event, "content") or _value(_value(event, "config", {}), "content")
    parts = _value(content, "parts", []) if content else []
    return _value(parts[0], "text") if parts else None


def _normalize_mirror_event(event: SessionEvent) -> dict[str, str] | None:
    event_type = event.event_type
    payload = event.payload
    if event_type == "user_message":
        text = payload.get("text") or payload.get("message")
        return {"kind": "user", "text": text} if isinstance(text, str) else None
    if event_type in {"agent_message", "completed"}:
        text = payload.get("text") or payload.get("output")
        return {"kind": "assistant", "text": text} if isinstance(text, str) else None
    if event_type in {"tool_started", "tool_completed"}:
        tool = payload.get("tool_name") or payload.get("tool")
        if isinstance(tool, str):
            return {"kind": event_type, "text": tool}
    return None


def _normalize_transcript_record(record: dict[str, Any]) -> dict[str, str] | None:
    role = record.get("role") or record.get("type")
    message = record.get("message", record)
    content = _value(message, "content")
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    text = content if isinstance(content, str) else record.get("text")
    if role in {"user", "assistant"} and isinstance(text, str):
        return {"kind": role, "text": text}
    tool = record.get("tool_name") or record.get("name")
    if role in {"tool_use", "tool_result"} and isinstance(tool, str):
        return {"kind": role, "text": tool}
    return None


def compare_mirror_to_transcript(
    events: Iterable[SessionEvent], transcript_jsonl: str
) -> TranscriptComparison:
    """Compare normalized records without modifying either persistence source."""
    mirror = [entry for event in events if (entry := _normalize_mirror_event(event)) is not None]
    transcript: list[dict[str, str]] = []
    for line in transcript_jsonl.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and (entry := _normalize_transcript_record(record)) is not None:
            transcript.append(entry)
    matched = min(len(mirror), len(transcript))
    ordering_difference = mirror != transcript and sorted(mirror, key=str) == sorted(
        transcript, key=str
    )
    content_differences = [] if ordering_difference else [
        {"mirror": str(left), "transcript": str(right)}
        for left, right in zip(mirror, transcript)
        if left != right
    ]
    return TranscriptComparison(
        matched_count=matched - len(content_differences),
        missing_in_transcript=mirror[len(transcript):],
        extra_in_transcript=transcript[len(mirror):],
        ordering_difference=ordering_difference,
        content_differences=content_differences,
    )


class GoogleSessionStore:
    def __init__(self, *, project: str, location: str, agent_engine: str, sessions: SessionsApi,
                 events: EventsApi, restore_ttl: timedelta = timedelta(days=1)) -> None:
        expected_prefix = f"projects/{project}/locations/{location}/reasoningEngines/"
        if not agent_engine.startswith(expected_prefix):
            raise ConfigurationError("agent engine must match the configured project and location")
        self.project, self.location, self.agent_engine = project, location, agent_engine
        self.sessions, self.events, self.restore_ttl = sessions, events, restore_ttl

    def create(self, user_id: str) -> str:
        created = self.sessions.create(
            name=self.agent_engine, user_id=user_id, config={"wait_for_completion": True}
        )
        session = _value(created, "response") or created
        name = _value(session, "name")
        if not isinstance(name, str):
            raise SessionNotFoundError("Session Store did not return a session name")
        return name

    def get(self, session_id: str, user_id: str) -> Any:
        if not session_id.startswith(f"{self.agent_engine}/sessions/"):
            raise SessionNotFoundError("session does not belong to the configured agent engine")
        try:
            session = self.sessions.get(name=session_id)
        except Exception as error:
            if "404" in str(error) or "not found" in str(error).lower():
                raise SessionNotFoundError("session was not found") from error
            raise
        if _value(session, "user_id") != user_id:
            raise SessionOwnershipError("session belongs to another user")
        return session

    def list_for_user(self, user_id: str) -> list[Any]:
        try:
            sessions = list(
                self.sessions.list(name=self.agent_engine, config={"user_id": user_id})
            )
        except (AttributeError, NotImplementedError) as error:
            raise SessionUnsupportedError("Session list is unavailable in this SDK") from error
        return [item for item in sessions if _value(item, "user_id") == user_id]

    def append(self, session_id: str, event: SessionEvent) -> None:
        payload = event.model_dump(mode="json")
        self.events.append(
            name=session_id, author="cas-hosting-adapter",
            invocation_id=f"{event.run_id}:{event.sequence}", timestamp=event.timestamp,
            config={
                "content": {
                    "role": "system",
                    "parts": [{"text": json.dumps(payload, sort_keys=True)}],
                }
            },
        )

    def events_for_run(self, session_id: str, run_id: UUID) -> list[SessionEvent]:
        result: dict[tuple[UUID, int], SessionEvent] = {}
        for raw in self.events.list(name=session_id):
            text = _event_text(raw)
            if not text:
                continue
            try:
                event: SessionEvent = SessionEvent.model_validate_json(text)
            except ValueError:
                continue
            if event.run_id == run_id:
                result[(event.run_id, event.sequence)] = event
        return [result[key] for key in sorted(result, key=lambda item: item[1])]

    def state_for_run(self, session_id: str, run_id: UUID) -> RunState:
        events: list[SessionEvent] = self.events_for_run(session_id, run_id)
        terminal = {
            "cancelled": RunState.CANCELLED,
            "completed": RunState.COMPLETED,
            "failed": RunState.FAILED,
            "timed_out": RunState.TIMED_OUT,
            "persistence_failed": RunState.PERSISTENCE_FAILED,
        }
        for event in reversed(events):
            if event.event_type in terminal:
                return terminal[event.event_type]
            if event.event_type == "cancel_requested":
                return RunState.CANCEL_REQUESTED
            if event.event_type in {"run_started", "run_requested", "operation_bound"}:
                return RunState.RUNNING
        return RunState.REQUESTED

    def reconcile_run(
        self, session_id: str, run_id: UUID, *, operation_status: str | None
    ) -> RunState:
        """Resolve stale event mirrors using the authoritative LRO state."""
        if operation_status is None:
            return self.state_for_run(session_id, run_id)
        status = operation_status.upper()
        if status in {"SUCCEEDED", "SUCCESS", "COMPLETED"}:
            return (
                RunState.COMPLETED
                if self.latest_snapshot(session_id, run_id) is not None
                else RunState.PERSISTENCE_FAILED
            )
        if status in {"CANCELLED", "CANCELED"}:
            return RunState.CANCELLED
        if status in {"FAILED", "ERROR"}:
            return RunState.FAILED
        if status in {"RUNNING", "PENDING", "QUEUED"}:
            return RunState.RUNNING
        return self.state_for_run(session_id, run_id)

    def latest_snapshot(self, session_id: str, run_id: UUID) -> SnapshotReference | None:
        events: list[SessionEvent] = self.events_for_run(session_id, run_id)
        for event in reversed(events):
            if event.event_type == "snapshot_committed":
                return SnapshotReference.model_validate(event.payload)
        return None

    def ensure_fresh(self, last_commit: datetime) -> None:
        if datetime.now(UTC) - last_commit > self.restore_ttl:
            raise SessionExpiredError("session restore period has expired")
