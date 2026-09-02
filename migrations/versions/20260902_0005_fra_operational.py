"""Add connected FRA operational persistence.

Revision ID: 20260902_0005
Revises: 20260826_0004
"""

from alembic import op

from app.db.base import Base
from app.db import fra_completion_models  # noqa: F401
from app.db import fra_models  # noqa: F401
from app.db import fra_operational_models  # noqa: F401
from app.db import models  # noqa: F401


revision = "20260902_0005"
down_revision = "20260826_0004"
branch_labels = None
depends_on = None


TABLE_NAMES = [
    "fra_intake_items",
    "spatial_import_batches",
    "spatial_reference_features",
    "imagery_scenes",
    "imagery_artifacts",
    "dss_fact_snapshots",
    "scheme_catalog_entries",
]


def upgrade():
    bind = op.get_bind()
    for name in TABLE_NAMES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade():
    bind = op.get_bind()
    for name in reversed(TABLE_NAMES):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
