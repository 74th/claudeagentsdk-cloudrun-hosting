"""保存イベントを UI に依存しない値へ正規化する処理。"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Any
from uuid import UUID

from cas_hosting_adapter.models import ChatEvent


class ChatEventKind(StrEnum):
    USER = "user"
    AGENT = "agent"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    PROGRESS = "progress"
    FINAL = "final"
    ERROR = "error"
    TERMINAL = "terminal"
    UNKNOWN = "unknown"


class CommonChatEvent:
    """すべてのフロントエンドで扱えるイベント値。

    ``payload`` は保存された値を保持し、``content`` は表示に使える代表値を
    提供する。未知のイベントも ``raw_type`` を失わずに渡す。
    """

    __slots__ = ("id", "run_id", "sequence", "kind", "raw_type", "payload", "content")

    def __init__(
        self,
        *,
        id: str,
        run_id: UUID,
        sequence: int,
        kind: ChatEventKind,
        raw_type: str,
        payload: dict[str, Any],
        content: str | None,
    ) -> None:
        self.id = id
        self.run_id = run_id
        self.sequence = sequence
        self.kind = kind
        self.raw_type = raw_type
        self.payload = dict(payload)
        self.content = content

    @property
    def type(self) -> str:
        """既存表示コード向けのイベント種別。"""
        return self.raw_type

    @property
    def event_type(self) -> str:
        return self.raw_type

    @property
    def display_content(self) -> str | None:
        return self.content

    def __repr__(self) -> str:
        return (
            f"CommonChatEvent(id={self.id!r}, run_id={self.run_id!s}, "
            f"sequence={self.sequence}, kind={self.kind.value!r})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CommonChatEvent):
            return NotImplemented
        return (
            self.id,
            self.run_id,
            self.sequence,
            self.kind,
            self.raw_type,
            self.payload,
            self.content,
        ) == (
            other.id,
            other.run_id,
            other.sequence,
            other.kind,
            other.raw_type,
            other.payload,
            other.content,
        )


def _kind(event_type: str) -> ChatEventKind:
    try:
        return ChatEventKind(event_type)
    except ValueError:
        return ChatEventKind.UNKNOWN


def _content(event_type: str, payload: dict[str, Any]) -> str | None:
    if event_type in {"agent", "user"} and isinstance(payload.get("content"), str):
        return payload["content"]
    if event_type == "final" and isinstance(payload.get("output"), str):
        return payload["output"]
    for key in ("description", "message", "output", "content"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return None


def normalize_event(event: ChatEvent) -> CommonChatEvent:
    """``ChatEvent`` を共通表現へ変換する。"""
    return CommonChatEvent(
        id=event.id,
        run_id=event.run_id,
        sequence=event.sequence,
        kind=_kind(event.type),
        raw_type=event.type,
        payload=event.payload,
        content=_content(event.type, event.payload),
    )


def _legacy_tool_events(event: ChatEvent) -> list[ChatEvent]:
    """旧版で user event に保存された SDK tool block を復元する。"""
    content = event.payload.get("content")
    if event.type != "user" or not isinstance(content, list):
        return [event]
    converted: list[ChatEvent] = []
    for index, block in enumerate(content):
        if not isinstance(block, dict):
            continue
        if "tool_use_id" in block:
            converted.append(
                event.model_copy(
                    update={
                        "id": f"{event.id}:tool-result:{index}",
                        "type": "tool_completed",
                        "payload": {
                            "tool_id": block.get("tool_use_id", ""),
                            "content": block.get("content"),
                            "is_error": bool(block.get("is_error", False)),
                        },
                    }
                )
            )
        elif "name" in block and "input" in block:
            converted.append(
                event.model_copy(
                    update={
                        "id": f"{event.id}:tool-use:{index}",
                        "type": "tool_started",
                        "payload": {
                            "tool_id": block.get("id", ""),
                            "name": block.get("name", "ツール"),
                            "input": block.get("input", {}),
                        },
                    }
                )
            )
    return converted or [event]


def normalize_events(events: Iterable[ChatEvent]) -> list[CommonChatEvent]:
    """履歴を順序付け、旧形式を復元して共通表現へ変換する。"""
    expanded = [converted for event in events for converted in _legacy_tool_events(event)]
    ordered = sorted(expanded, key=lambda item: (item.sequence, item.id))
    return [normalize_event(event) for event in ordered]
