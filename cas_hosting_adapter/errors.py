"""Stable public errors without provider-specific exception details."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorDetail:
    code: str
    retryable: bool
    stage: str


class HostingError(RuntimeError):
    detail = ErrorDetail("internal", False, "configuration")

    @property
    def code(self) -> str:
        return self.detail.code

    @property
    def retryable(self) -> bool:
        return self.detail.retryable


class ValidationError(HostingError):
    detail = ErrorDetail("validation", False, "validation")


class SessionNotFoundError(HostingError):
    detail = ErrorDetail("session_not_found", False, "session")


class SessionOwnershipError(HostingError):
    detail = ErrorDetail("session_owner_mismatch", False, "session")


class SessionExpiredError(HostingError):
    detail = ErrorDetail("session_expired", False, "session")


class SessionUnsupportedError(HostingError):
    detail = ErrorDetail("unsupported", False, "session")


class WorkspaceError(HostingError):
    detail = ErrorDetail("workspace_error", False, "workspace")


class WorkspaceCorruptedError(WorkspaceError):
    detail = ErrorDetail("workspace_corrupted", False, "workspace")


class SessionIncompatibleError(WorkspaceError):
    detail = ErrorDetail("session_incompatible", False, "workspace")


class WorkspaceTooLargeError(WorkspaceError):
    detail = ErrorDetail("workspace_too_large", False, "workspace")


class AgentError(HostingError):
    detail = ErrorDetail("agent_error", False, "agent")


class AgentQuestionTimeoutError(AgentError):
    """The agent was waiting for an answer that did not arrive in time."""

    detail = ErrorDetail("timed_out", False, "question")


class OperationError(HostingError):
    detail = ErrorDetail("operation_error", True, "operation")


class ExecutionNotFoundError(HostingError):
    detail = ErrorDetail("execution_not_found", False, "execution")


class ExecutionTemporaryError(HostingError):
    detail = ErrorDetail("execution_temporary", True, "execution")


class ExecutionPermissionError(HostingError):
    detail = ErrorDetail("execution_permission_denied", False, "execution")


class ExecutionConflictError(HostingError):
    detail = ErrorDetail("execution_conflict", False, "execution")


class ExecutionQuotaError(HostingError):
    detail = ErrorDetail("execution_quota_exceeded", True, "execution")


class ActiveRunConflictError(HostingError):
    detail = ErrorDetail("active_run", False, "conflict")


class TimeoutError(HostingError):
    detail = ErrorDetail("timed_out", False, "timeout")


class ConfigurationError(HostingError):
    detail = ErrorDetail("configuration", False, "configuration")


class QuestionNotFoundError(HostingError):
    """Safe error that does not disclose another user's question."""

    detail = ErrorDetail("question_not_found", False, "question")


class QuestionOwnershipError(HostingError):
    detail = ErrorDetail("question_owner_mismatch", False, "question")


class QuestionConflictError(HostingError):
    detail = ErrorDetail("question_conflict", False, "question")


class QuestionClosedError(HostingError):
    detail = ErrorDetail("question_closed", False, "question")
