"""Add persisted FRA village asset profiles.

Revision ID: 20260906_0009
Revises: 20260906_0008
"""

from alembic import op
import sqlalchemy as sa


revision = "20260906_0009"
down_revision = "20260906_0008"
branch_labels = None
depends_on = None


def upgrade():
    if "fra_village_asset_profiles" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "fra_village_asset_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("village_id", sa.Uuid(), nullable=False),
        sa.Column("taxonomy_version", sa.String(length=100), nullable=False),
        sa.Column("calculation_version", sa.String(length=100), nullable=False),
        sa.Column("source_digest", sa.String(length=64), nullable=False),
        sa.Column("source_asset_count", sa.Integer(), nullable=False),
        sa.Column("verified_asset_count", sa.Integer(), nullable=False),
        sa.Column("pending_asset_count", sa.Integer(), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("sources_json", sa.JSON(), nullable=False),
        sa.Column("generated_by", sa.Uuid(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["generated_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["village_id"], ["fra_village_profiles.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("village_id", name="uq_fra_village_asset_profile_village"),
    )


def downgrade():
    if "fra_village_asset_profiles" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("fra_village_asset_profiles")
