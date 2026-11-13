"""Community portal: private profiles, immutable review snapshots and staff correspondence."""
import uuid
from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, JSON, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from app.db.models import GEOJSON_MULTIPOLYGON, utcnow

class CommunityProfile(Base):
    __tablename__ = 'community_profiles'
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(), ForeignKey('users.id'), primary_key=True)
    community_name: Mapped[str] = mapped_column(String(120), default='', nullable=False)
    public_label: Mapped[str] = mapped_column(String(120), default='', nullable=False)
    village: Mapped[str] = mapped_column(String(120), default='', nullable=False)
    phone: Mapped[str] = mapped_column(String(30), default='', nullable=False)
    email: Mapped[str] = mapped_column(String(254), default='', server_default='', nullable=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    phone_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email_verification_method: Mapped[str | None] = mapped_column(String(32))
    phone_verification_method: Mapped[str | None] = mapped_column(String(32))
    livelihood: Mapped[str] = mapped_column(String(120), default='', nullable=False)
    share_label: Mapped[bool] = mapped_column(Boolean(), default=False, nullable=False)
    revision: Mapped[int] = mapped_column(default=1, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

class PlotSubmission(Base):
    __tablename__ = 'plot_submissions'
    __table_args__ = (UniqueConstraint('plot_id','plot_revision',name='uq_plot_submission_revision'),
                      Index('ix_plot_submissions_status','status'), Index('ix_plot_submissions_owner','owner_id'))
    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    plot_id: Mapped[uuid.UUID] = mapped_column(Uuid(), ForeignKey('user_plots.id'), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid(), ForeignKey('users.id'), nullable=False)
    plot_revision: Mapped[int] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    village: Mapped[str] = mapped_column(String(120), nullable=False)
    geometry: Mapped[dict] = mapped_column(GEOJSON_MULTIPOLYGON, nullable=False)
    area_sqm: Mapped[float] = mapped_column(Numeric(18,3), nullable=False)
    public_label: Mapped[str] = mapped_column(String(120), nullable=False)
    community_name: Mapped[str] = mapped_column(String(120), nullable=False)
    share_label: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default='submitted', nullable=False)
    review_note: Mapped[str] = mapped_column(Text(), default='', nullable=False)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(), ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(default=1, nullable=False)

class PortalPublication(Base):
    __tablename__ = 'portal_publications'
    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    body: Mapped[str] = mapped_column(Text(), nullable=False)
    source_url: Mapped[str] = mapped_column(String(1000), default='', nullable=False)
    district: Mapped[str] = mapped_column(String(120), default='', nullable=False)
    status: Mapped[str] = mapped_column(String(24), default='draft', nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synthetic: Mapped[bool] = mapped_column(Boolean(), default=False, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid(), ForeignKey('users.id'), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    revision: Mapped[int] = mapped_column(default=1, nullable=False)

class UserResource(Base):
    __tablename__ = 'user_resources'
    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid(), ForeignKey('users.id'), nullable=False, index=True)
    plot_id: Mapped[uuid.UUID] = mapped_column(Uuid(), ForeignKey('user_plots.id'), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text(), nullable=False)
    source: Mapped[str] = mapped_column(String(300), nullable=False)
    verification_state: Mapped[str] = mapped_column(String(24), default='unverified', nullable=False)
    synthetic: Mapped[bool] = mapped_column(Boolean(), default=False, nullable=False)
    recorded_by: Mapped[uuid.UUID] = mapped_column(Uuid(), ForeignKey('users.id'), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

class SupportTicket(Base):
    __tablename__ = 'support_tickets'
    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid(), ForeignKey('users.id'), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    subject: Mapped[str] = mapped_column(String(180), nullable=False)
    plot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(), ForeignKey('user_plots.id'))
    scheme_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(), ForeignKey('portal_publications.id'))
    status: Mapped[str] = mapped_column(String(32), default='open', nullable=False)
    revision: Mapped[int] = mapped_column(default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

class TicketMessage(Base):
    __tablename__ = 'ticket_messages'
    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(Uuid(), ForeignKey('support_tickets.id'), nullable=False, index=True)
    author_id: Mapped[uuid.UUID] = mapped_column(Uuid(), ForeignKey('users.id'), nullable=False)
    author_role: Mapped[str] = mapped_column(String(24), nullable=False)
    body: Mapped[str] = mapped_column(Text(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

class TicketAttachment(Base):
    __tablename__ = 'ticket_attachments'
    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(Uuid(), ForeignKey('support_tickets.id'), nullable=False, index=True)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(Uuid(), ForeignKey('users.id'), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1000), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

class ContactOTP(Base):
    __tablename__ = 'contact_otps'
    __table_args__ = (Index('ix_contact_otps_owner_channel', 'user_id', 'channel'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(), ForeignKey('users.id'), nullable=False)
    channel: Mapped[str] = mapped_column(String(12), nullable=False)
    contact_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    code_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
