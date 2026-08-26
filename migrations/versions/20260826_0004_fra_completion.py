"""Add Tamil Nadu-first FRA workflow completion persistence.

Revision ID: 20260826_0004
Revises: 20260826_0003
"""

from alembic import op

from app.db.base import Base
from app.db import fra_completion_models  # noqa: F401
from app.db import fra_models  # noqa: F401
from app.db import models  # noqa: F401


revision = "20260826_0004"
down_revision = "20260826_0003"
branch_labels = None
depends_on = None


TABLE_NAMES = [
    "fra_import_batches",
    "model_versions",
    "fra_village_profiles",
    "fra_archive_records",
    "processing_jobs",
    "fra_extraction_runs",
    "inference_runs",
    "asset_features",
    "dss_referrals",
    "report_artifacts",
]


def upgrade():
    bind = op.get_bind()
    for name in TABLE_NAMES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade():
    bind = op.get_bind()
    for name in reversed(TABLE_NAMES):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
