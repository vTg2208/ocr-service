"""Private, reviewable land-cover inference over immutable boundary snapshots."""

from alembic import op
import sqlalchemy as sa

revision = "20261114_0017"
down_revision = "20261112_0016"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "land_cover_analyses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("input_signature", sa.String(64), nullable=False),
        sa.Column("geometry_json", sa.JSON(), nullable=False),
        sa.Column("boundary_version", sa.String(100), nullable=False),
        sa.Column("processing_job_id", sa.Uuid(), sa.ForeignKey("processing_jobs.id"), nullable=False, unique=True),
        sa.Column("requested_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("model_version", sa.String(100), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("review_state", sa.String(24), nullable=False),
        sa.Column("review_note", sa.Text(), nullable=False),
        sa.Column("reviewed_by", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("parameters_json", sa.JSON(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("features_json", sa.JSON(), nullable=False),
        sa.Column("provenance_json", sa.JSON(), nullable=False),
        sa.Column("artifact_keys_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("target_type", "target_id", "input_signature", name="uq_land_cover_input"),
        sa.CheckConstraint("target_type IN ('fra_claim', 'plot_submission')", name="ck_land_cover_target"),
    )
    op.create_index("ix_land_cover_analyses_target_id", "land_cover_analyses", ["target_id"])


def downgrade():
    op.drop_index("ix_land_cover_analyses_target_id", table_name="land_cover_analyses")
    op.drop_table("land_cover_analyses")
