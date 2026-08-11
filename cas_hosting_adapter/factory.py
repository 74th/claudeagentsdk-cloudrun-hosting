"""Provider-neutral application assembly and Google Cloud client construction."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal, Protocol, cast

from .batch_backend import BatchClient, CloudBatchBackend, GoogleCloudBatchClient
from .cloud_run_backend import CloudRunJobsBackend
from .control_client import ControlClient
from .firestore_chat_store import FirestoreChatStore
from .firestore_codec import DEFAULT_RETENTION_DAYS
from .gke_backend import GKEJobsBackend, KubernetesBatchClient, create_kubernetes_batch_client
from .protocols import AgentFactory, ChatStore, Clock, ExecutionBackend, WorkspaceStore
from .runtime import (
    ClaudeAgentConfig,
    RuntimePolicy,
    UsageHook,
    WorkspaceInitializer,
    WorkspaceSetup,
)

LOGGER = logging.getLogger(__name__)


class IdentityProvider(Protocol):
    def user_id(self) -> str: ...


@dataclass(frozen=True)
class ApplicationComponents:
    execution_backend: ExecutionBackend
    chat_store: ChatStore
    workspace_store: WorkspaceStore
    agent: AgentFactory
    clock: Clock
    identity_provider: IdentityProvider


@dataclass(frozen=True)
class GoogleCloudSettings:
    project: str
    region: str
    firestore_database: str
    bucket_name: str
    job_name: str = "test-claudesdk-cloudrun"
    retention_days: int = DEFAULT_RETENTION_DAYS
    execution_platform: Literal["cloud-run", "cloud-batch", "gke"] = "cloud-run"
    image: str = ""
    job_service_account: str | None = None
    batch_job_id_prefix: str = "claude-agent"
    batch_machine_type: str = "e2-standard-2"
    batch_cpu_milli: int = 2000
    batch_memory_mib: int = 4096
    task_timeout_seconds: int = 1800
    question_timeout_seconds: int = 300
    vertex_region: str = "us-east5"
    claude_model: str = "claude-haiku-4-5@20251001"
    log_level: str = "INFO"
    gke_cluster: str = ""
    gke_cluster_region: str = ""
    gke_namespace: str = "claude-agent"
    gke_ksa_name: str = "claude-agent"
    gke_kube_context: str = ""
    gke_kubeconfig: str | None = None
    gke_cpu: str = "1"
    gke_memory: str = "2Gi"
    gke_job_ttl_seconds: int = 3600

    @classmethod
    def from_environment(
        cls, environment: dict[str, str] | None = None
    ) -> GoogleCloudSettings:
        """Parse and validate job configuration at the Cloud composition edge."""

        values = os.environ if environment is None else environment

        def required(name: str) -> str:
            value = values.get(name, "").strip()
            if not value:
                raise ValueError(f"{name} must not be blank")
            return value

        def positive_int(name: str, default: int) -> int:
            try:
                value = int(values.get(name, str(default)))
            except ValueError as error:
                raise ValueError(f"{name} must be a positive integer") from error
            if value < 1:
                raise ValueError(f"{name} must be a positive integer")
            return value

        return cls(
            project=required("GOOGLE_CLOUD_PROJECT"),
            region=values.get("CLOUD_RUN_REGION", "us-central1"),
            firestore_database=required("FIRESTORE_DATABASE"),
            bucket_name=required("WORKSPACE_BUCKET"),
            retention_days=positive_int("RUN_RETENTION_DAYS", DEFAULT_RETENTION_DAYS),
            task_timeout_seconds=positive_int("TASK_TIMEOUT_SECONDS", 1800),
            question_timeout_seconds=positive_int("QUESTION_TIMEOUT_SECONDS", 300),
            vertex_region=values.get("CLOUD_ML_REGION", "us-east5"),
            claude_model=values.get("CLAUDE_MODEL", "claude-haiku-4-5@20251001"),
            log_level=values.get("LOG_LEVEL", "INFO"),
        )

    def __post_init__(self) -> None:
        if self.retention_days < 1:
            raise ValueError("retention_days must be positive")
        if not all(
            value.strip()
            for value in (
                self.project,
                self.region,
                self.firestore_database,
                self.bucket_name,
                self.job_name
                if self.execution_platform == "cloud-run"
                else self.batch_job_id_prefix
                if self.execution_platform == "cloud-batch"
                else self.gke_cluster,
            )
        ):
            raise ValueError("Google Cloud settings must not be blank")
        expected = f"projects/{self.project}/locations/{self.region}/jobs/"
        if self.job_name.startswith("projects/") and not self.job_name.startswith(expected):
            raise ValueError("Cloud Run Job resource name must match project and region")
        if self.execution_platform not in {"cloud-run", "cloud-batch", "gke"}:
            raise ValueError("execution_platform must be cloud-run, cloud-batch, or gke")
        if self.batch_cpu_milli < 1 or self.batch_memory_mib < 1:
            raise ValueError("Batch CPU and memory must be positive")
        if not 1 <= self.task_timeout_seconds <= 86400:
            raise ValueError("task_timeout_seconds must be between 1 and 86400")
        if not 1 <= self.question_timeout_seconds <= 86400:
            raise ValueError("question_timeout_seconds must be between 1 and 86400")
        if self.execution_platform == "gke":
            if not self.gke_cluster_region or not self.gke_kube_context:
                raise ValueError("GKE cluster region and kube context are required")
            if not self.gke_cpu.strip() or not self.gke_memory.strip():
                raise ValueError("GKE CPU and memory must not be blank")
            if not 1 <= self.gke_job_ttl_seconds <= 86400:
                raise ValueError("gke_job_ttl_seconds must be between 1 and 86400")


@dataclass(frozen=True)
class GoogleCloudClients:
    """SDK clients confined to the Google Cloud composition boundary."""

    firestore: object
    storage: object
    jobs: object | None = None
    executions: object | None = None
    batch: object | None = None
    kubernetes_batch: KubernetesBatchClient | None = None


@dataclass(frozen=True)
class GoogleCloudJobComposition:
    """Ready-to-run Cloud composition; no provider state leaks into examples."""

    chat_store: ChatStore
    workspace_store: WorkspaceStore
    runtime_policy: RuntimePolicy

    async def run_from_environment(
        self,
        agent_config: ClaudeAgentConfig,
        *,
        workspace_initializer: WorkspaceInitializer | None = None,
        workspace_setup: WorkspaceSetup | None = None,
        usage_hook: UsageHook | None = None,
        environment: dict[str, str] | None = None,
    ) -> int:
        from .agent_adapter import ClaudeAgentAdapter
        from .job_runner import JobInvocation

        invocation = JobInvocation.from_environment(environment)
        adapter = ClaudeAgentAdapter(
            agent_config=agent_config,
            chat_store=self.chat_store,
            workspace_store=self.workspace_store,
            runtime_policy=self.runtime_policy,
            workspace_initializer=workspace_initializer,
            workspace_setup=workspace_setup,
            usage_hook=usage_hook,
        )
        return await adapter.run_job(invocation)


def create_google_cloud_job_composition(
    settings: GoogleCloudSettings | None = None,
    *,
    environment: dict[str, str] | None = None,
) -> GoogleCloudJobComposition:
    """Build stores and one shared runtime policy for a Cloud Run Job."""

    resolved = settings or GoogleCloudSettings.from_environment(environment)
    logging.basicConfig(
        level=resolved.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    clients = create_google_cloud_clients(resolved)
    from .google_adapters import GCSWorkspaceStore

    storage = cast(Any, clients.storage)
    bucket = storage.bucket(resolved.bucket_name)
    return GoogleCloudJobComposition(
        chat_store=FirestoreChatStore(
            clients.firestore, retention_days=resolved.retention_days
        ),
        workspace_store=GCSWorkspaceStore(bucket),
        runtime_policy=RuntimePolicy(
            question_timeout=float(resolved.question_timeout_seconds),
            max_runtime=timedelta(seconds=resolved.task_timeout_seconds),
            idle_timeout=timedelta(seconds=resolved.task_timeout_seconds),
            sdk_version=RuntimePolicy().sdk_version,
            log_level=resolved.log_level,
        ),
    )


def create_google_cloud_clients(settings: GoogleCloudSettings) -> GoogleCloudClients:
    from google.cloud.firestore import Client as FirestoreClient
    from google.cloud.storage import Client as StorageClient  # type: ignore[import-untyped]
    clients = {
        "firestore": FirestoreClient(
            project=settings.project, database=settings.firestore_database
        ),
        "storage": StorageClient(project=settings.project),
    }
    if settings.execution_platform == "cloud-run":
        from google.cloud.run_v2 import ExecutionsClient, JobsClient

        return GoogleCloudClients(
            **clients, jobs=JobsClient(), executions=ExecutionsClient()
        )
    if settings.execution_platform == "gke":
        return GoogleCloudClients(
            **clients,
            kubernetes_batch=create_kubernetes_batch_client(
                kubeconfig=settings.gke_kubeconfig, context=settings.gke_kube_context
            ),
        )
    from google.cloud.batch_v1 import BatchServiceClient

    return GoogleCloudClients(**clients, batch=GoogleCloudBatchClient(BatchServiceClient()))


def create_google_cloud_control_client(settings: GoogleCloudSettings) -> ControlClient:
    """Build the Streamlit-facing control plane without exposing SDK clients."""
    clients = create_google_cloud_clients(settings)
    backend = (
        CloudRunJobsBackend(
            clients.jobs,
            clients.executions,
            project=settings.project,
            region=settings.region,
            job_name=settings.job_name,
        )
        if settings.execution_platform == "cloud-run"
        else GKEJobsBackend(
            cast(KubernetesBatchClient, clients.kubernetes_batch),
            image=settings.image,
            namespace=settings.gke_namespace,
            service_account=settings.gke_ksa_name,
            cpu=settings.gke_cpu,
            memory=settings.gke_memory,
            task_timeout_seconds=settings.task_timeout_seconds,
            job_ttl_seconds=settings.gke_job_ttl_seconds,
            environment={
                "GOOGLE_CLOUD_PROJECT": settings.project,
                "CLAUDE_CODE_USE_VERTEX": "1",
                "ANTHROPIC_VERTEX_PROJECT_ID": settings.project,
                "CLOUD_ML_REGION": settings.vertex_region,
                "CLAUDE_MODEL": settings.claude_model,
                "LOG_LEVEL": settings.log_level,
                "FIRESTORE_DATABASE": settings.firestore_database,
                "WORKSPACE_BUCKET": settings.bucket_name,
                "RUN_RETENTION_DAYS": str(settings.retention_days),
                "QUESTION_TIMEOUT_SECONDS": str(settings.question_timeout_seconds),
            },
        )
        if settings.execution_platform == "gke"
        else CloudBatchBackend(
            cast(BatchClient, clients.batch),
            project=settings.project,
            region=settings.region,
            job_id_prefix=settings.batch_job_id_prefix,
            image=settings.image,
            machine_type=settings.batch_machine_type,
            cpu_milli=settings.batch_cpu_milli,
            memory_mib=settings.batch_memory_mib,
            task_timeout_seconds=settings.task_timeout_seconds,
            service_account=(
                settings.job_service_account
                or f"claude-job@{settings.project}.iam.gserviceaccount.com"
            ),
            environment={
                "GOOGLE_CLOUD_PROJECT": settings.project,
                "CLAUDE_CODE_USE_VERTEX": "1",
                "ANTHROPIC_VERTEX_PROJECT_ID": settings.project,
                "CLOUD_ML_REGION": settings.vertex_region,
                "CLAUDE_MODEL": settings.claude_model,
                "LOG_LEVEL": settings.log_level,
                "FIRESTORE_DATABASE": settings.firestore_database,
                "WORKSPACE_BUCKET": settings.bucket_name,
                "RUN_RETENTION_DAYS": str(settings.retention_days),
                "QUESTION_TIMEOUT_SECONDS": str(settings.question_timeout_seconds),
            },
        )
    )
    return ControlClient(
        # Firestore is the control-plane source of truth for the same retention
        # value exposed to the job deployment.
        FirestoreChatStore(clients.firestore, retention_days=settings.retention_days),
        backend,
    )
