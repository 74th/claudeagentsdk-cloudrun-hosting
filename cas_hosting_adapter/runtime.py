"""Provider-neutral contracts for an application-owned agent runtime.

This module deliberately has no Claude SDK or Google Cloud imports.  The
application supplies only agent behaviour and workspace hooks; persistence and
the run lifecycle stay on the framework side.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

WorkspaceInitializer = Callable[[Path], None]
WorkspaceSetup = Callable[[Path], None]


@dataclass(frozen=True)
class ClaudeAgentConfig:
    """Immutable application-facing Claude Agent behaviour."""

    system_prompt: str = ""
    model: str = "claude-haiku-4-5@20251001"
    allowed_tools: tuple[Any, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("model must not be blank")
        if not isinstance(self.system_prompt, str):
            raise TypeError("system_prompt must be a string")
        if not isinstance(self.allowed_tools, tuple):
            object.__setattr__(self, "allowed_tools", tuple(self.allowed_tools))
        for tool in self.allowed_tools:
            if isinstance(tool, str) and not tool.strip():
                raise ValueError("allowed_tools must not contain blank names")


@dataclass(frozen=True)
class AgentUsageRecord:
    """Immutable, provider-neutral usage data for one terminal agent run."""

    user_name: str
    run_id: UUID
    session_name: str
    estimated_cost_usd: int | float | None
    recorded_at: datetime
    duration_ms: int | None


UsageHook = Callable[[AgentUsageRecord], None]


@dataclass(frozen=True)
class RuntimePolicy:
    """Framework-owned limits and versioning shared by new and resumed runs."""

    snapshot_max_bytes: int = 100 * 1024 * 1024
    question_timeout: float | None = 300.0
    sdk_version: str = "0.2.128"
    snapshot_schema_version: str = "1"
    max_runtime: timedelta | None = None
    idle_timeout: timedelta | None = None
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        if self.snapshot_max_bytes < 1:
            raise ValueError("snapshot_max_bytes must be positive")
        if self.question_timeout is not None and self.question_timeout <= 0:
            raise ValueError("question_timeout must be positive")
        if not self.sdk_version.strip():
            raise ValueError("sdk_version must not be blank")
        if not self.snapshot_schema_version.strip():
            raise ValueError("snapshot_schema_version must not be blank")
        for name, value in (("max_runtime", self.max_runtime), ("idle_timeout", self.idle_timeout)):
            if value is not None and value.total_seconds() <= 0:
                raise ValueError(f"{name} must be positive")
        if not self.log_level.strip():
            raise ValueError("log_level must not be blank")


ExecutionStatus = Literal["completed", "cancelled", "timed_out", "failed"]


@dataclass(frozen=True)
class AgentExecutionResult:
    """The only terminal result shape used by the durable job coordinator."""

    status: ExecutionStatus
    output: str | None = None
    claude_session_id: str | None = None
    error_code: str | None = None
    snapshot_required: bool = False

    def __post_init__(self) -> None:
        if self.status == "completed" and not isinstance(self.output, str):
            raise ValueError("completed execution requires text output")
        if (
            self.status != "completed"
            and self.output is not None
            and not isinstance(self.output, str)
        ):
            raise ValueError("execution output must be text")


def immutable_tools(tools: Sequence[Any] | None) -> tuple[Any, ...]:
    """Normalize an SDK tool collection without exposing a mutable list."""

    return tuple(tools or ())


# Short aliases make the contracts convenient for applications while keeping
# the explicit names used by the public documentation.
AgentConfig = ClaudeAgentConfig
ExecutionResult = AgentExecutionResult
