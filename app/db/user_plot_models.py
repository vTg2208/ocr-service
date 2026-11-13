"""Individual account credentials and user-drawn plots, separate from FRA claims."""
import uuid
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from app.db.models import GEOJSON_MULTIPOLYGON, utcnow

class AccountCredential(Base):
    __tablename__ = 'account_credentials'
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey('users.id'), primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

class UserPlot(Base):
    __tablename__ = 'user_plots'
    __table_args__ = (Index('ix_user_plots_owner', 'owner_id'),
        Index('ix_user_plots_geometry_gist', 'geometry', postgresql_using='gist'))
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey('users.id'), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    village: Mapped[str] = mapped_column(String(120), default='', nullable=False)
    geometry: Mapped[dict] = mapped_column(GEOJSON_MULTIPOLYGON, nullable=False)
    area_sqm: Mapped[float] = mapped_column(Numeric(18, 3), nullable=False)
    revision: Mapped[int] = mapped_column(default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
