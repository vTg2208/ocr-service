"""Complete core FRA domain links and field-level review provenance.

Revision ID: 20260905_0006
Revises: 20260902_0005
"""

from alembic import op
import sqlalchemy as sa


revision = "20260905_0006"
down_revision = "20260902_0005"
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def _columns(table_name):
    return {column["name"] for column in _inspector().get_columns(table_name)}


def _constraint_names(table_name, kind):
    inspector = _inspector()
    readers = {
        "check": inspector.get_check_constraints,
        "foreign": inspector.get_foreign_keys,
        "unique": inspector.get_unique_constraints,
    }
    return {item.get("name") for item in readers[kind](table_name)}


def upgrade():
    claim_columns = _columns("fra_claims")
    with op.batch_alter_table("fra_claims") as batch:
        if "village_id" not in claim_columns:
            batch.add_column(sa.Column("village_id", sa.Uuid(), nullable=True))
        if "supersedes_claim_id" not in claim_columns:
            batch.add_column(sa.Column("supersedes_claim_id", sa.Uuid(), nullable=True))
    foreign_keys = _constraint_names("fra_claims", "foreign")
    unique_constraints = _constraint_names("fra_claims", "unique")
    check_constraints = _constraint_names("fra_claims", "check")
    with op.batch_alter_table("fra_claims") as batch:
        if "fk_fra_claim_village" not in foreign_keys:
            batch.create_foreign_key(
                "fk_fra_claim_village", "fra_village_profiles", ["village_id"], ["id"]
            )
        if "fk_fra_claim_supersedes" not in foreign_keys:
            batch.create_foreign_key(
                "fk_fra_claim_supersedes", "fra_claims", ["supersedes_claim_id"], ["id"]
            )
        if "uq_fra_claim_supersedes" not in unique_constraints:
            batch.create_unique_constraint("uq_fra_claim_supersedes", ["supersedes_claim_id"])
        if "ck_fra_claim_right_type" not in check_constraints:
            batch.create_check_constraint("ck_fra_claim_right_type", "right_type IN ('IFR', 'CR', 'CFR')")
        if "ck_fra_claim_status" not in check_constraints:
            batch.create_check_constraint(
                "ck_fra_claim_status",
                "status IN ('draft', 'submitted', 'gram_sabha_verified', 'sdlc_review', "
                "'dlc_decided', 'granted', 'rejected', 'remanded', 'withdrawn', 'superseded')",
            )
        if "ck_fra_claim_area_positive" not in check_constraints:
            batch.create_check_constraint(
                "ck_fra_claim_area_positive", "claimed_area_sqm IS NULL OR claimed_area_sqm > 0"
            )

    decision_columns = _columns("fra_decisions")
    with op.batch_alter_table("fra_decisions") as batch:
        if "decision_date" not in decision_columns:
            batch.add_column(sa.Column("decision_date", sa.Date(), nullable=True))
        if "reference_number" not in decision_columns:
            batch.add_column(sa.Column("reference_number", sa.String(length=100), nullable=True))

    evidence_columns = _columns("fra_evidence_items")
    with op.batch_alter_table("fra_evidence_items") as batch:
        if "source_page_start" not in evidence_columns:
            batch.add_column(sa.Column("source_page_start", sa.Integer(), nullable=True))
        if "source_page_end" not in evidence_columns:
            batch.add_column(sa.Column("source_page_end", sa.Integer(), nullable=True))
    evidence_checks = _constraint_names("fra_evidence_items", "check")
    with op.batch_alter_table("fra_evidence_items") as batch:
        if "ck_fra_evidence_page_start_positive" not in evidence_checks:
            batch.create_check_constraint(
                "ck_fra_evidence_page_start_positive",
                "source_page_start IS NULL OR source_page_start > 0",
            )
        if "ck_fra_evidence_page_range" not in evidence_checks:
            batch.create_check_constraint(
                "ck_fra_evidence_page_range",
                "source_page_end IS NULL OR source_page_end >= source_page_start",
            )

    title_columns = _columns("fra_titles")
    with op.batch_alter_table("fra_titles") as batch:
        if "granted_area_sqm" not in title_columns:
            batch.add_column(sa.Column("granted_area_sqm", sa.Numeric(16, 4), nullable=True))
    title_checks = _constraint_names("fra_titles", "check")
    with op.batch_alter_table("fra_titles") as batch:
        if "ck_fra_title_area_positive" not in title_checks:
            batch.create_check_constraint(
                "ck_fra_title_area_positive", "granted_area_sqm IS NULL OR granted_area_sqm > 0"
            )

    holder_checks = _constraint_names("rights_holders", "check")
    if "ck_rights_holder_type" not in holder_checks:
        with op.batch_alter_table("rights_holders") as batch:
            batch.create_check_constraint(
                "ck_rights_holder_type",
                "holder_type IN ('individual', 'household', 'community')",
            )

    if "fra_field_reviews" not in _inspector().get_table_names():
        op.create_table(
            "fra_field_reviews",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column("extraction_run_id", sa.Uuid(), nullable=False),
            sa.Column("field_name", sa.String(length=100), nullable=False),
            sa.Column("source_page", sa.Integer(), nullable=True),
            sa.Column("source_value_json", sa.JSON(), nullable=True),
            sa.Column("extracted_value_json", sa.JSON(), nullable=True),
            sa.Column("extraction_method", sa.String(length=100), nullable=False),
            sa.Column("confidence", sa.Numeric(8, 5), nullable=True),
            sa.Column("evidence_json", sa.JSON(), nullable=False),
            sa.Column("corrected_value_json", sa.JSON(), nullable=True),
            sa.Column("final_value_json", sa.JSON(), nullable=True),
            sa.Column("review_state", sa.String(length=32), nullable=False),
            sa.Column("reviewed_by", sa.Uuid(), nullable=True),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["extraction_run_id"], ["fra_extraction_runs.id"]),
            sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"]),
            sa.UniqueConstraint(
                "extraction_run_id", "field_name", name="uq_fra_field_review_run_field"
            ),
            sa.CheckConstraint(
                "source_page IS NULL OR source_page > 0",
                name="ck_fra_field_review_page_positive",
            ),
            sa.CheckConstraint(
                "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
                name="ck_fra_field_review_confidence",
            ),
        )


