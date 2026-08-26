"""Enforce one registered claim per parcel.

Revision ID: 20260826_0002
Revises: 20260813_0001
"""

from alembic import op
from sqlalchemy import inspect, text


revision = "20260826_0002"
down_revision = "20260813_0001"
branch_labels = None
depends_on = None

CONSTRAINT_NAME = "uq_claim_parcel_exclusive"


def upgrade():
    bind = op.get_bind()
    duplicate = bind.execute(text("""
        SELECT parcel_id, COUNT(*) AS claim_count
        FROM claims
        GROUP BY parcel_id
        HAVING COUNT(*) > 1
        LIMIT 1
    """)).first()
    if duplicate:
        raise RuntimeError(
            "Cannot enable exclusive claims while a parcel has multiple claim records. "
            "Resolve legacy duplicates before rerunning the migration."
        )

    names = {item.get("name") for item in inspect(bind).get_unique_constraints("claims")}
    if CONSTRAINT_NAME not in names:
        with op.batch_alter_table("claims") as batch:
            batch.create_unique_constraint(CONSTRAINT_NAME, ["parcel_id"])


def downgrade():
    bind = op.get_bind()
    names = {item.get("name") for item in inspect(bind).get_unique_constraints("claims")}
    if CONSTRAINT_NAME in names:
        with op.batch_alter_table("claims") as batch:
            batch.drop_constraint(CONSTRAINT_NAME, type_="unique")
