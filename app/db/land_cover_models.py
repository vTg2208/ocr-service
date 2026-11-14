"""Private inference artifacts tied to an immutable boundary and review."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, JSON, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models import utcnow


class LandCoverAnalysis(Base):
    __tablename__ = "land_cover_analyses"
    __table_args__ = (
        UniqueConstraint("target_type", "target_id", "input_signature", name="uq_land_cover_input"),
        CheckConstraint("target_type IN ('fra_claim', 'plot_submission')", name="ck_land_cover_target"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(Uuid(), nullable=False, index=True)
    input_signature: Mapped[str] = mapped_column(String(64), nullable=False)
    geometry_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    boundary_version: Mapped[str] = mapped_column(String(100), nullable=False)
    processing_job_id: Mapped[uuid.UUID] = mapped_column(Uuid(), ForeignKey("processing_jobs.id"), unique=True, nullable=False)
    requested_by: Mapped[uuid.UUID] = mapped_column(Uuid(), ForeignKey("users.id"), nullable=False)
    model_version: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    review_state: Mapped[str] = mapped_column(String(24), default="unverified", nullable=False)
    review_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(), ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(default=1, nullable=False)
    parameters_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    summary_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    features_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    provenance_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    artifact_keys_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
