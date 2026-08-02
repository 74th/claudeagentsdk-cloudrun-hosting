"""Thin Google Cloud adapters isolated from the domain logic."""
from __future__ import annotations

from typing import Any, cast

from .errors import OperationError, WorkspaceError


class GoogleCloudSnapshotStore:
    def __init__(self, bucket: Any) -> None:
        self.bucket = bucket

    def upload(self, object_path: str, data: bytes, *, if_generation_match: int) -> int:
        try:
            blob = self.bucket.blob(object_path)
            blob.upload_from_string(data, if_generation_match=if_generation_match)
            if not isinstance(blob.generation, int):
                raise WorkspaceError("GCS did not return an object generation")
            return blob.generation
        except WorkspaceError:
            raise
        except Exception as error:
            raise WorkspaceError("GCS snapshot upload failed") from error

    def download(self, object_path: str, generation: int) -> bytes:
        try:
            data = self.bucket.blob(object_path, generation=generation).download_as_bytes()
            return cast(bytes, data)
        except Exception as error:
            raise WorkspaceError("GCS snapshot download failed") from error

    def delete(self, object_path: str, generation: int) -> None:
        try:
            self.bucket.blob(object_path, generation=generation).delete(
                if_generation_match=generation
            )
        except Exception as error:
            raise WorkspaceError("GCS snapshot delete failed") from error


class AgentPlatformOperations:
    def __init__(self, agent_engines: Any, agent_engine_name: str) -> None:
        self.agent_engines = agent_engines
        self.agent_engine_name = agent_engine_name

    def start(self, *, input_payload: dict[str, Any]) -> str:
        try:
            result = self.agent_engines.run_query_job(
                name=self.agent_engine_name, config=input_payload
            )
            name = getattr(result, "job_name", None)
            if not isinstance(name, str):
                raise OperationError("Agent Platform did not return an operation name")
            return name
        except OperationError:
            raise
        except Exception as error:
            raise OperationError("Agent Platform async start failed") from error

    def get(self, operation_name: str) -> str:
        try:
            result = self.agent_engines.check_query_job(
                name=operation_name, config={"retrieve_result": True}
            )
            status = getattr(result, "status", None)
            if not isinstance(status, str):
                raise OperationError("Agent Platform did not return an operation status")
            # Agent Platform currently reports a user-cancelled async job as
            # FAILED with a result message rather than as CANCELLED.
            result_message = getattr(result, "result", None)
            if status.upper() == "FAILED" and "cancelled by user" in str(result_message).lower():
                return "CANCELLED"
            return status
        except OperationError:
            raise
        except Exception as error:
            raise OperationError("Agent Platform operation status failed") from error

    def cancel(self, operation_name: str) -> None:
        try:
            self.agent_engines.cancel_query_job(
                name=self.agent_engine_name, config={"operation_name": operation_name}
            )
        except Exception as error:
            raise OperationError("Agent Platform cancellation failed") from error
