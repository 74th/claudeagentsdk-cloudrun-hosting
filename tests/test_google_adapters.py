from types import SimpleNamespace

import pytest

from cas_hosting_adapter.errors import OperationError, WorkspaceError
from cas_hosting_adapter.google_adapters import (
    AgentPlatformOperations,
    GoogleCloudSnapshotStore,
)


class FakeBlob:
    def __init__(self, data: bytes = b"", generation: int | None = 7) -> None:
        self.data = data
        self.generation = generation
        self.upload_calls: list[tuple[bytes, int]] = []
        self.delete_calls: list[int] = []

    def upload_from_string(self, data: bytes, *, if_generation_match: int) -> None:
        self.data = data
        self.upload_calls.append((data, if_generation_match))

    def download_as_bytes(self) -> bytes:
        return self.data

    def delete(self, *, if_generation_match: int) -> None:
        self.delete_calls.append(if_generation_match)


class FakeBucket:
    def __init__(self) -> None:
        self.blobs: dict[tuple[str, int | None], FakeBlob] = {}

    def blob(self, name: str, generation: int | None = None) -> FakeBlob:
        return self.blobs.setdefault((name, generation), FakeBlob())


def test_snapshot_store_forwards_generation_preconditions() -> None:
    bucket = FakeBucket()
    store = GoogleCloudSnapshotStore(bucket)

    assert store.upload("a", b"body", if_generation_match=0) == 7
    assert bucket.blob("a").upload_calls == [(b"body", 0)]
    store.delete("a", 7)
    assert bucket.blob("a", generation=7).delete_calls == [7]


def test_snapshot_store_rejects_missing_generation() -> None:
    bucket = FakeBucket()
    bucket.blob("a").generation = None
    with pytest.raises(WorkspaceError):
        GoogleCloudSnapshotStore(bucket).upload("a", b"body", if_generation_match=0)


class FakeAgentEngines:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def run_query_job(self, *, name: str, config: dict[str, object]) -> SimpleNamespace:
        self.calls.append(("start", name, config))
        return SimpleNamespace(job_name="operations/123")

    def check_query_job(self, *, name: str, config: dict[str, object]) -> SimpleNamespace:
        self.calls.append(("get", name, config))
        return SimpleNamespace(status="RUNNING")

    def cancel_query_job(self, *, name: str, config: dict[str, object]) -> None:
        self.calls.append(("cancel", name, config))


def test_operations_use_engine_name_for_start_and_cancel() -> None:
    api = FakeAgentEngines()
    operations = AgentPlatformOperations(api, "reasoningEngines/engine")

    assert operations.start(input_payload={"query": "{}"}) == "operations/123"
    assert operations.get("operations/123") == "RUNNING"
    operations.cancel("operations/123")

    assert api.calls == [
        ("start", "reasoningEngines/engine", {"query": "{}"}),
        ("get", "operations/123", {"retrieve_result": True}),
        ("cancel", "reasoningEngines/engine", {"operation_name": "operations/123"}),
    ]


def test_operations_reject_missing_operation_name() -> None:
    class MissingName(FakeAgentEngines):
        def run_query_job(self, *, name: str, config: dict[str, object]) -> SimpleNamespace:
            return SimpleNamespace(job_name=None)

    with pytest.raises(OperationError):
        AgentPlatformOperations(MissingName(), "engine").start(input_payload={})


def test_operations_normalize_agent_platform_cancelled_job() -> None:
    class CancelledJob(FakeAgentEngines):
        def check_query_job(self, *, name: str, config: dict[str, object]) -> SimpleNamespace:
            del name, config
            return SimpleNamespace(status="FAILED", result="Cancelled by user.")

    assert AgentPlatformOperations(CancelledJob(), "engine").get("operations/123") == "CANCELLED"
