"""Add leases and retained failure history to FRA processing jobs.

Revision ID: 20260906_0013
Revises: 20260906_0012
"""

from alembic import op
import sqlalchemy as sa


revision = "20260906_0013"
down_revision = "20260906_0012"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "processing_jobs" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("processing_jobs")}
    with op.batch_alter_table("processing_jobs") as batch:
        if "failure_history_json" not in columns:
            batch.add_column(
                sa.Column("failure_history_json", sa.JSON(), nullable=False, server_default="[]")
            )
        if "lease_token" not in columns:
            batch.add_column(sa.Column("lease_token", sa.String(length=36), nullable=True))
        if "lease_expires_at" not in columns:
            batch.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
        if "heartbeat_at" not in columns:
            batch.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))

    # Jobs running before leases existed cannot prove a live owner after deployment.
    # Return them to the queue so the new worker can recover them safely.
    op.execute(sa.text("""
        UPDATE processing_jobs
        SET state = 'queued', worker_id = NULL, started_at = NULL,
            available_at = CURRENT_TIMESTAMP,
            error_code = 'worker_restart_recovery',
            error_message = 'Recovered during deployment because the former job had no lease.'
        WHERE state = 'running' AND lease_expires_at IS NULL
    """))

    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("processing_jobs")}
    with op.batch_alter_table("processing_jobs") as batch:
        if "ix_processing_jobs_dispatch" not in indexes:
            batch.create_index(
                "ix_processing_jobs_dispatch", ["state", "available_at", "created_at"]
            )
        if "ix_processing_jobs_lease" not in indexes:
            batch.create_index(
                "ix_processing_jobs_lease", ["state", "lease_expires_at"]
            )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "processing_jobs" not in inspector.get_table_names():
        return
    indexes = {index["name"] for index in inspector.get_indexes("processing_jobs")}
    columns = {column["name"] for column in inspector.get_columns("processing_jobs")}
    with op.batch_alter_table("processing_jobs") as batch:
        if "ix_processing_jobs_lease" in indexes:
            batch.drop_index("ix_processing_jobs_lease")
        if "ix_processing_jobs_dispatch" in indexes:
            batch.drop_index("ix_processing_jobs_dispatch")
        for name in ("heartbeat_at", "lease_expires_at", "lease_token", "failure_history_json"):
            if name in columns:
                batch.drop_column(name)
