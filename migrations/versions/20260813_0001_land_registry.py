"""Create the PostGIS-backed parcel and claim registry."""

from alembic import op

from app.db.base import Base
from app.db import models  # noqa: F401

revision = "20260813_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    Base.metadata.create_all(bind=bind)
    if bind.dialect.name == "postgresql":
        op.execute("CREATE INDEX IF NOT EXISTS parcels_geometry_gix ON parcels USING GIST (geometry)")


def downgrade():
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
