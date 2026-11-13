"""Individual credentials and owner-linked plot boundaries."""
from alembic import op
import sqlalchemy as sa
from migrations.migration_types import GeoJSONMultiPolygon
revision = '20261112_0014'
down_revision = '20260906_0013'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('account_credentials',
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('users.id'), primary_key=True),
        sa.Column('password_hash', sa.String(255), nullable=False))
    op.create_table('user_plots',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('owner_id', sa.Uuid(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('village', sa.String(120), nullable=False),
        sa.Column('geometry', GeoJSONMultiPolygon(), nullable=False),
        sa.Column('area_sqm', sa.Numeric(18, 3), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_user_plots_owner', 'user_plots', ['owner_id'])
    op.create_index('ix_user_plots_geometry_gist', 'user_plots', ['geometry'], postgresql_using='gist')

def downgrade():
    op.drop_table('user_plots')
    op.drop_table('account_credentials')
