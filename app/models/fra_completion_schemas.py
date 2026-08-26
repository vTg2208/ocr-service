"""Strict API contracts for the completed FRA workflow modules."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FRAImportBatchCreate(StrictModel):
    source_label: str = Field(min_length=1, max_length=255)
    state: str = Field(default="Tamil Nadu", min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=255)
    synthetic: bool
    provenance: dict[str, Any] = Field(default_factory=dict)


class FRAArchiveRecordCreate(StrictModel):
    batch_id: UUID
    document_id: UUID
    legacy_reference: str = Field(min_length=1, max_length=255)
    synthetic: bool | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    extraction_manifest: dict[str, Any] | None = None
    idempotency_key: str | None = Field(default=None, max_length=255)


class FRAArchiveReview(StrictModel):
    expected_revision: int = Field(ge=0)
    reviewed_fields: dict[str, Any]


class ModelVersionCreate(StrictModel):
    task: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=100)
    adapter_type: str = Field(min_length=1, max_length=64)
    framework: str | None = Field(default=None, max_length=100)
    artifact_uri: str | None = Field(default=None, max_length=500)
    checksum: str | None = Field(default=None, max_length=128)
    label_map: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=lambda: {"status": "not_evaluated"})
    configuration: dict[str, Any] = Field(default_factory=dict)


JobState = Literal["queued", "running", "completed", "failed", "quarantined"]
