"""Persistence models for the Tamil Nadu-first FRA completion workflows."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models import GEOJSON_MULTIPOLYGON, UUID_PK, utcnow


class FRAImportBatch(Base):
    __tablename__ = "fra_import_batches"
    __table_args__ = (
        UniqueConstraint("created_by", "idempotency_key", name="uq_fra_batch_idempotency"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    source_label: Mapped[str] = mapped_column(String(255), nullable=False)
    state_code: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    record_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    provenance_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_summary_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    creator: Mapped["User"] = relationship(foreign_keys=[created_by])
    records: Mapped[list["FRAArchiveRecord"]] = relationship(back_populates="batch")


class ModelVersion(Base):
    __tablename__ = "model_versions"
    __table_args__ = (
        UniqueConstraint("task", "name", "version", name="uq_model_task_name_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    task: Mapped[str] = mapped_column(String(64), nullable=False)
    adapter_type: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    framework: Mapped[str | None] = mapped_column(String(100))
    artifact_uri: Mapped[str | None] = mapped_column(String(500))
    checksum: Mapped[str | None] = mapped_column(String(128))
    label_map_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    metrics_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    configuration_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="registered", nullable=False)
    registered_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    registrar: Mapped["User"] = relationship(foreign_keys=[registered_by])
    inference_runs: Mapped[list["InferenceRun"]] = relationship(back_populates="model_version")


class FRAVillageProfile(Base):
    __tablename__ = "fra_village_profiles"
    __table_args__ = (
        UniqueConstraint(
            "state_code",
            "district_code",
            "block_code",
            "village_code",
            name="uq_fra_village_natural_key",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    state_code: Mapped[str] = mapped_column(String(8), nullable=False)
    state_name: Mapped[str] = mapped_column(String(100), nullable=False)
    district_code: Mapped[str] = mapped_column(String(64), nullable=False)
    district_name: Mapped[str] = mapped_column(String(255), nullable=False)
    block_code: Mapped[str] = mapped_column(String(64), nullable=False)
    block_name: Mapped[str] = mapped_column(String(255), nullable=False)
    village_code: Mapped[str] = mapped_column(String(64), nullable=False)
    village_name: Mapped[str] = mapped_column(String(255), nullable=False)
    boundary: Mapped[Any] = mapped_column(GEOJSON_MULTIPOLYGON, nullable=False)
    tribal_groups_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    socioeconomic_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    provenance_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    reference_version: Mapped[str] = mapped_column(String(100), nullable=False)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    assets: Mapped[list["AssetFeature"]] = relationship(back_populates="village")


class FRAArchiveRecord(Base):
    __tablename__ = "fra_archive_records"
    __table_args__ = (
        UniqueConstraint("batch_id", "legacy_reference", name="uq_fra_archive_batch_legacy"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fra_import_batches.id"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), nullable=False)
    promoted_claim_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fra_claims.id"))
    legacy_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    state_code: Mapped[str] = mapped_column(String(8), nullable=False)
    claim_number: Mapped[str | None] = mapped_column(String(100))
    holder_display_name: Mapped[str | None] = mapped_column(String(255))
    district: Mapped[str | None] = mapped_column(String(255))
    block: Mapped[str | None] = mapped_column(String(255))
    village: Mapped[str | None] = mapped_column(String(255))
    right_type: Mapped[str | None] = mapped_column(String(16))
    claim_status: Mapped[str | None] = mapped_column(String(32))
    claim_year: Mapped[int | None] = mapped_column(Integer)
    review_state: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    reviewed_fields_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    duplicate_fingerprint: Mapped[str | None] = mapped_column(String(128))
    provenance_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    batch: Mapped[FRAImportBatch] = relationship(back_populates="records")
    document: Mapped["Document"] = relationship(foreign_keys=[document_id])
    promoted_claim: Mapped["FRAClaim | None"] = relationship(foreign_keys=[promoted_claim_id])
    reviewer: Mapped["User | None"] = relationship(foreign_keys=[reviewed_by])
    extraction_runs: Mapped[list["FRAExtractionRun"]] = relationship(
        back_populates="archive_record", order_by="FRAExtractionRun.created_at"
    )

    @property
    def latest_extraction(self) -> "FRAExtractionRun | None":
        return self.extraction_runs[-1] if self.extraction_runs else None


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (
        UniqueConstraint(
            "task_type", "entity_id", "idempotency_key", name="uq_processing_job_idempotency"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID_PK, nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    requested_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String(255))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    requester: Mapped["User"] = relationship(foreign_keys=[requested_by])
    inference_runs: Mapped[list["InferenceRun"]] = relationship(back_populates="processing_job")


class FRAExtractionRun(Base):
    __tablename__ = "fra_extraction_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    archive_record_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fra_archive_records.id"), nullable=False
    )
    ocr_model_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model_versions.id"))
    entity_model_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("model_versions.id")
    )
    ocr_model_version: Mapped[str | None] = mapped_column(String(100))
    entity_model_version: Mapped[str | None] = mapped_column(String(100))
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    standardized_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    field_evidence_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    provenance_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    overall_confidence: Mapped[Decimal | None] = mapped_column(Numeric(8, 5))
    processing_time_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    archive_record: Mapped[FRAArchiveRecord] = relationship(back_populates="extraction_runs")
    ocr_model: Mapped[ModelVersion | None] = relationship(foreign_keys=[ocr_model_version_id])
    entity_model: Mapped[ModelVersion | None] = relationship(
        foreign_keys=[entity_model_version_id]
    )


class InferenceRun(Base):
    __tablename__ = "inference_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("model_versions.id"), nullable=False
    )
    processing_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("processing_jobs.id"))
    input_entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    input_entity_id: Mapped[uuid.UUID] = mapped_column(UUID_PK, nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="running", nullable=False)
    input_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    output_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(8, 5))
    processing_time_ms: Mapped[int | None] = mapped_column(Integer)
    provenance_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    model_version: Mapped[ModelVersion] = relationship(back_populates="inference_runs")
    processing_job: Mapped[ProcessingJob | None] = relationship(back_populates="inference_runs")
    assets: Mapped[list["AssetFeature"]] = relationship(back_populates="inference_run")


class AssetFeature(Base):
    __tablename__ = "asset_features"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    village_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fra_village_profiles.id"))
    claim_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fra_claims.id"))
    asset_class: Mapped[str] = mapped_column(String(64), nullable=False)
    polygon_geometry: Mapped[Any | None] = mapped_column(GEOJSON_MULTIPOLYGON)
    point_geometry_json: Mapped[dict | None] = mapped_column(JSON)
    observed_value_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    acquired_at: Mapped[date | None] = mapped_column(Date)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(8, 5))
    inference_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("inference_runs.id"))
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_reference: Mapped[str | None] = mapped_column(String(500))
    provenance_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    verification_state: Mapped[str] = mapped_column(
        String(32), default="unverified", nullable=False
    )
    verification_reasons_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    verified_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("asset_features.id"))
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    village: Mapped[FRAVillageProfile | None] = relationship(back_populates="assets")
    claim: Mapped["FRAClaim | None"] = relationship(foreign_keys=[claim_id])
    inference_run: Mapped[InferenceRun | None] = relationship(back_populates="assets")
    verifier: Mapped["User | None"] = relationship(foreign_keys=[verified_by])
    supersedes: Mapped["AssetFeature | None"] = relationship(remote_side=[id])


class DSSReferral(Base):
    __tablename__ = "dss_referrals"
    __table_args__ = (
        UniqueConstraint("recommendation_id", name="uq_dss_referral_recommendation"),
        UniqueConstraint("created_by", "idempotency_key", name="uq_dss_referral_idempotency"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    recommendation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dss_recommendations.id"), nullable=False
    )
    department: Mapped[str] = mapped_column(String(255), nullable=False)
    priority: Mapped[str] = mapped_column(String(32), default="normal", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    assigned_to: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    history_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    advisory_only: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    recommendation: Mapped["DSSRecommendation"] = relationship()
    creator: Mapped["User"] = relationship(foreign_keys=[created_by])


class ReportArtifact(Base):
    __tablename__ = "report_artifacts"
    __table_args__ = (
        UniqueConstraint("created_by", "idempotency_key", name="uq_report_idempotency"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    report_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID_PK, nullable=False)
    parameters_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    storage_key: Mapped[str | None] = mapped_column(String(500))
    generation_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("processing_jobs.id"))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    generation_job: Mapped[ProcessingJob | None] = relationship()
    creator: Mapped["User"] = relationship(foreign_keys=[created_by])


# Imported only for SQLAlchemy's string-based relationship resolution.
from app.db.fra_models import DSSRecommendation, FRAClaim  # noqa: E402,F401
from app.db.models import Document, User  # noqa: E402,F401
