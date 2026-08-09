"""Validated, secret-free release configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, model_validator


class CloudRunReleaseSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_name: str = Field(default="test-claudesdk-cloudrun", min_length=1)


class CloudBatchReleaseSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id_prefix: str = Field(default="claude-agent", pattern=r"^[a-z][a-z0-9-]{0,24}$")
    machine_type: str = Field(default="e2-standard-2", min_length=1)
    cpu_milli: int = Field(default=2000, ge=1)
    memory_mib: int = Field(default=4096, ge=1)


class ReleaseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["2", "3"] = "3"
    project_id: str
    region: str
    firestore_location: str
    firestore_database: str = Field(pattern=r"^[a-z][a-z0-9-]{2,62}$")
    bucket_name: str
    image: str
    execution_platform: Literal["cloud-run", "cloud-batch", "gke"] = "cloud-run"
    enable_cloud_run: bool = True
    enable_cloud_batch: bool = True
    cloud_run: CloudRunReleaseSettings = Field(default_factory=CloudRunReleaseSettings)
    cloud_batch: CloudBatchReleaseSettings = Field(default_factory=CloudBatchReleaseSettings)
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
            value = dict(value)
            schema_version = value.get("schema_version", "3")
            if schema_version not in {"2", "3"}:
                raise ValueError(f"unsupported release schema_version: {schema_version}")
            if schema_version == "2" and "execution_platform" not in value:
                raise ValueError(
                    "schema_version 2 requires migration: add execution_platform: cloud-run "
                    "and update schema_version to 3 before deployment"
                )
            # Keep the flat v2 Cloud Run key readable while making the new
            # platform-specific settings explicit in the validated model.
            if "job_name" in value:
                cloud_run = dict(value.get("cloud_run") or {})
                cloud_run["job_name"] = value.pop("job_name")
                value["cloud_run"] = cloud_run
            if "batch" in value and "cloud_batch" not in value:
                value["cloud_batch"] = value.pop("batch")
            platform = value.get("execution_platform", "cloud-run")
            if platform == "cloud-run" and "cloud_batch" in value:
                raise ValueError("cloud-run configuration must not contain cloud_batch settings")
            if platform == "cloud-batch" and (
                "cloud_run" in value or "job_name" in value
            ):
                raise ValueError("cloud-batch configuration must not contain Cloud Run settings")
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
        if self.execution_platform == "gke":
            raise ValueError("execution_platform gke is reserved but not implemented")
        if not self.enable_cloud_run and not self.enable_cloud_batch:
            raise ValueError("at least one of enable_cloud_run or enable_cloud_batch must be true")
        if self.execution_platform == "cloud-run" and not self.enable_cloud_run:
            raise ValueError("execution_platform cloud-run requires enable_cloud_run=true")
        if self.execution_platform == "cloud-batch" and not self.enable_cloud_batch:
            raise ValueError("execution_platform cloud-batch requires enable_cloud_batch=true")
        forbidden = ("api_key", "password", "secret")
        if any(token in self.model_dump_json().lower() for token in forbidden):
            raise ValueError("release configuration must not contain secrets")
        return self

    def terraform_variables(self) -> dict[str, object]:
        values: dict[str, object] = {
            "project_id": self.project_id,
            "region": self.region,
            "firestore_database": self.firestore_database,
            "bucket_name": self.bucket_name,
            "image": self.image,
            "execution_platform": self.execution_platform,
            "enable_cloud_run": self.enable_cloud_run,
            "enable_cloud_batch": self.enable_cloud_batch,
            "task_timeout_seconds": self.task_timeout_seconds,
            "retention_days": self.retention_days,
            "vertex_region": self.vertex_region,
            "claude_model": self.claude_model,
            "log_level": self.log_level,
        }
        if self.execution_platform == "cloud-run":
            values["job_name"] = self.cloud_run.job_name
        elif self.execution_platform == "cloud-batch":
            values.update(
                {
                    "batch_job_id_prefix": self.cloud_batch.job_id_prefix,
                    "batch_machine_type": self.cloud_batch.machine_type,
                    "batch_cpu_milli": self.cloud_batch.cpu_milli,
                    "batch_memory_mib": self.cloud_batch.memory_mib,
                }
            )
        return values

    @property
    def job_name(self) -> str:
        """Compatibility accessor for Cloud Run callers."""
        return self.cloud_run.job_name


def load_release_config(path: Path) -> ReleaseConfig:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("release configuration must be a mapping")
    return ReleaseConfig.model_validate(data)
