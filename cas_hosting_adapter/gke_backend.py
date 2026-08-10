"""Kubernetes Job implementation of the provider-neutral execution port."""

from __future__ import annotations

from dataclasses import dataclass
from time import sleep
from typing import Any, Protocol
from uuid import UUID

from .errors import (
    ConfigurationError,
    ExecutionConflictError,
    ExecutionNotFoundError,
    ExecutionPermissionError,
    ExecutionQuotaError,
    ExecutionTemporaryError,
    ValidationError,
)
from .models import ExecutionReference, ExecutionState

RUN_ID_LABEL = "run-id"
EXECUTION_LABEL = "cas.dev/execution"
_FORBIDDEN_ENVIRONMENT_TOKENS = (
    "prompt",
    "message",
    "token",
    "password",
    "secret",
    "credential",
    "api_key",
)


@dataclass(frozen=True)
class GKEJobSpec:
    """SDK-independent description of one single-run Kubernetes Job."""

    image: str
    namespace: str
    service_account: str
    cpu: str
    memory: str
    timeout_seconds: int
    ttl_seconds_after_finished: int
    environment: dict[str, str]
    backoff_limit: int = 0
    parallelism: int = 1
    completions: int = 1


class KubernetesBatchClient(Protocol):
    def create_job(self, *, namespace: str, body: dict[str, Any]) -> object: ...

    def get_job(self, *, namespace: str, name: str) -> object: ...

    def delete_job(
        self, *, namespace: str, name: str, propagation_policy: str
    ) -> object: ...


class KubernetesBatchApiClient:
    """Translate the small backend client protocol to ``BatchV1Api``."""

    def __init__(self, api: Any) -> None:
        self._api = api

    def create_job(self, *, namespace: str, body: dict[str, Any]) -> object:
        return self._api.create_namespaced_job(namespace=namespace, body=body)

    def get_job(self, *, namespace: str, name: str) -> object:
        return self._api.read_namespaced_job(name=name, namespace=namespace)

    def delete_job(
        self, *, namespace: str, name: str, propagation_policy: str
    ) -> object:
        from kubernetes.client import V1DeleteOptions  # type: ignore[import-untyped]

        return self._api.delete_namespaced_job(
            name=name,
            namespace=namespace,
            body=V1DeleteOptions(propagation_policy=propagation_policy),
            propagation_policy=propagation_policy,
        )


def create_kubernetes_batch_client(
    *, kubeconfig: str | None = None, context: str | None = None
) -> KubernetesBatchApiClient:
    """Load the caller's existing kubeconfig and construct a Batch API client."""
    from kubernetes import client, config  # type: ignore[import-untyped]

    config.load_kube_config(config_file=kubeconfig, context=context or None)
    return KubernetesBatchApiClient(client.BatchV1Api())


