"""Exact OCR page frames for reviewer evidence overlays."""
from alembic import op
import sqlalchemy as sa
revision='20261114_0019'
down_revision='20261114_0018'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('ocr_page_artifacts',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('extraction_run_id',sa.Uuid(),sa.ForeignKey('fra_extraction_runs.id'),nullable=False),
        sa.Column('page_number',sa.Integer(),nullable=False),
        sa.Column('width',sa.Integer(),nullable=False),
        sa.Column('height',sa.Integer(),nullable=False),
        sa.Column('storage_key',sa.String(1000),nullable=False),
        sa.Column('sha256',sa.String(64),nullable=False),
        sa.Column('preprocessing_json',sa.JSON(),nullable=False),
        sa.UniqueConstraint('extraction_run_id','page_number',name='uq_ocr_page_run_number'))


def downgrade():op.drop_table('ocr_page_artifacts')
