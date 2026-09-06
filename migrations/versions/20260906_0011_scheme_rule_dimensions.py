"""Add complete advisory scheme rule dimensions.

Revision ID: 20260906_0011
Revises: 20260906_0010
"""

from alembic import op
import sqlalchemy as sa


revision = "20260906_0011"
down_revision = "20260906_0010"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "scheme_rule_sets" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("scheme_rule_sets")}
    columns = (
        ("required_evidence_json", sa.JSON(), sa.text("'[]'"), False),
        ("required_assets_json", sa.JSON(), sa.text("'[]'"), False),
        ("exclusion_condition_json", sa.JSON(), None, True),
        ("priority_conditions_json", sa.JSON(), sa.text("'[]'"), False),
        ("freshness_requirements_json", sa.JSON(), sa.text("'{}'"), False),
        ("recommendation_logic_json", sa.JSON(), sa.text("'{}'"), False),
    )
    with op.batch_alter_table("scheme_rule_sets") as batch:
        for name, column_type, default, nullable in columns:
            if name not in existing:
                batch.add_column(
                    sa.Column(
                        name,
                        column_type,
                        nullable=nullable,
                        server_default=default,
                    )
                )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if "scheme_rule_sets" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("scheme_rule_sets")}
    with op.batch_alter_table("scheme_rule_sets") as batch:
        for name in (
            "recommendation_logic_json",
            "freshness_requirements_json",
            "priority_conditions_json",
            "exclusion_condition_json",
            "required_assets_json",
            "required_evidence_json",
        ):
            if name in existing:
                batch.drop_column(name)
