"""Validated, secret-free release configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReleaseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["2"] = "2"
    project_id: str
    region: str
    firestore_location: str
    firestore_database: str = Field(pattern=r"^[a-z][a-z0-9-]{2,62}$")
    bucket_name: str
    image: str
    job_name: str = "test-claudesdk-cloudrun"
    task_timeout_seconds: int = Field(default=1800, ge=1, le=86400)
    task_retries: int = Field(default=0, ge=0, le=0)
    retention_days: int = Field(default=30, ge=1)
    log_level: str = "INFO"
    vertex_region: str = "us-east5"
    claude_model: str = "claude-haiku-4-5@20251001"

    @model_validator(mode="before")
    @classmethod
    def reject_legacy_retention_fields(cls, value: Any) -> Any:
        if isinstance(value, dict):
            legacy = sorted(
                {
                    key
                    for key in (
                        "run_retention_days",
                        "snapshot_retention_days",
                        "uncommitted_retention_days",
                    )
                    if key in value
                }
            )
            if legacy:
                fields = ", ".join(legacy)
                raise ValueError(
                    f"legacy retention fields ({fields}) are unsupported; "
                    "replace them with retention_days"
                )
        return value

    @model_validator(mode="after")
    def validate_locations(self) -> ReleaseConfig:
        if self.region != self.firestore_location:
            raise ValueError("region and firestore_location must match")
        if self.firestore_database == "(default)":
            raise ValueError("firestore_database must be a named database, not (default)")
        if not self.vertex_region:
            raise ValueError("vertex_region is required")
        if not self.claude_model.startswith("claude-"):
            raise ValueError("claude_model must be a Claude Vertex AI model ID")
        forbidden = ("api_key", "password", "secret")
        if any(token in self.model_dump_json().lower() for token in forbidden):
            raise ValueError("release configuration must not contain secrets")
        return self

    def terraform_variables(self) -> dict[str, object]:
        return {
            "project_id": self.project_id,
            "region": self.region,
            "firestore_database": self.firestore_database,
            "bucket_name": self.bucket_name,
            "image": self.image,
            "job_name": self.job_name,
            "task_timeout_seconds": self.task_timeout_seconds,
            "retention_days": self.retention_days,
            "vertex_region": self.vertex_region,
            "claude_model": self.claude_model,
            "log_level": self.log_level,
        }


def load_release_config(path: Path) -> ReleaseConfig:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("release configuration must be a mapping")
    return ReleaseConfig.model_validate(data)