class GKEJobsBackend:
    """One deterministic, idempotent Kubernetes Job per run."""

    backend_name = "gke"

    def __init__(
        self,
        client: KubernetesBatchClient,
        *,
        image: str,
        namespace: str = "claude-agent",
        service_account: str = "claude-agent",
        cpu: str = "1",
        memory: str = "2Gi",
        task_timeout_seconds: int = 1800,
        job_ttl_seconds: int = 3600,
        environment: dict[str, str] | None = None,
        cancel_poll_attempts: int = 60,
        cancel_poll_interval_seconds: float = 0.5,
    ) -> None:
        if not image.strip() or not namespace.strip() or not service_account.strip():
            raise ConfigurationError("image, namespace, and service_account are required for GKE")
        if not cpu.strip() or not memory.strip():
            raise ConfigurationError("GKE CPU and memory must not be blank")
        if not 1 <= task_timeout_seconds <= 86400:
            raise ConfigurationError("task_timeout_seconds must be between 1 and 86400")
        if job_ttl_seconds < 1:
            raise ConfigurationError("job_ttl_seconds must be positive")
        if cancel_poll_attempts < 1 or cancel_poll_interval_seconds < 0:
            raise ConfigurationError("invalid GKE cancellation polling settings")
        self._client = client
        self._namespace = namespace
        self._service_account = service_account
        self._spec = GKEJobSpec(
            image=image,
            namespace=namespace,
            service_account=service_account,
            cpu=cpu,
            memory=memory,
            timeout_seconds=task_timeout_seconds,
            ttl_seconds_after_finished=job_ttl_seconds,
            environment=dict(environment or {}),
        )
        self._cancel_poll_attempts = cancel_poll_attempts
        self._cancel_poll_interval_seconds = cancel_poll_interval_seconds
        self._references_by_run: dict[UUID, ExecutionReference] = {}
        self._cancelled: set[str] = set()

    @property
    def namespace(self) -> str:
        return self._namespace

    def job_name_for_run(self, run_id: UUID) -> str:
        return f"claude-agent-{run_id.hex}"

    def job_reference_for_run(self, run_id: UUID) -> ExecutionReference:
        return ExecutionReference(
            backend="gke",
            name=f"{self._namespace}/{self.job_name_for_run(run_id)}",
            identity=str(run_id),
        )

    def build_job_manifest(self, run_id: UUID) -> dict[str, Any]:
        """Build a Kubernetes API manifest without carrying input or credentials."""
        environment = {
            **self._spec.environment,
            "RUN_ID": str(run_id),
            # JobRunner's established provider-neutral owner-claim contract.
            "CLOUD_RUN_EXECUTION": f"{self._namespace}/{self.job_name_for_run(run_id)}",
        }
        for name in environment:
            if any(token in name.lower() for token in _FORBIDDEN_ENVIRONMENT_TOKENS):
                raise ConfigurationError(f"forbidden secret or input environment variable: {name}")
        labels = {RUN_ID_LABEL: str(run_id), EXECUTION_LABEL: "claude-agent"}
        return {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": self.job_name_for_run(run_id), "labels": labels},
            "spec": {
                "parallelism": self._spec.parallelism,
                "completions": self._spec.completions,
                "backoffLimit": self._spec.backoff_limit,
                "activeDeadlineSeconds": self._spec.timeout_seconds,
                "ttlSecondsAfterFinished": self._spec.ttl_seconds_after_finished,
                "template": {
                    "metadata": {"labels": labels},
                    "spec": {
                        "serviceAccountName": self._spec.service_account,
                        "restartPolicy": "Never",
                        "containers": [
                            {
                                "name": "agent",
                                "image": self._spec.image,
                                "env": [
                                    {"name": name, "value": value}
                                    for name, value in environment.items()
                                ],
                                "resources": {
                                    "requests": {
                                        "cpu": self._spec.cpu,
                                        "memory": self._spec.memory,
                                    },
                                    "limits": {
                                        "cpu": self._spec.cpu,
                                        "memory": self._spec.memory,
                                    },
                                },
                            }
                        ],
                    },
                },
            },
        }

    def start(self, run_id: UUID) -> ExecutionReference:
        existing = self._references_by_run.get(run_id)
        if existing is not None:
            return existing
        reference = self.job_reference_for_run(run_id)
        try:
            self._client.create_job(
                namespace=self._namespace, body=self.build_job_manifest(run_id)
            )
        except Exception as error:
            if not _is_conflict(error):
                raise _map_kubernetes_error(error, "GKE Job start failed") from error
            try:
                existing_job = self._client.get_job(
                    namespace=self._namespace, name=self.job_name_for_run(run_id)
                )
            except Exception as lookup_error:
                raise _map_kubernetes_error(
                    lookup_error, "GKE existing Job lookup failed"
                ) from lookup_error
            labels = _value(_value(existing_job, "metadata", {}), "labels", {}) or {}
            if labels.get(RUN_ID_LABEL) != str(run_id):
                raise ExecutionConflictError("GKE Job name is occupied by another run") from error
        self._references_by_run[run_id] = reference
        return reference

    def get(self, reference: ExecutionReference) -> ExecutionState:
        self._validate_reference(reference)
        if reference.name in self._cancelled:
            return ExecutionState.CANCELLED
        namespace, name = _split_reference(reference)
        try:
            job = self._client.get_job(namespace=namespace, name=name)
            return normalize_gke_job_state(job)
        except Exception as error:
            if isinstance(error, ExecutionTemporaryError):
                raise
            raise _map_kubernetes_error(error, "GKE Job get failed") from error

    def cancel(self, reference: ExecutionReference) -> ExecutionState:
        self._validate_reference(reference)
        if reference.name in self._cancelled:
            return ExecutionState.CANCELLED
        state = self.get(reference)
        if state in {ExecutionState.SUCCEEDED, ExecutionState.FAILED, ExecutionState.CANCELLED}:
            return state
        namespace, name = _split_reference(reference)
        try:
            self._client.delete_job(
                namespace=namespace, name=name, propagation_policy="Foreground"
            )
        except Exception as error:
            if not _is_not_found(error):
                raise _map_kubernetes_error(error, "GKE Job cancel failed") from error
            self._cancelled.add(reference.name)
            return ExecutionState.CANCELLED

        for _ in range(self._cancel_poll_attempts):
            try:
                current = self.get(reference)
            except ExecutionNotFoundError:
                self._cancelled.add(reference.name)
                return ExecutionState.CANCELLED
            if current in {ExecutionState.SUCCEEDED, ExecutionState.FAILED}:
                return current
            sleep(self._cancel_poll_interval_seconds)
        raise ExecutionTemporaryError("GKE Job deletion was not confirmed")

    @staticmethod
    def _validate_reference(reference: ExecutionReference) -> None:
        if reference.backend != "gke":
            raise ValidationError("GKE backend received an execution reference for another backend")


