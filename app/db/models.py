"""Central parcel, claim, document, identity, and audit data model."""

import uuid
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UserDefinedType
from shapely import wkb
from shapely.geometry import mapping, shape

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


UUID_PK = Uuid(as_uuid=True)


class GeoJSONMultiPolygon(UserDefinedType):
    """GeoJSON in SQLite, a native PostGIS MultiPolygon in PostgreSQL."""

    cache_ok = True

    def get_col_spec(self, **_kwargs):
        return "geometry(MultiPolygon,4326)"

    def bind_processor(self, dialect):
        if dialect.name == "sqlite":
            return lambda value: json.dumps(value) if value is not None else None
        return lambda value: shape(value).wkt if value is not None else None

    def result_processor(self, dialect, _coltype):
        if dialect.name == "sqlite":
            return lambda value: json.loads(value) if isinstance(value, str) else value

        def from_postgis(value):
            if value is None or isinstance(value, dict):
                return value
            raw = bytes.fromhex(value) if isinstance(value, str) else bytes(value)
            return json.loads(json.dumps(mapping(wkb.loads(raw))))

        return from_postgis


@compiles(GeoJSONMultiPolygon, "postgresql")
def compile_postgis_multipolygon(_type, _compiler, **_kwargs):
    return "geometry(MultiPolygon,4326)"


@compiles(GeoJSONMultiPolygon, "sqlite")
def compile_sqlite_geojson(_type, _compiler, **_kwargs):
    return "JSON"


GEOJSON_MULTIPOLYGON = GeoJSONMultiPolygon()


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    external_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="user", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Parcel(Base):
    __tablename__ = "parcels"
    __table_args__ = (
        UniqueConstraint(
            "state", "district", "taluk", "village", "survey_number",
            "subdivision_number", name="uq_parcel_composite_key",
        ),
        Index(
            "ix_parcel_lookup", "state", "district", "taluk", "village",
            "survey_number", "subdivision_number",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    state: Mapped[str] = mapped_column(String(255), nullable=False)
    district: Mapped[str] = mapped_column(String(255), nullable=False)
    taluk: Mapped[str] = mapped_column(String(255), nullable=False)
    village: Mapped[str] = mapped_column(String(255), nullable=False)
    survey_number: Mapped[str] = mapped_column(String(64), nullable=False)
    subdivision_number: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    official_area_sqm: Mapped[Decimal | None] = mapped_column(Numeric(16, 4))
    geometry: Mapped[Any] = mapped_column(GEOJSON_MULTIPOLYGON, nullable=False)
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    source_version: Mapped[str | None] = mapped_column(String(100))
    source_record_id: Mapped[str | None] = mapped_column(String(255))
    boundary_quality: Mapped[str] = mapped_column(String(50), default="unknown")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AdministrativeAlias(Base):
    __tablename__ = "administrative_aliases"
    __table_args__ = (
        UniqueConstraint("level", "normalized_alias", name="uq_admin_alias_level"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    level: Mapped[str] = mapped_column(String(32), nullable=False)
    alias: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    language: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("uploaded_by", "idempotency_key", name="uq_document_idempotency"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    ocr_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    uploader: Mapped[User] = relationship()


class OCRResult(Base):
    __tablename__ = "ocr_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id"), unique=True, nullable=False
    )
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    overall_confidence: Mapped[Decimal | None] = mapped_column(Numeric(8, 5))
    structured_result_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Claim(Base):
    __tablename__ = "claims"
    __table_args__ = (
        UniqueConstraint("claimant_id", "idempotency_key", name="uq_claim_idempotency"),
        UniqueConstraint("parcel_id", name="uq_claim_parcel_exclusive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    claimant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    parcel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("parcels.id"), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), nullable=False)
    claimed_area_sqm: Mapped[Decimal | None] = mapped_column(Numeric(16, 4))
    confirmed_fields_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    match_confidence: Mapped[Decimal | None] = mapped_column(Numeric(8, 5))
    match_method: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_notes: Mapped[str | None] = mapped_column(Text)

    parcel: Mapped[Parcel] = relationship()
    document: Mapped[Document] = relationship()
    claimant: Mapped[User] = relationship(foreign_keys=[claimant_id])


class ClaimConflict(Base):
    __tablename__ = "claim_conflicts"
    __table_args__ = (
        UniqueConstraint(
            "claim_a_id", "claim_b_id", "conflict_type", name="uq_conflict_pair_type"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    claim_a_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id"), nullable=False)
    claim_b_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id"), nullable=False)
    conflict_type: Mapped[str] = mapped_column(String(32), nullable=False)
    overlap_area_sqm: Mapped[Decimal | None] = mapped_column(Numeric(16, 4))
    overlap_percent: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    status: Mapped[str] = mapped_column(String(32), default="open", nullable=False)
    resolution_notes: Mapped[str | None] = mapped_column(Text)
    resolution_history_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    claim_a: Mapped[Claim] = relationship(foreign_keys=[claim_a_id])
    claim_b: Mapped[Claim] = relationship(foreign_keys=[claim_b_id])


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID_PK, nullable=False)
    before_json: Mapped[dict | None] = mapped_column(JSON)
    after_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    request_id: Mapped[str | None] = mapped_column(String(100))


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    notification_type: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID_PK, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
