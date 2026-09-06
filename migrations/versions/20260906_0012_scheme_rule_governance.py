"""Link executable rules to governed scheme catalogue versions.

Revision ID: 20260906_0012
Revises: 20260906_0011
"""

from alembic import op
import sqlalchemy as sa


revision = "20260906_0012"
down_revision = "20260906_0011"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "scheme_rule_sets" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("scheme_rule_sets")}
    if "catalog_entry_id" not in columns:
        with op.batch_alter_table("scheme_rule_sets") as batch:
            batch.add_column(sa.Column("catalog_entry_id", sa.Uuid(), nullable=True))
            batch.create_foreign_key(
                "fk_scheme_rule_sets_catalog_entry",
                "scheme_catalog_entries", ["catalog_entry_id"], ["id"],
            )
            batch.create_index("ix_scheme_rule_sets_catalog_entry_id", ["catalog_entry_id"])
    # Only rules tied to the currently active, approved catalogue version remain executable.
    op.execute(sa.text("""
        UPDATE scheme_rule_sets
        SET catalog_entry_id = (
            SELECT scheme_catalog_entries.id
            FROM scheme_catalog_entries
            WHERE scheme_catalog_entries.scheme_code = scheme_rule_sets.scheme_code
              AND scheme_catalog_entries.active = true
              AND scheme_catalog_entries.authoritative = true
            ORDER BY scheme_catalog_entries.created_at DESC
            LIMIT 1
        )
        WHERE catalog_entry_id IS NULL
    """))
    op.execute(sa.text("UPDATE scheme_rule_sets SET active = false WHERE catalog_entry_id IS NULL"))


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if "scheme_rule_sets" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("scheme_rule_sets")}
    if "catalog_entry_id" in columns:
        with op.batch_alter_table("scheme_rule_sets") as batch:
            batch.drop_index("ix_scheme_rule_sets_catalog_entry_id")
            batch.drop_constraint("fk_scheme_rule_sets_catalog_entry", type_="foreignkey")
            batch.drop_column("catalog_entry_id")
