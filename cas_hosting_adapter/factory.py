"""Provider-neutral application assembly and Google Cloud client construction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

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
    job_name: str
    retention_days: int = DEFAULT_RETENTION_DAYS

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
                self.job_name,
            )
        ):
            raise ValueError("Google Cloud settings must not be blank")
        expected = f"projects/{self.project}/locations/{self.region}/jobs/"
        if self.job_name.startswith("projects/") and not self.job_name.startswith(expected):
            raise ValueError("Cloud Run Job resource name must match project and region")


@dataclass(frozen=True)
class GoogleCloudClients:
    """SDK clients confined to the Google Cloud composition boundary."""

    firestore: object
    storage: object
    jobs: object
    executions: object


def create_google_cloud_clients(settings: GoogleCloudSettings) -> GoogleCloudClients:
    from google.cloud.firestore import Client as FirestoreClient
    from google.cloud.run_v2 import ExecutionsClient, JobsClient
    from google.cloud.storage import Client as StorageClient  # type: ignore[import-untyped]

    return GoogleCloudClients(
        firestore=FirestoreClient(project=settings.project, database=settings.firestore_database),
        storage=StorageClient(project=settings.project),
        jobs=JobsClient(),
        executions=ExecutionsClient(),
    )


def create_google_cloud_control_client(settings: GoogleCloudSettings) -> ControlClient:
    """Build the Streamlit-facing control plane without exposing SDK clients."""
    clients = create_google_cloud_clients(settings)
    return ControlClient(
        # Firestore is the control-plane source of truth for the same retention
        # value exposed to the job deployment.
        FirestoreChatStore(clients.firestore, retention_days=settings.retention_days),
        CloudRunJobsBackend(
            clients.jobs,
            clients.executions,
            project=settings.project,
            region=settings.region,
            job_name=settings.job_name,
        ),
    )
