"""Add native spatial indexes and Point storage for FRA layers.

Revision ID: 20260905_0007
Revises: 20260905_0006
"""

from alembic import op
import sqlalchemy as sa


revision = "20260905_0007"
down_revision = "20260905_0006"
branch_labels = None
depends_on = None


SPATIAL_INDEXES = (
    ("ix_fra_geometry_versions_geometry_gist", "fra_geometry_versions", "geometry"),
    ("ix_fra_village_profiles_boundary_gist", "fra_village_profiles", "boundary"),
    ("ix_spatial_reference_features_geometry_gist", "spatial_reference_features", "geometry"),
    ("ix_imagery_scenes_footprint_gist", "imagery_scenes", "footprint"),
    ("ix_asset_features_polygon_geometry_gist", "asset_features", "polygon_geometry"),
    ("ix_asset_features_point_geometry_gist", "asset_features", "point_geometry_json"),
)


def _index_names(table_name: str) -> set[str]:
    return {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_indexes(table_name)
        if item.get("name")
    }


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        point_is_native = bool(bind.scalar(sa.text(
            "SELECT EXISTS (SELECT 1 FROM geometry_columns "
            "WHERE f_table_name = 'asset_features' "
            "AND f_geometry_column = 'point_geometry_json' AND type = 'POINT')"
        )))
        if not point_is_native:
            op.execute(
                "ALTER TABLE asset_features "
                "ALTER COLUMN point_geometry_json TYPE geometry(Point,4326) "
                "USING CASE WHEN point_geometry_json IS NULL THEN NULL "
                "ELSE ST_SetSRID(ST_GeomFromGeoJSON(point_geometry_json::text),4326)::geometry(Point,4326) END"
            )
        for name, table, column in SPATIAL_INDEXES:
            op.execute(f'CREATE INDEX IF NOT EXISTS "{name}" ON "{table}" USING GIST ("{column}")')
        return
    for name, table, column in SPATIAL_INDEXES:
        if name not in _index_names(table):
            op.create_index(name, table, [column])


def downgrade():
    bind = op.get_bind()
    for name, table, _column in reversed(SPATIAL_INDEXES):
        if bind.dialect.name == "postgresql":
            op.execute(f'DROP INDEX IF EXISTS "{name}"')
        elif name in _index_names(table):
            op.drop_index(name, table_name=table)
    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TABLE asset_features "
            "ALTER COLUMN point_geometry_json TYPE JSON "
            "USING CASE WHEN point_geometry_json IS NULL THEN NULL "
            "ELSE ST_AsGeoJSON(point_geometry_json)::json END"
        )
