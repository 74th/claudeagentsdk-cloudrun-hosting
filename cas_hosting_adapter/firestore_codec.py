"""Versioned, SDK-free Firestore document codecs."""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from .models import ChatEvent, Run, Session

SCHEMA_VERSION = "1"


def user_key(user_id: str) -> str:
    normalized = user_id.strip()
    if not normalized:
        raise ValueError("user_id must not be blank")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def encode_session(session: Session) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, **session.model_dump(mode="json")}


def encode_run(run: Run) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, **run.model_dump(mode="json")}


def encode_event(event: ChatEvent) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, **event.model_dump(mode="json")}


def decode_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    raise ValueError("timestamp must be a datetime or RFC3339 string")
