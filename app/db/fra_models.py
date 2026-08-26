"""Forest Rights Act domain, supporting evidence, and DSS persistence models."""

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
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models import Claim, Document, GEOJSON_MULTIPOLYGON, Parcel, UUID_PK, User, utcnow


class GramSabha(Base):
    __tablename__ = "gram_sabhas"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    village: Mapped[str] = mapped_column(String(255), nullable=False)
    gram_panchayat: Mapped[str | None] = mapped_column(String(255))
    block: Mapped[str | None] = mapped_column(String(255))
    district: Mapped[str | None] = mapped_column(String(255))
    state: Mapped[str | None] = mapped_column(String(255))
    external_reference: Mapped[str | None] = mapped_column(String(255), unique=True)
    boundary: Mapped[Any | None] = mapped_column(GEOJSON_MULTIPOLYGON)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    rights_holders: Mapped[list["RightsHolder"]] = relationship(back_populates="gram_sabha")
    claims: Mapped[list["FRAClaim"]] = relationship(back_populates="gram_sabha")


class RightsHolder(Base):
    __tablename__ = "rights_holders"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    holder_type: Mapped[str] = mapped_column(String(32), nullable=False)
    claimant_category: Mapped[str | None] = mapped_column(String(32))
    external_reference: Mapped[str | None] = mapped_column(String(255), unique=True)
    gram_sabha_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("gram_sabhas.id"))
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    gram_sabha: Mapped[GramSabha | None] = relationship(back_populates="rights_holders")
    claims: Mapped[list["FRAClaim"]] = relationship(back_populates="rights_holder")


class FRAClaim(Base):
    __tablename__ = "fra_claims"
    __table_args__ = (
        UniqueConstraint("claim_number", name="uq_fra_claim_number"),
        UniqueConstraint("legacy_claim_id", name="uq_fra_claim_legacy"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    claim_number: Mapped[str] = mapped_column(String(100), nullable=False)
    right_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    rights_holder_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rights_holders.id"), nullable=False
    )
    gram_sabha_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("gram_sabhas.id"))
    submitted_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    legacy_claim_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("claims.id"))
    parcel_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("parcels.id"))
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id"))
    claimed_area_sqm: Mapped[Decimal | None] = mapped_column(Numeric(16, 4))
    provenance_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    rights_holder: Mapped[RightsHolder] = relationship(back_populates="claims")
    gram_sabha: Mapped[GramSabha | None] = relationship(back_populates="claims")
    submitter: Mapped[User] = relationship(foreign_keys=[submitted_by])
    legacy_claim: Mapped[Claim | None] = relationship(foreign_keys=[legacy_claim_id])
    parcel: Mapped[Parcel | None] = relationship(foreign_keys=[parcel_id])
    document: Mapped[Document | None] = relationship(foreign_keys=[document_id])
    decisions: Mapped[list["FRADecision"]] = relationship(
        back_populates="claim", order_by="FRADecision.created_at"
    )
    geometry_versions: Mapped[list["FRAGeometryVersion"]] = relationship(
        back_populates="claim", order_by="FRAGeometryVersion.version"
    )
    evidence_items: Mapped[list["FRAEvidenceItem"]] = relationship(back_populates="claim")
    titles: Mapped[list["FRATitle"]] = relationship(
        back_populates="claim", order_by="FRATitle.version"
    )
    satellite_observations: Mapped[list["SatelliteObservation"]] = relationship(
        back_populates="claim"
    )
    dss_recommendations: Mapped[list["DSSRecommendation"]] = relationship(
        back_populates="claim"
    )


class FRADecision(Base):
    __tablename__ = "fra_decisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fra_claims.id"), nullable=False)
    authority_level: Mapped[str] = mapped_column(String(32), nullable=False)
    from_status: Mapped[str] = mapped_column(String(32), nullable=False)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(64), nullable=False)
    reasons_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    claim: Mapped[FRAClaim] = relationship(back_populates="decisions")
    actor: Mapped[User] = relationship(foreign_keys=[actor_id])


