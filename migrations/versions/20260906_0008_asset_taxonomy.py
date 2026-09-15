"""Standardize the FRA asset taxonomy.

Revision ID: 20260906_0008
Revises: 20260905_0007
"""

from alembic import op
import sqlalchemy as sa


revision = "20260906_0008"
down_revision = "20260905_0007"
branch_labels = None
depends_on = None


CANONICAL = (
    "agricultural_land", "water_body", "homestead", "forest_cover",
    "road", "infrastructure", "other_asset",
)
ALIASES = {
    "agricultural_cover": "agricultural_land", "cropland": "agricultural_land",
    "agriculture": "agricultural_land", "farm": "agricultural_land",
    "forest": "forest_cover", "tree_cover": "forest_cover",
    "pond": "water_body", "lake": "water_body", "river_stream": "water_body",
    "river": "water_body", "stream": "water_body", "water_source": "water_body",
    "built_up": "homestead", "house": "homestead",
    "well": "infrastructure", "open_well": "infrastructure", "borewell": "infrastructure",
    "pipeline": "infrastructure", "water_tank": "infrastructure", "tap_water": "infrastructure",
    "check_dam": "infrastructure", "irrigation": "infrastructure",
    "irrigation_canal": "infrastructure", "rainwater_harvesting": "infrastructure",
    "bridge": "infrastructure", "electricity": "infrastructure",
    "electricity_grid": "infrastructure", "solar_power": "infrastructure",
    "school": "infrastructure", "anganwadi": "infrastructure",
    "health_center": "infrastructure", "health_centre": "infrastructure",
    "community_building": "infrastructure", "community_center": "infrastructure",
    "community_centre": "infrastructure", "market": "infrastructure",
    "sanitation_toilet": "infrastructure", "warehouse": "infrastructure",
    "storage_warehouse": "infrastructure", "barren_land": "other_asset",
    "scrubland": "other_asset", "plantation_orchard": "other_asset",
    "grazing_land": "other_asset", "minor_forest_produce": "other_asset",
    "livestock": "other_asset", "fisheries": "other_asset", "forest_nursery": "other_asset",
}
CHECK_SQL = (
    "asset_class IN ('agricultural_land','water_body','homestead','forest_cover',"
    "'road','infrastructure','other_asset')"
)


def _normalized(value) -> str:
    return "_".join(str(value or "").strip().casefold().replace("-", " ").split())


def _canonical(value) -> str:
    normalized = _normalized(value)
    if normalized in CANONICAL:
        return normalized
    return ALIASES.get(normalized, "other_asset")


def _migrate_rows(table_name: str) -> None:
    bind = op.get_bind()
    table = sa.table(
        table_name,
        sa.column("id"),
        sa.column("asset_class", sa.String()),
        sa.column("observed_value_json", sa.JSON()),
    )
    rows = bind.execute(sa.select(table.c.id, table.c.asset_class, table.c.observed_value_json)).mappings()
    for row in rows:
        source = _normalized(row["asset_class"])
        canonical = _canonical(source)
        value = row["observed_value_json"]
        value = dict(value) if isinstance(value, dict) else {"value": value}
        if source and source != canonical:
            value.setdefault("asset_subtype", source)
        bind.execute(
            table.update().where(table.c.id == row["id"]).values(
                asset_class=canonical, observed_value_json=value,
            )
        )


def _create_constraint(table_name: str, constraint_name: str) -> None:
    existing = {
        item.get("name") for item in sa.inspect(op.get_bind()).get_check_constraints(table_name)
    }
    if constraint_name in existing:
        return
    with op.batch_alter_table(table_name) as batch:
        batch.create_check_constraint(constraint_name, CHECK_SQL)


def _drop_constraint(table_name: str, constraint_name: str) -> None:
    existing = {
        item.get("name") for item in sa.inspect(op.get_bind()).get_check_constraints(table_name)
    }
    if constraint_name not in existing:
        return
    with op.batch_alter_table(table_name) as batch:
        batch.drop_constraint(constraint_name, type_="check")


def upgrade():
    for table_name in ("satellite_observations", "asset_features"):
        _migrate_rows(table_name)
    _create_constraint("satellite_observations", "ck_satellite_observations_asset_class")
    _create_constraint("asset_features", "ck_asset_features_asset_class")


def downgrade():
    _drop_constraint("asset_features", "ck_asset_features_asset_class")
    _drop_constraint("satellite_observations", "ck_satellite_observations_asset_class")