GKEJobBackend = GKEJobsBackend


def normalize_gke_job_state(job: Any) -> ExecutionState:
    """Map Job conditions and counters without returning Kubernetes objects."""
    status = _value(job, "status", {}) or {}
    deletion_timestamp = _value(status, "deletion_timestamp", None)
    if deletion_timestamp is None:
        deletion_timestamp = _value(_value(job, "metadata", {}), "deletion_timestamp", None)
    if deletion_timestamp is not None:
        return ExecutionState.CANCELLED
    conditions = _value(status, "conditions", ()) or ()
    for condition in conditions:
        condition_type = str(_value(condition, "type", "")).lower()
        condition_status = str(_value(condition, "status", "")).lower()
        if condition_status != "true":
            continue
        if condition_type == "complete":
            return ExecutionState.SUCCEEDED
        if condition_type == "failed":
            return ExecutionState.FAILED
    if int(_value(status, "failed", 0) or 0) > 0:
        return ExecutionState.FAILED
    if int(_value(status, "succeeded", 0) or 0) >= 1:
        return ExecutionState.SUCCEEDED
    if int(_value(status, "active", 0) or 0) >= 1:
        return ExecutionState.RUNNING
    return ExecutionState.PENDING


def _split_reference(reference: ExecutionReference) -> tuple[str, str]:
    try:
        namespace, name = reference.name.split("/", 1)
    except ValueError as error:
        raise ValidationError("GKE execution reference must be namespace/job-name") from error
    if not namespace or not name:
        raise ValidationError("GKE execution reference must be namespace/job-name")
    return namespace, name


def _value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _status(error: Exception) -> int | None:
    value = getattr(error, "status", None)
    if isinstance(value, int):
        return value
    code = getattr(error, "code", None)
    if isinstance(code, int):
        return code
    return None


def _error_text(error: Exception) -> str:
    return f"{getattr(error, 'reason', '')} {error}".lower()


def _is_not_found(error: Exception) -> bool:
    return _status(error) == 404 or "not found" in _error_text(error)


def _is_conflict(error: Exception) -> bool:
    text = _error_text(error)
    return _status(error) == 409 or "conflict" in text or "already exists" in text


def _map_kubernetes_error(error: Exception, message: str) -> Exception:
    status = _status(error)
    text = _error_text(error)
    if status == 404 or "not found" in text:
        return ExecutionNotFoundError(message)
    if (
        status in {401, 403}
        or "unauthorized" in text
        or "permission" in text
        or "forbidden" in text
    ):
        return ExecutionPermissionError(message)
    if status == 409 or "conflict" in text or "already exists" in text:
        return ExecutionConflictError(message)
    if status == 429 or "quota" in text or "too many requests" in text:
        return ExecutionQuotaError(message)
    if status is not None and 400 <= status < 500:
        return ValidationError(message)
    return ExecutionTemporaryError(message)
