"""Align stored DSS rules with the canonical fact contract.

Revision ID: 20260906_0010
Revises: 20260906_0009
"""

from copy import deepcopy

from alembic import op
import sqlalchemy as sa


revision = "20260906_0010"
down_revision = "20260906_0009"
branch_labels = None
depends_on = None


FACT_NAMES = {
    "claim_right_type",
    "claim_status",
    "has_active_title",
    "village_socioeconomic",
    "water_source_present",
    "homestead_observation",
    "agricultural_observation",
    "agricultural_land_fraction",
    "forest_cover_present",
    "groundwater_status",
    "water_stress_status",
    "road_access",
    "infrastructure_services",
    "mapped_asset_coverage_percent",
    "assets_intersecting_fra_land_count",
    "assets_near_fra_land_count",
    "asset_deficiency_indicators",
    "source_quality_flags",
}
LEGACY_NAMES = {
    "has_title": "has_active_title",
    "has_water": "water_source_present",
    "water_body_present": "water_source_present",
    "homestead_present": "homestead_observation",
    "agricultural_land": "agricultural_land_fraction",
    "forest_observation": "forest_cover_present",
    "forest_cover": "forest_cover_present",
    "water_stress_reference": "water_stress_status",
    "road_present": "road_access",
}


def _mapped_name(value):
    name = str(value or "").strip()
    return LEGACY_NAMES.get(name, name)


def _mapped_condition(condition):
    result = deepcopy(condition)
    if not isinstance(result, dict) or len(result) != 1:
        return result
    operator, payload = next(iter(result.items()))
    if operator in {"all", "any"} and isinstance(payload, list):
        result[operator] = [_mapped_condition(item) for item in payload]
    elif isinstance(payload, dict) and "fact" in payload:
        payload["fact"] = _mapped_name(payload["fact"])
    return result


def _condition_names(condition):
    if not isinstance(condition, dict) or len(condition) != 1:
        return set()
    operator, payload = next(iter(condition.items()))
    if operator in {"all", "any"} and isinstance(payload, list):
        return set().union(*(_condition_names(item) for item in payload))
    if isinstance(payload, dict) and payload.get("fact"):
        return {str(payload["fact"])}
    return set()


def upgrade():
    if "scheme_rule_sets" not in sa.inspect(op.get_bind()).get_table_names():
        return
    rules = sa.table(
        "scheme_rule_sets",
        sa.column("id", sa.Uuid()),
        sa.column("required_facts_json", sa.JSON()),
        sa.column("condition_json", sa.JSON()),
        sa.column("active", sa.Boolean()),
    )
    connection = op.get_bind()
    for row in connection.execute(
        sa.select(
            rules.c.id,
            rules.c.required_facts_json,
            rules.c.condition_json,
            rules.c.active,
        )
    ).mappings():
        required = [_mapped_name(item) for item in (row["required_facts_json"] or [])]
        required = list(dict.fromkeys(required))
        condition = _mapped_condition(row["condition_json"] or {})
        referenced = set(required) | _condition_names(condition)
        values = {
            "required_facts_json": required,
            "condition_json": condition,
        }
        if referenced - FACT_NAMES:
            values["active"] = False
        connection.execute(
            rules.update().where(rules.c.id == row["id"]).values(**values)
        )


def downgrade():
    # Stored recommendations and old fact snapshots remain immutable. Reversing
    # canonical names would make rules ambiguous, so downgrade preserves data.
    pass
