"""Operational persistence for connected Tamil Nadu FRA workflows."""

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


class FRAIntakeItem(Base):
    __tablename__ = "fra_intake_items"
    __table_args__ = (
        UniqueConstraint("legacy_claim_id", name="uq_fra_intake_legacy_claim"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    legacy_claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id"), nullable=False)
    promoted_claim_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fra_claims.id"), unique=True
    )
    state: Mapped[str] = mapped_column(String(32), default="awaiting_triage", nullable=False)
    triage_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    reasons_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    legacy_claim: Mapped["Claim"] = relationship(foreign_keys=[legacy_claim_id])
    promoted_claim: Mapped["FRAClaim | None"] = relationship(foreign_keys=[promoted_claim_id])
    creator: Mapped["User"] = relationship(foreign_keys=[created_by])
    updater: Mapped["User | None"] = relationship(foreign_keys=[updated_by])


class SpatialImportBatch(Base):
    __tablename__ = "spatial_import_batches"
    __table_args__ = (
        UniqueConstraint("created_by", "idempotency_key", name="uq_spatial_import_idempotency"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    dataset_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    source_authority: Mapped[str] = mapped_column(String(255), nullable=False)
    source_version: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="staged", nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(255))
    storage_key: Mapped[str | None] = mapped_column(String(500))
    declared_crs: Mapped[str | None] = mapped_column(String(100))
    detected_crs: Mapped[str | None] = mapped_column(String(100))
    record_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    valid_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    invalid_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    repaired_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    provenance_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_summary_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    creator: Mapped["User"] = relationship(foreign_keys=[created_by])
    reviewer: Mapped["User | None"] = relationship(foreign_keys=[reviewed_by])
    features: Mapped[list["SpatialReferenceFeature"]] = relationship(
        back_populates="import_batch"
    )


class SpatialReferenceFeature(Base):
    __tablename__ = "spatial_reference_features"
    __table_args__ = (
        UniqueConstraint(
            "source_authority", "source_version", "source_record_id",
            name="uq_spatial_source_record",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    import_batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("spatial_import_batches.id"), nullable=False
    )
    dataset_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    source_authority: Mapped[str] = mapped_column(String(255), nullable=False)
    source_version: Mapped[str] = mapped_column(String(100), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(255), nullable=False)
    geometry: Mapped[Any] = mapped_column(GEOJSON_MULTIPOLYGON, nullable=False)
    properties_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    provenance_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    import_batch: Mapped[SpatialImportBatch] = relationship(back_populates="features")


class ImagerySceneRecord(Base):
    __tablename__ = "imagery_scenes"
    __table_args__ = (
        UniqueConstraint("provider", "collection", "scene_id", name="uq_imagery_scene"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    collection: Mapped[str] = mapped_column(String(100), nullable=False)
    scene_id: Mapped[str] = mapped_column(String(255), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    footprint: Mapped[Any] = mapped_column(GEOJSON_MULTIPOLYGON, nullable=False)
    cloud_cover: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    asset_references_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    license_reference: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(32), default="discovered", nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    provenance_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ImageryArtifact(Base):
    __tablename__ = "imagery_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "claim_id", "geometry_version_id", "artifact_type", "processor_version",
            name="uq_imagery_claim_geometry_artifact_processor",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fra_claims.id"), nullable=False)
    geometry_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fra_geometry_versions.id"), nullable=False
    )
    imagery_scene_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("imagery_scenes.id"))
    processing_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("processing_jobs.id"))
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_year: Mapped[int | None] = mapped_column(Integer)
    storage_key: Mapped[str | None] = mapped_column(String(500))
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    processor_version: Mapped[str] = mapped_column(String(100), nullable=False)
    model_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model_versions.id"))
    parameters_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    statistics_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    quality_flags_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    provenance_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    verification_state: Mapped[str] = mapped_column(
        String(32), default="unverified", nullable=False
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    claim: Mapped["FRAClaim"] = relationship(foreign_keys=[claim_id])
    geometry_version: Mapped["FRAGeometryVersion"] = relationship(
        foreign_keys=[geometry_version_id]
    )
    imagery_scene: Mapped[ImagerySceneRecord | None] = relationship()
    processing_job: Mapped["ProcessingJob | None"] = relationship()
    model_version: Mapped["ModelVersion | None"] = relationship()
    reviewer: Mapped["User | None"] = relationship(foreign_keys=[reviewed_by])


class DSSFactSnapshot(Base):
    __tablename__ = "dss_fact_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "claim_id", "derivation_version", "idempotency_key",
            name="uq_dss_fact_snapshot",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fra_claims.id"), nullable=False)
    derivation_version: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    facts_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    sources_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    claim: Mapped["FRAClaim"] = relationship()
    creator: Mapped["User"] = relationship(foreign_keys=[created_by])


class SchemeCatalogEntry(Base):
    __tablename__ = "scheme_catalog_entries"
    __table_args__ = (
        UniqueConstraint("scheme_code", "version", name="uq_scheme_catalog_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    scheme_code: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    department: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    approving_authority: Mapped[str | None] = mapped_column(String(255))
    source_reference: Mapped[str] = mapped_column(String(500), nullable=False)
    definition_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    authoritative: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    creator: Mapped["User"] = relationship(foreign_keys=[created_by])


# Imported only for SQLAlchemy's string-based relationship resolution.
from app.db.fra_completion_models import ModelVersion, ProcessingJob  # noqa: E402,F401
from app.db.fra_models import FRAClaim, FRAGeometryVersion  # noqa: E402,F401
from app.db.models import Claim, User  # noqa: E402,F401
