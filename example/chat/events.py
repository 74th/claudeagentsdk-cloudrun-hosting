"""保存イベントを UI に依存しない値へ正規化する処理。"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from numbers import Real
from typing import Any
from uuid import UUID

from cas_hosting_adapter.models import ChatEvent, QuestionRequest


class ChatEventKind(StrEnum):
    USER = "user"
    AGENT = "agent"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    PROGRESS = "progress"
    FINAL = "final"
    ERROR = "error"
    TERMINAL = "terminal"
    QUESTION_PENDING = "question_pending"
    QUESTION_ANSWERED = "question_answered"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TaskState:
    """Latest provider-neutral state for one Claude Task ID."""

    task_id: str
    subject: str = ""
    description: str = ""
    status: str = "pending"
    blocked_by: tuple[str, ...] = ()
    owner: str | None = None
    deleted: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.task_id

    @property
    def dependencies(self) -> tuple[str, ...]:
        return self.blocked_by


@dataclass(frozen=True)
class InteractionState:
    pending_questions: tuple[QuestionRequest, ...] = ()
    tasks: dict[str, TaskState] = field(default_factory=dict)
    task_order: tuple[str, ...] = ()

    @property
    def questions(self) -> tuple[QuestionRequest, ...]:
        return self.pending_questions

    @property
    def task_list(self) -> list[TaskState]:
        return [self.tasks[key] for key in self.task_order if key in self.tasks]


@dataclass(frozen=True)
class ProcessingMetadata:
    """共通イベントから取り出したSDK処理メタデータ。"""

    estimated_cost_usd: int | float | None = None
    duration_ms: int | float | None = None

    @property
    def display_lines(self) -> tuple[str, ...]:
        lines: list[str] = []
        if self.estimated_cost_usd is not None:
            lines.append(f"推定価格 (USD): ${self.estimated_cost_usd:.6f}")
        if self.duration_ms is not None:
            lines.append(f"処理時間 (SDK): {self.duration_ms / 1000:.2f}秒")
        return tuple(lines)

    @property
    def display_text(self) -> str:
        return "\n".join(self.display_lines)


def _valid_processing_number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return value


def parse_processing_metadata(payload: dict[str, Any]) -> ProcessingMetadata:
    """終端イベントpayloadの任意メタデータを安全な共通値へ変換する。"""
    return ProcessingMetadata(
        estimated_cost_usd=_valid_processing_number(payload.get("estimated_cost_usd")),
        duration_ms=_valid_processing_number(payload.get("duration_ms")),
    )


processing_metadata = parse_processing_metadata


def format_processing_metadata(metadata: ProcessingMetadata) -> str:
    """共通の利用者向け処理メタデータ表示を返す。"""
    return metadata.display_text


class CommonChatEvent:
    """すべてのフロントエンドで扱えるイベント値。

    ``payload`` は保存された値を保持し、``content`` は表示に使える代表値を
    提供する。未知のイベントも ``raw_type`` を失わずに渡す。
    """

    __slots__ = (
        "id", "run_id", "sequence", "kind", "raw_type", "payload", "content", "question",
        "processing_metadata",
    )

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
        question: QuestionRequest | None = None,
        processing_metadata: ProcessingMetadata | None = None,
    ) -> None:
        self.id = id
        self.run_id = run_id
        self.sequence = sequence
        self.kind = kind
        self.raw_type = raw_type
        self.payload = dict(payload)
        self.content = content
        self.question = question
        self.processing_metadata = processing_metadata or parse_processing_metadata(self.payload)

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
            self.question,
            self.processing_metadata,
        ) == (
            other.id,
            other.run_id,
            other.sequence,
            other.kind,
            other.raw_type,
            other.payload,
            other.content,
            other.question,
            other.processing_metadata,
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
    question = None
    if event.type in {"question_pending", "question_answered"}:
        try:
            question = QuestionRequest.model_validate(event.payload)
        except Exception:
            # Event streams are forward compatible. A malformed new event is
            # still visible as an unknown-shaped common event.
            question = None
    return CommonChatEvent(
        id=event.id,
        run_id=event.run_id,
        sequence=event.sequence,
        kind=_kind(event.type),
        raw_type=event.type,
        payload=event.payload,
        content=_content(event.type, event.payload),
        question=question,
        processing_metadata=parse_processing_metadata(event.payload),
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


def _result_value(value: Any) -> Any:
    """Parse the SDK's object, JSON-string, and content-block result forms."""
    if isinstance(value, (dict, list)):
        if isinstance(value, dict):
            return value
        for block in value:
            if isinstance(block, dict):
                for key in ("json", "input", "text", "content"):
                    if key in block:
                        parsed = _result_value(block[key])
                        if isinstance(parsed, (dict, list)):
                            return parsed
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return None


