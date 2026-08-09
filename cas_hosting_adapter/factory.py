"""Provider-neutral application assembly and Google Cloud client construction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, cast

from .batch_backend import BatchClient, CloudBatchBackend, GoogleCloudBatchClient
from .cloud_run_backend import CloudRunJobsBackend
from .control_client import ControlClient
from .firestore_chat_store import FirestoreChatStore
from .firestore_codec import DEFAULT_RETENTION_DAYS
from .protocols import AgentFactory, ChatStore, Clock, ExecutionBackend, WorkspaceStore


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
    execution_platform: Literal["cloud-run", "cloud-batch"] = "cloud-run"
    image: str = ""
    job_service_account: str | None = None
    batch_job_id_prefix: str = "claude-agent"
    batch_machine_type: str = "e2-standard-2"
    batch_cpu_milli: int = 2000
    batch_memory_mib: int = 4096
    task_timeout_seconds: int = 1800
    vertex_region: str = "us-east5"
    claude_model: str = "claude-haiku-4-5@20251001"
    log_level: str = "INFO"

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
                else self.batch_job_id_prefix,
            )
        ):
            raise ValueError("Google Cloud settings must not be blank")
        expected = f"projects/{self.project}/locations/{self.region}/jobs/"
        if self.job_name.startswith("projects/") and not self.job_name.startswith(expected):
            raise ValueError("Cloud Run Job resource name must match project and region")
        if self.execution_platform not in {"cloud-run", "cloud-batch"}:
            raise ValueError("execution_platform must be cloud-run or cloud-batch")
        if self.batch_cpu_milli < 1 or self.batch_memory_mib < 1:
            raise ValueError("Batch CPU and memory must be positive")
        if not 1 <= self.task_timeout_seconds <= 86400:
            raise ValueError("task_timeout_seconds must be between 1 and 86400")


@dataclass(frozen=True)
class GoogleCloudClients:
    """SDK clients confined to the Google Cloud composition boundary."""

    firestore: object
    storage: object
    jobs: object | None = None
    executions: object | None = None
    batch: object | None = None


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
            },
        )
    )
    return ControlClient(
        # Firestore is the control-plane source of truth for the same retention
        # value exposed to the job deployment.
        FirestoreChatStore(clients.firestore, retention_days=settings.retention_days),
        backend,
    )
