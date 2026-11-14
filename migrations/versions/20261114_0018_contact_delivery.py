"""Preserve each challenge's actual delivery method across provider changes."""
from alembic import op
import sqlalchemy as sa

revision = "20261114_0018"
down_revision = "20261114_0017"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("contact_otps", sa.Column("delivery_method", sa.String(32), nullable=False, server_default="development_terminal"))


def downgrade():
    op.drop_column("contact_otps", "delivery_method")
