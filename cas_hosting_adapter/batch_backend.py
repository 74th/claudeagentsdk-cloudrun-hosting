"""Google Cloud Batch adapter for the provider-neutral execution port.

The domain-facing part of this module contains only small immutable values and
the ``ExecutionBackend`` contract.  Google Cloud Batch protobuf types are
created by ``GoogleCloudBatchClient`` and never cross that boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Protocol
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


@dataclass(frozen=True)
class BatchJobSpec:
    """SDK-independent description of the one-task Batch job we create."""

    image: str
    machine_type: str
    cpu_milli: int
    memory_mib: int
    timeout_seconds: int
    environment: dict[str, str]
    service_account: str | None = None
    task_count: int = 1
    parallelism: int = 1
    max_retry_count: int = 0


class BatchClient(Protocol):
    def create_job(self, *, parent: str, job_id: str, job: BatchJobSpec) -> object: ...
    def get_job(self, *, name: str) -> object: ...
    def delete_job(self, *, name: str) -> object: ...


class GoogleCloudBatchClient:
    """Translate ``BatchJobSpec`` into the Google Cloud Batch SDK request."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def create_job(self, *, parent: str, job_id: str, job: BatchJobSpec) -> object:
        from google.cloud import batch_v1

        runnable = batch_v1.Runnable(
            container=batch_v1.Runnable.Container(image_uri=job.image),
            environment=batch_v1.Environment(variables=job.environment),
        )
        task_spec = batch_v1.TaskSpec(
            runnables=[runnable],
            compute_resource=batch_v1.ComputeResource(
                cpu_milli=job.cpu_milli, memory_mib=job.memory_mib
            ),
            max_run_duration={"seconds": job.timeout_seconds},
            max_retry_count=job.max_retry_count,
        )
        allocation = batch_v1.AllocationPolicy(
            instances=[
                batch_v1.AllocationPolicy.InstancePolicyOrTemplate(
                    policy=batch_v1.AllocationPolicy.InstancePolicy(
                        machine_type=job.machine_type
                    )
                )
            ]
        )
        if job.service_account:
            allocation.service_account = batch_v1.ServiceAccount(email=job.service_account)
        request = batch_v1.CreateJobRequest(parent=parent, job_id=job_id, job=batch_v1.Job(
            task_groups=[
                batch_v1.TaskGroup(
                    task_count=job.task_count,
                    parallelism=job.parallelism,
                    task_spec=task_spec,
                )
            ],
            allocation_policy=allocation,
            logs_policy=batch_v1.LogsPolicy(destination="CLOUD_LOGGING"),
        ))
        return self._client.create_job(request=request)

    def get_job(self, *, name: str) -> object:
        return self._client.get_job(name=name)

    def delete_job(self, *, name: str) -> object:
        return self._client.delete_job(name=name)


