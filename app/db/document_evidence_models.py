"""Private immutable images of the exact coordinate frame consumed by OCR."""
import uuid
from sqlalchemy import ForeignKey, Integer, JSON, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class OCRPageArtifact(Base):
    __tablename__ = 'ocr_page_artifacts'
    __table_args__ = (UniqueConstraint('extraction_run_id','page_number',name='uq_ocr_page_run_number'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(),primary_key=True,default=uuid.uuid4)
    extraction_run_id: Mapped[uuid.UUID] = mapped_column(Uuid(),ForeignKey('fra_extraction_runs.id'),nullable=False)
    page_number: Mapped[int] = mapped_column(Integer,nullable=False)
    width: Mapped[int] = mapped_column(Integer,nullable=False)
    height: Mapped[int] = mapped_column(Integer,nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1000),nullable=False)
    sha256: Mapped[str] = mapped_column(String(64),nullable=False)
    preprocessing_json: Mapped[dict] = mapped_column(JSON,nullable=False,default=dict)
