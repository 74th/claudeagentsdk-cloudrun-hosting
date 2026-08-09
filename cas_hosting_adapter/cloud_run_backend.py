"""Cloud Run Jobs implementation of the ExecutionBackend port."""

from __future__ import annotations

from collections.abc import Callable
from time import sleep
from typing import Any
from uuid import UUID

from .errors import (
    ConfigurationError,
    ExecutionNotFoundError,
    ExecutionPermissionError,
    ExecutionQuotaError,
    ExecutionTemporaryError,
    ValidationError,
)
from .models import ExecutionReference, ExecutionState


class CloudRunJobsBackend:
    backend_name = "cloud_run"
    def __init__(
        self, jobs: Any, executions: Any, *, project: str, region: str, job_name: str
    ) -> None:
        if not project or not region or not job_name:
            raise ConfigurationError("project, region, and job_name are required")
        expected = f"projects/{project}/locations/{region}/jobs/"
        self._job = job_name if job_name.startswith("projects/") else f"{expected}{job_name}"
        if not self._job.startswith(expected):
            raise ConfigurationError("job_name must match configured project and region")
        self._jobs, self._executions = jobs, executions

    def start(self, run_id: UUID) -> ExecutionReference:
        try:
            from google.cloud.run_v2.types import EnvVar, RunJobRequest

            overrides = RunJobRequest.Overrides(
                container_overrides=[
                    RunJobRequest.Overrides.ContainerOverride(
                        env=[EnvVar(name="RUN_ID", value=str(run_id))]
                    )
                ]
            )
            request = RunJobRequest(name=self._job, overrides=overrides)
            operation = self._jobs.run_job(request=request)
            target = getattr(getattr(operation, "metadata", None), "target", "")
            if isinstance(target, str) and "/executions/" in target:
                return ExecutionReference(
                    backend="cloud-run-jobs", name=target, identity=str(run_id)
                )
            operation_name = getattr(getattr(operation, "operation", None), "name", "")
            if isinstance(operation_name, str) and "/operations/" in operation_name:
                execution = self._find_execution_for_operation(operation_name, run_id)
                if execution is not None:
                    return ExecutionReference(
                        backend="cloud-run-jobs", name=execution, identity=str(run_id)
                    )
                # Test doubles and older clients may not support listing
                # executions. Keep the LRO reference as a compatibility
                # fallback, but production clients resolve it above because
                # generic long-running-operation cancellation is unsupported
                # by the Cloud Run API.
                return ExecutionReference(
                    backend="cloud-run-jobs", name=operation_name, identity=str(run_id)
                )
            execution = operation.result()
            return ExecutionReference(
                backend="cloud-run-jobs", name=execution.name, identity=str(run_id)
            )
        except Exception as error:
            raise _map_execution_error(error, "Cloud Run Job start failed") from error

    def _find_execution_for_operation(self, operation_name: str, run_id: UUID) -> str | None:
        """Resolve RunJob's LRO to its cancellable Cloud Run Execution."""
        list_executions = getattr(self._executions, "list_executions", None)
        if not callable(list_executions):
            return None
        operation_id = operation_name.rsplit("/", 1)[-1]
        for _ in range(10):
            try:
                for execution in list_executions(parent=self._job):
                    annotations = getattr(execution, "annotations", {}) or {}
                    containers = getattr(getattr(execution, "template", None), "containers", ())
                    has_run_id = any(
                        env.name == "RUN_ID" and env.value == str(run_id)
                        for container in containers
                        for env in getattr(container, "env", ())
                    )
                    matches_operation = (
                        annotations.get("run.googleapis.com/operation-id") == operation_id
                    )
                    if matches_operation or has_run_id:
                        return str(execution.name)
            except Exception as error:
                raise _map_execution_error(error, "Cloud Run Execution list failed") from error
            sleep(0.2)
        return None

    def dispatch_once(
        self,
        run_id: UUID,
        *,
        existing: ExecutionReference | None,
        on_orphaned_dispatch: Callable[[UUID], None] | None = None,
    ) -> ExecutionReference:
        if existing is not None:
            return existing
        try:
            return self.start(run_id)
        except ExecutionTemporaryError:
            if on_orphaned_dispatch is not None:
                on_orphaned_dispatch(run_id)
            raise

    def get(self, reference: ExecutionReference) -> ExecutionState:
        self._validate_reference(reference)
        try:
            if "/operations/" in reference.name:
                operation = self._jobs.transport.operations_client.get_operation(reference.name)
                if not operation.done:
                    return ExecutionState.PENDING
                if getattr(getattr(operation, "error", None), "code", 0):
                    return ExecutionState.FAILED
                return ExecutionState.SUCCEEDED
            execution = self._executions.get_execution(name=reference.name)
            return normalize_execution_conditions(execution.conditions)
        except Exception as error:
            raise _map_execution_error(error, "Cloud Run Execution get failed") from error

    def cancel(self, reference: ExecutionReference) -> ExecutionState:
        self._validate_reference(reference)
        if "/operations/" in reference.name:
            try:
                self._jobs.transport.operations_client.cancel_operation(reference.name)
                return ExecutionState.CANCELLED
            except Exception as error:
                raise _map_execution_error(error, "Cloud Run Job cancel failed") from error
        state = self.get(reference)
        if state in {ExecutionState.SUCCEEDED, ExecutionState.FAILED, ExecutionState.CANCELLED}:
            return state
        try:
            self._executions.cancel_execution(name=reference.name)
            return ExecutionState.CANCELLED
        except Exception as error:
            raise _map_execution_error(error, "Cloud Run Execution cancel failed") from error

    @staticmethod
    def _validate_reference(reference: ExecutionReference) -> None:
        if reference.backend != "cloud-run-jobs":
            raise ValidationError(
                "Cloud Run Jobs backend received an execution reference for another backend"
            )


def normalize_execution_conditions(conditions: Any) -> ExecutionState:
    """Map Cloud Run condition data deterministically without exposing SDK types."""
    by_type = {condition.type_: condition for condition in conditions}
    completed = by_type.get("Completed")
    if completed is not None:
        state = completed.state.name
        if state == "CONDITION_SUCCEEDED":
            return ExecutionState.SUCCEEDED
        if state == "CONDITION_FAILED":
            reason = getattr(completed.reason, "name", str(completed.reason)).lower()
            if "cancel" in reason:
                return ExecutionState.CANCELLED
            return ExecutionState.FAILED
    started = by_type.get("Started")
    if started is not None and started.state.name == "CONDITION_SUCCEEDED":
        return ExecutionState.RUNNING
    return ExecutionState.PENDING


def _map_execution_error(error: Exception, message: str) -> Exception:
    status = getattr(error, "code", None)
    status_name = getattr(status, "name", str(status)).upper()
    text = str(error).lower()
    if "NOT_FOUND" in status_name or "not found" in text:
        return ExecutionNotFoundError(message)
    if "PERMISSION_DENIED" in status_name or "permission" in text:
        return ExecutionPermissionError(message)
    if "RESOURCE_EXHAUSTED" in status_name or "quota" in text:
        return ExecutionQuotaError(message)
    if "INVALID_ARGUMENT" in status_name or "region" in text or "invalid" in text:
        return ValidationError(message)
    if status_name in {"UNAVAILABLE", "DEADLINE_EXCEEDED", "INTERNAL"}:
        return ExecutionTemporaryError(message)
    return ExecutionTemporaryError(message)