class CloudBatchBackend:
    """One deterministic, idempotent Google Cloud Batch Job per run."""

    backend_name = "cloud_batch"

    def __init__(
        self,
        client: BatchClient,
        *,
        project: str,
        region: str,
        job_id_prefix: str = "claude-agent",
        image: str = "",
        machine_type: str = "e2-standard-2",
        cpu_milli: int = 2000,
        memory_mib: int = 4096,
        task_timeout_seconds: int = 1800,
        service_account: str | None = None,
        environment: dict[str, str] | None = None,
    ) -> None:
        if not project or not region or not image:
            raise ConfigurationError("project, region, and image are required for Cloud Batch")
        if not job_id_prefix or len(job_id_prefix) > 25:
            raise ConfigurationError("job_id_prefix must be at most 25 characters")
        if cpu_milli < 1 or memory_mib < 1 or not 1 <= task_timeout_seconds <= 86400:
            raise ConfigurationError("invalid Cloud Batch resource limits")
        self._client = client
        self._project = project
        self._region = region
        self._job_id_prefix = job_id_prefix.rstrip("-")
        self._spec = BatchJobSpec(
            image=image,
            machine_type=machine_type,
            cpu_milli=cpu_milli,
            memory_mib=memory_mib,
            timeout_seconds=task_timeout_seconds,
            service_account=service_account,
            environment=dict(environment or {}),
        )
        self._references_by_run: dict[UUID, ExecutionReference] = {}
        self._cancelled: set[str] = set()

    @property
    def parent(self) -> str:
        return f"projects/{self._project}/locations/{self._region}"

    def job_id_for_run(self, run_id: UUID) -> str:
        return f"{self._job_id_prefix}-{run_id.hex}"[:63]

    def job_name_for_run(self, run_id: UUID) -> str:
        return f"{self.parent}/jobs/{self.job_id_for_run(run_id)}"

    def start(self, run_id: UUID) -> ExecutionReference:
        existing = self._references_by_run.get(run_id)
        if existing is not None:
            return existing
        job_id = self.job_id_for_run(run_id)
        name = self.job_name_for_run(run_id)
        try:
            spec = replace(
                self._spec,
                environment={
                    **self._spec.environment,
                    "RUN_ID": str(run_id),
                    # JobRunner uses the provider-neutral execution identity
                    # for its durable owner claim. Cloud Run injects this
                    # value; Batch must provide the deterministic Job name.
                    "CLOUD_RUN_EXECUTION": name,
                },
            )
            self._client.create_job(parent=self.parent, job_id=job_id, job=spec)
        except Exception as error:
            if not _is_already_exists(error):
                raise _map_batch_error(error, "Cloud Batch Job start failed") from error
            # A retry after the create succeeded converges to the deterministic
            # job.  Reading it also makes permission/not-found failures visible.
            try:
                self._client.get_job(name=name)
            except Exception as get_error:
                raise _map_batch_error(
                    get_error, "Cloud Batch existing Job lookup failed"
                ) from get_error
        reference = ExecutionReference(backend="cloud-batch", name=name, identity=str(run_id))
        self._references_by_run[run_id] = reference
        return reference

    def get(self, reference: ExecutionReference) -> ExecutionState:
        self._validate_reference(reference)
        if reference.name in self._cancelled:
            return ExecutionState.CANCELLED
        try:
            job = self._client.get_job(name=reference.name)
            return normalize_batch_job_state(job)
        except Exception as error:
            raise _map_batch_error(error, "Cloud Batch Job get failed") from error

    def cancel(self, reference: ExecutionReference) -> ExecutionState:
        self._validate_reference(reference)
        state = self.get(reference)
        if state in {ExecutionState.SUCCEEDED, ExecutionState.FAILED, ExecutionState.CANCELLED}:
            return state
        try:
            self._client.delete_job(name=reference.name)
        except Exception as error:
            if not _is_not_found(error):
                raise _map_batch_error(error, "Cloud Batch Job cancel failed") from error
        self._cancelled.add(reference.name)
        return ExecutionState.CANCELLED

    @staticmethod
    def _validate_reference(reference: ExecutionReference) -> None:
        if reference.backend != "cloud-batch":
            raise ValidationError(
                "Cloud Batch backend received an execution reference for another backend"
            )


def normalize_batch_job_state(job: Any) -> ExecutionState:
    """Map Batch SDK status values without returning SDK enums to callers."""
    status = _value(job, "status", job)
    raw_state = _value(status, "state", "")
    state = _enum_name(raw_state)
    if state in {"QUEUED", "SCHEDULED", "DELETION_IN_PROGRESS", "DELETING"}:
        return ExecutionState.PENDING
    if state in {"RUNNING", "ASSIGNED", "STARTING"}:
        return ExecutionState.RUNNING
    if state in {"SUCCEEDED", "SUCCESS"}:
        return ExecutionState.SUCCEEDED
    if state in {"FAILED", "FAILURE"}:
        return ExecutionState.FAILED
    if state in {"CANCELLED", "CANCELED", "DELETED", "CANCELLATION_IN_PROGRESS"}:
        return ExecutionState.CANCELLED
    raise ExecutionTemporaryError(f"unknown Cloud Batch Job state: {state or '<empty>'}")


def _value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _enum_name(value: Any) -> str:
    name = getattr(value, "name", value)
    return str(name).rsplit(".", 1)[-1].upper()


def _is_already_exists(error: Exception) -> bool:
    code = getattr(error, "code", None)
    return "ALREADY_EXISTS" in str(getattr(code, "name", code)).upper() or (
        "already exists" in str(error).lower()
    )


def _is_not_found(error: Exception) -> bool:
    code = getattr(error, "code", None)
    return "NOT_FOUND" in str(getattr(code, "name", code)).upper() or (
        "not found" in str(error).lower()
    )


def _map_batch_error(error: Exception, message: str) -> Exception:
    code = getattr(error, "code", None)
    code_name = str(getattr(code, "name", code)).upper()
    text = str(error).lower()
    if "NOT_FOUND" in code_name or "not found" in text or isinstance(error, KeyError):
        return ExecutionNotFoundError(message)
    if "PERMISSION_DENIED" in code_name or "permission" in text:
        return ExecutionPermissionError(message)
    if "RESOURCE_EXHAUSTED" in code_name or "quota" in text:
        return ExecutionQuotaError(message)
    if "INVALID_ARGUMENT" in code_name or "validation" in text or "invalid" in text:
        return ValidationError(message)
    return ExecutionTemporaryError(message)
