"""Versioned, SDK-free Firestore document codecs."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

from .models import ChatEvent, QuestionRequest, Run, Session

SCHEMA_VERSION = "1"
DEFAULT_RETENTION_DAYS = 30


def user_key(user_id: str) -> str:
    normalized = user_id.strip()
    if not normalized:
        raise ValueError("user_id must not be blank")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


def expiry_at(base: datetime, retention_days: int) -> datetime:
    if retention_days < 1:
        raise ValueError("retention_days must be positive")
    return _utc(base) + timedelta(days=retention_days)


def encode_session(
    session: Session,
    *,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    now: datetime | None = None,
) -> dict[str, Any]:
    payload = {"schema_version": SCHEMA_VERSION, **session.model_dump(mode="json")}
    payload["expires_at"] = expiry_at(now or session.updated_at, retention_days)
    return payload


def encode_run(
    run: Run,
    *,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    now: datetime | None = None,
) -> dict[str, Any]:
    payload = {"schema_version": SCHEMA_VERSION, **run.model_dump(mode="json")}
    payload["expires_at"] = expiry_at(now or run.created_at, retention_days)
    return payload


def encode_event(
    event: ChatEvent, *, retention_days: int = DEFAULT_RETENTION_DAYS
) -> dict[str, Any]:
    payload = {"schema_version": SCHEMA_VERSION, **event.model_dump(mode="json")}
    payload["expires_at"] = expiry_at(event.occurred_at, retention_days)
    return payload


def encode_question(
    question: QuestionRequest,
    *,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Encode a question without depending on the Firestore SDK."""
    payload = {"schema_version": SCHEMA_VERSION, **question.model_dump(mode="json")}
    payload["expires_at"] = expiry_at(
        now or question.expires_at or question.created_at, retention_days
    )
    return payload


def decode_question(payload: dict[str, Any]) -> QuestionRequest:
    """Decode legacy and current question records."""
    value = dict(payload)
    value.pop("schema_version", None)
    return QuestionRequest.model_validate(value)


def decode_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    to_datetime = getattr(value, "to_datetime", None)
    if callable(to_datetime):
        return decode_timestamp(to_datetime())
    raise ValueError("timestamp must be a datetime or RFC3339 string")


def is_expired(payload: dict[str, Any], now: datetime) -> bool:
    """Apply TTL visibility semantics even while Firestore deletion is delayed."""
    expires_at = payload.get("expires_at")
    return expires_at is not None and decode_timestamp(expires_at) <= _utc(now)