class FRAGeometryVersion(Base):
    __tablename__ = "fra_geometry_versions"
    __table_args__ = (
        UniqueConstraint("claim_id", "version", name="uq_fra_geometry_claim_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fra_claims.id"), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    geometry: Mapped[Any] = mapped_column(GEOJSON_MULTIPOLYGON, nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    provenance_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    boundary_quality: Mapped[str] = mapped_column(String(50), default="unknown", nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    claim: Mapped[FRAClaim] = relationship(back_populates="geometry_versions")
    creator: Mapped[User] = relationship(foreign_keys=[created_by])


class SatelliteObservation(Base):
    __tablename__ = "satellite_observations"
    __table_args__ = (
        UniqueConstraint(
            "provider", "scene_id", "claim_id", "asset_class",
            name="uq_satellite_scene_claim_asset",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fra_claims.id"), nullable=False)
    geometry_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fra_geometry_versions.id")
    )
    scene_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    source_uri: Mapped[str] = mapped_column(String(500), nullable=False)
    asset_class: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_value_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(8, 5), nullable=False)
    analyser_version: Mapped[str] = mapped_column(String(100), nullable=False)
    acquired_at: Mapped[date] = mapped_column(Date, nullable=False)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processing_time_ms: Mapped[int | None] = mapped_column()
    provenance_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    claim: Mapped[FRAClaim] = relationship(back_populates="satellite_observations")
    geometry_version: Mapped[FRAGeometryVersion | None] = relationship()
    evidence_item: Mapped["FRAEvidenceItem | None"] = relationship(
        back_populates="satellite_observation", uselist=False
    )


class FRAEvidenceItem(Base):
    __tablename__ = "fra_evidence_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fra_claims.id"), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    legal_role: Mapped[str] = mapped_column(String(32), default="submitted", nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id"))
    satellite_observation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("satellite_observations.id"), unique=True
    )
    provenance_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    captured_at: Mapped[date | None] = mapped_column(Date)
    verification_state: Mapped[str] = mapped_column(
        String(32), default="unverified", nullable=False
    )
    source_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verified_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    claim: Mapped[FRAClaim] = relationship(back_populates="evidence_items")
    document: Mapped[Document | None] = relationship(foreign_keys=[document_id])
    satellite_observation: Mapped[SatelliteObservation | None] = relationship(
        back_populates="evidence_item", foreign_keys=[satellite_observation_id]
    )
    verifier: Mapped[User | None] = relationship(foreign_keys=[verified_by])
    creator: Mapped[User] = relationship(foreign_keys=[created_by])


class FRATitle(Base):
    __tablename__ = "fra_titles"
    __table_args__ = (
        UniqueConstraint("claim_id", "version", name="uq_fra_title_claim_version"),
        UniqueConstraint("title_number", name="uq_fra_title_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fra_claims.id"), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    title_number: Mapped[str] = mapped_column(String(100), nullable=False)
    geometry_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fra_geometry_versions.id")
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    issued_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    claim: Mapped[FRAClaim] = relationship(back_populates="titles")
    geometry_version: Mapped[FRAGeometryVersion | None] = relationship()
    issuer: Mapped[User] = relationship(foreign_keys=[issued_by])


class SchemeRuleSet(Base):
    __tablename__ = "scheme_rule_sets"
    __table_args__ = (
        UniqueConstraint("scheme_code", "version", name="uq_scheme_rule_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    scheme_code: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    required_facts_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    condition_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    recommendation_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_reference: Mapped[str] = mapped_column(String(500), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    creator: Mapped[User] = relationship(foreign_keys=[created_by])
    recommendations: Mapped[list["DSSRecommendation"]] = relationship(
        back_populates="rule_set"
    )


class DSSRecommendation(Base):
    __tablename__ = "dss_recommendations"
    __table_args__ = (
        UniqueConstraint(
            "actor_id", "rule_set_id", "idempotency_key",
            name="uq_dss_actor_rule_idempotency",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fra_claims.id"), nullable=False)
    rule_set_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scheme_rule_sets.id"), nullable=False
    )
    rule_version: Mapped[str] = mapped_column(String(100), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    input_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    output_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    claim: Mapped[FRAClaim] = relationship(back_populates="dss_recommendations")
    rule_set: Mapped[SchemeRuleSet] = relationship(back_populates="recommendations")
    actor: Mapped[User] = relationship(foreign_keys=[actor_id])
