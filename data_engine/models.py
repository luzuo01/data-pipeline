from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class SourceType(str, Enum):
    upload = "upload"
    huggingface = "huggingface"


class ImportFormat(str, Enum):
    auto = "auto"
    jsonl = "jsonl"
    csv = "csv"
    parquet = "parquet"
    zip = "zip"
    hdf5 = "hdf5"
    lerobot = "lerobot"


class ImportRequest(BaseModel):
    source_type: SourceType = SourceType.upload
    source_uri: str | None = Field(None, description="HF dataset id or uploaded object id")
    format: ImportFormat = ImportFormat.auto
    dataset_name: str = Field(..., min_length=1, max_length=128)
    description: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MappingConfig(BaseModel):
    """Maps source records into canonical observation/action/time fields."""

    observation: str | list[str] = Field(..., description="JSON path(s) for policy state")
    action: str | list[str] = Field(..., description="JSON path(s) for policy action")
    timestamp: str | None = None
    task: str | None = None
    episode: str | None = None
    fps: float | None = Field(None, gt=0)
    source_frame_index: str | None = None
    include_fields: list[str] = Field(default_factory=list)

    @field_validator("observation", "action")
    @classmethod
    def non_empty_paths(cls, value: str | list[str]) -> str | list[str]:
        if isinstance(value, list) and not value:
            raise ValueError("at least one source path is required")
        return value


class ConversionRequest(BaseModel):
    import_id: str
    output_repo_id: str = Field(..., min_length=1, max_length=128)
    mapping: MappingConfig
    target_version: Literal["lerobot_v3"] = "lerobot_v3"
    overwrite: bool = False
    preserve_source: bool = True
    options: dict[str, Any] = Field(default_factory=dict)


class JobState(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class JobResponse(BaseModel):
    job_id: str
    state: JobState
    progress: int = Field(ge=0, le=100)
    stage: str
    created_at: datetime
    updated_at: datetime
    output_uri: str | None = None
    manifest_uri: str | None = None
    error: dict[str, Any] | None = None
    validation: dict[str, Any] | None = None


class Hdf5ConversionRequest(BaseModel):
    import_id: str
    output_repo_id: str = Field(..., min_length=1, max_length=128)
    task: str | None = None
    preserve_source: bool = True
    overwrite: bool = False


class ZipConversionRequest(BaseModel):
    """Convert every recognized source group inside one uploaded ZIP."""

    import_id: str
    output_repo_id: str = Field(..., min_length=1, max_length=128)
    task: str | None = None
    preserve_source: bool = True
    overwrite: bool = False


class LerobotValidationRequest(BaseModel):
    path: str = Field(..., min_length=1)
