"""Add the backward-compatible FRA platform foundation.

Revision ID: 20260826_0003
Revises: 20260826_0002
"""

from alembic import op

from app.db.base import Base
from app.db import fra_models  # noqa: F401
from app.db import models  # noqa: F401


revision = "20260826_0003"
down_revision = "20260826_0002"
branch_labels = None
depends_on = None


TABLE_NAMES = [
    "gram_sabhas",
    "rights_holders",
    "fra_claims",
    "fra_decisions",
    "fra_geometry_versions",
    "satellite_observations",
    "fra_evidence_items",
    "fra_titles",
    "scheme_rule_sets",
    "dss_recommendations",
]


def upgrade():
    bind = op.get_bind()
    for name in TABLE_NAMES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade():
    bind = op.get_bind()
    for name in reversed(TABLE_NAMES):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
