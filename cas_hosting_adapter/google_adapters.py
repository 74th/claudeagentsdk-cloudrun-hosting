"""Thin Google Cloud adapters isolated from the domain logic."""
from __future__ import annotations

import hashlib
from typing import Any, cast

from .errors import WorkspaceError
from .models import WorkspaceReference


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


class GCSWorkspaceStore:
    """Immutable WorkspaceStore adapter using GCS generation preconditions."""

    def __init__(self, bucket: Any) -> None:
        self._bucket = bucket

    def create(self, object_key: str, data: bytes) -> WorkspaceReference:
        try:
            blob = self._bucket.blob(object_key)
            blob.upload_from_string(data, if_generation_match=0)
            blob.reload()
            if blob.generation is None:
                raise WorkspaceError("GCS did not return an object generation")
            return WorkspaceReference(
                object_key=object_key,
                version=str(blob.generation),
                sha256=hashlib.sha256(data).hexdigest(),
                size=len(data),
            )
        except WorkspaceError:
            raise
        except Exception as error:
            raise WorkspaceError("GCS conditional snapshot create failed") from error

    def get(self, reference: WorkspaceReference) -> bytes:
        try:
            data = cast(
                bytes,
                self._bucket.blob(
                    reference.object_key, generation=int(reference.version)
                ).download_as_bytes(),
            )
            if hashlib.sha256(data).hexdigest() != reference.sha256:
                raise WorkspaceError("GCS snapshot hash mismatch")
            return data
        except WorkspaceError:
            raise
        except Exception as error:
            raise WorkspaceError("GCS snapshot download failed") from error

    def delete(self, reference: WorkspaceReference) -> None:
        try:
            generation = int(reference.version)
            self._bucket.blob(reference.object_key, generation=generation).delete(
                if_generation_match=generation
            )
        except Exception as error:
            raise WorkspaceError("GCS snapshot delete failed") from error