def _task_id(value: dict[str, Any]) -> str | None:
    for key in ("taskId", "task_id", "id"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _task_from_value(value: Any, fallback: dict[str, Any] | None = None) -> dict[str, Any] | None:
    parsed = _result_value(value)
    if isinstance(parsed, dict):
        for key in ("task", "item", "result"):
            nested = parsed.get(key)
            if isinstance(nested, dict):
                parsed = nested
                break
        return parsed
    return fallback


def _task_state(value: dict[str, Any], fallback: dict[str, Any] | None = None) -> TaskState | None:
    merged = dict(fallback or {})
    merged.update(value)
    task_id = _task_id(merged)
    if task_id is None:
        return None
    blocked = merged.get("blockedBy", merged.get("blocked_by", merged.get("dependencies", [])))
    if not isinstance(blocked, list | tuple):
        blocked = []
    return TaskState(
        task_id=task_id,
        subject=str(merged.get("subject", merged.get("title", "")) or ""),
        description=str(merged.get("description", "") or ""),
        status=str(merged.get("status", "pending") or "pending"),
        blocked_by=tuple(str(item) for item in blocked if str(item).strip()),
        owner=str(merged["owner"]) if merged.get("owner") is not None else None,
        deleted=bool(merged.get("deleted", False) or merged.get("status") == "deleted"),
        raw=merged,
    )


def reduce_tasks(events: Iterable[CommonChatEvent]) -> dict[str, TaskState]:
    """Replay Task tool events without making the UI understand SDK payloads."""
    tasks: dict[str, TaskState] = {}
    tools: dict[str, tuple[str, dict[str, Any]]] = {}
    for event in sorted(events, key=lambda item: (item.sequence, item.id)):
        if event.type == "tool_started":
            tool_id = str(event.payload.get("tool_id") or "")
            name = str(event.payload.get("name") or "")
            if tool_id:
                tools[tool_id] = (name, event.payload.get("input", {}))
            if name == "TaskCreate":
                created = _task_state(event.payload.get("input", {}))
                if created:
                    tasks[created.task_id] = created
            continue
        if event.type != "tool_completed":
            continue
        tool_id = str(event.payload.get("tool_id") or "")
        name, input_value = tools.get(tool_id, (str(event.payload.get("name") or ""), {}))
        result = _result_value(
            event.payload.get("result", event.payload.get("content"))
        )
        if name == "TaskList":
            values = result.get("tasks") if isinstance(result, dict) else result
            if isinstance(values, list):
                snapshot: dict[str, TaskState] = {}
                for value in values:
                    if isinstance(value, dict):
                        task = _task_state(value)
                        if task and not task.deleted:
                            snapshot[task.task_id] = task
                tasks = snapshot
        elif name == "TaskCreate":
            value = _task_from_value(result, input_value if isinstance(input_value, dict) else None)
            task = _task_state(value or {}, input_value if isinstance(input_value, dict) else None)
            if task and not task.deleted:
                tasks[task.task_id] = task
        elif name in {"TaskUpdate", "TaskGet"}:
            value = _task_from_value(result)
            source = value or (input_value if isinstance(input_value, dict) else {})
            task_id = _task_id(source)
            if task_id is None:
                continue
            previous = tasks.get(task_id)
            task = _task_state(source, previous.raw if previous else None)
            if task is None:
                continue
            if task.deleted:
                tasks.pop(task_id, None)
            else:
                tasks[task_id] = task
    return tasks


def interaction_state(events: Iterable[CommonChatEvent]) -> InteractionState:
    """Build pending questions and the latest task snapshot from durable history."""
    pending: dict[str, QuestionRequest] = {}
    normalized = sorted(events, key=lambda item: (item.sequence, item.id))
    for event in normalized:
        if event.type == "question_pending" and event.question is not None:
            pending[event.question.id] = event.question
        elif event.type == "question_answered" and event.question is not None:
            pending.pop(event.question.id, None)
    tasks = reduce_tasks(normalized)
    return InteractionState(
        pending_questions=tuple(sorted(pending.values(), key=lambda question: question.ordinal)),
        tasks=tasks,
        task_order=tuple(tasks),
    )