def downgrade():
    if "fra_field_reviews" in _inspector().get_table_names():
        op.drop_table("fra_field_reviews")

    with op.batch_alter_table("rights_holders") as batch:
        if "ck_rights_holder_type" in _constraint_names("rights_holders", "check"):
            batch.drop_constraint("ck_rights_holder_type", type_="check")
    with op.batch_alter_table("fra_titles") as batch:
        if "ck_fra_title_area_positive" in _constraint_names("fra_titles", "check"):
            batch.drop_constraint("ck_fra_title_area_positive", type_="check")
        if "granted_area_sqm" in _columns("fra_titles"):
            batch.drop_column("granted_area_sqm")
    with op.batch_alter_table("fra_evidence_items") as batch:
        for name in ("ck_fra_evidence_page_range", "ck_fra_evidence_page_start_positive"):
            if name in _constraint_names("fra_evidence_items", "check"):
                batch.drop_constraint(name, type_="check")
        for name in ("source_page_end", "source_page_start"):
            if name in _columns("fra_evidence_items"):
                batch.drop_column(name)
    with op.batch_alter_table("fra_decisions") as batch:
        for name in ("reference_number", "decision_date"):
            if name in _columns("fra_decisions"):
                batch.drop_column(name)
    with op.batch_alter_table("fra_claims") as batch:
        for name in ("ck_fra_claim_area_positive", "ck_fra_claim_status", "ck_fra_claim_right_type"):
            if name in _constraint_names("fra_claims", "check"):
                batch.drop_constraint(name, type_="check")
        if "uq_fra_claim_supersedes" in _constraint_names("fra_claims", "unique"):
            batch.drop_constraint("uq_fra_claim_supersedes", type_="unique")
        for name in ("fk_fra_claim_supersedes", "fk_fra_claim_village"):
            if name in _constraint_names("fra_claims", "foreign"):
                batch.drop_constraint(name, type_="foreignkey")
        for name in ("supersedes_claim_id", "village_id"):
            if name in _columns("fra_claims"):
                batch.drop_column(name)
