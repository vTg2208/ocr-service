"""Canonical FRA asset vocabulary and typed observations; never legal decisions."""

from math import isfinite
from typing import Literal, get_args


AssetClass = Literal[
    "agricultural_land",
    "water_body",
    "homestead",
    "forest_cover",
    "road",
    "infrastructure",
    "other_asset",
]
ASSET_CLASSES = frozenset(get_args(AssetClass))
ASSET_TAXONOMY_VERSION = "fra-assets-v1"
ASSET_CLASS_LABELS = {
    "agricultural_land": "Agricultural land",
    "water_body": "Water body",
    "homestead": "Homestead",
    "forest_cover": "Forest cover",
    "road": "Road",
    "infrastructure": "Infrastructure",
    "other_asset": "Other asset",
}
ASSET_ALIASES = {
    "agricultural_cover": "agricultural_land", "cropland": "agricultural_land",
    "agriculture": "agricultural_land", "farm": "agricultural_land",
    "forest": "forest_cover", "tree_cover": "forest_cover",
    "pond": "water_body", "lake": "water_body", "river_stream": "water_body",
    "river": "water_body", "stream": "water_body", "water_source": "water_body",
    "built_up": "homestead", "house": "homestead",
    "well": "infrastructure", "open_well": "infrastructure", "borewell": "infrastructure",
    "pipeline": "infrastructure", "water_tank": "infrastructure",
    "tap_water": "infrastructure", "check_dam": "infrastructure",
    "irrigation": "infrastructure", "irrigation_canal": "infrastructure",
    "rainwater_harvesting": "infrastructure", "bridge": "infrastructure",
    "electricity": "infrastructure", "electricity_grid": "infrastructure",
    "solar_power": "infrastructure", "school": "infrastructure",
    "anganwadi": "infrastructure", "health_center": "infrastructure",
    "health_centre": "infrastructure", "community_building": "infrastructure",
    "community_center": "infrastructure", "community_centre": "infrastructure",
    "market": "infrastructure", "sanitation_toilet": "infrastructure",
    "warehouse": "infrastructure", "storage_warehouse": "infrastructure",
    "barren_land": "other_asset", "scrubland": "other_asset",
    "plantation_orchard": "other_asset", "grazing_land": "other_asset",
    "minor_forest_produce": "other_asset", "livestock": "other_asset",
    "fisheries": "other_asset", "forest_nursery": "other_asset",
}


def canonical_asset_class(value: str) -> str:
    normalized = "_".join(str(value or "").strip().casefold().replace("-", " ").split())
    return ASSET_ALIASES.get(normalized, normalized)


def normalize_asset_observation(asset_class: str, value) -> tuple[str, dict]:
    """Return the canonical class and preserve a narrower source label as metadata."""
    normalized = "_".join(str(asset_class or "").strip().casefold().replace("-", " ").split())
    canonical = canonical_asset_class(normalized)
    data = observed_value(value)
    if normalized and normalized != canonical:
        data.setdefault("asset_subtype", normalized)
    return canonical, data


def asset_subtype(asset_class: str, value) -> str:
    data = observed_value(value)
    subtype = data.get("asset_subtype")
    if isinstance(subtype, str) and subtype.strip():
        return "_".join(subtype.strip().casefold().replace("-", " ").split())
    return canonical_asset_class(asset_class)


def canonical_label_map(label_map: dict | None) -> dict[str, str]:
    """Validate and normalize a detector's labels before model registration."""
    result: dict[str, str] = {}
    for source, target in (label_map or {}).items():
        if not str(source).strip() or not isinstance(target, str):
            raise ValueError("Asset label maps require non-empty labels and string class names.")
        canonical = canonical_asset_class(target)
        if canonical not in ASSET_CLASSES:
            raise ValueError(f"Unsupported asset class in label map: {target or 'missing'}.")
        result[str(source).strip()] = canonical
    return result


def finite_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def observed_value(value) -> dict:
    return dict(value) if isinstance(value, dict) else {"value": value}


def cover_fraction(asset_class: str, value) -> float | None:
    """Explicit fraction aliases or scalar cover; boolean presence is not a fraction."""
    data = observed_value(value)
    measurements = [data[key] for key in ("cover_fraction", "coverage_fraction") if key in data]
    scalar = data.get("value")
    if canonical_asset_class(asset_class) in {"agricultural_land", "forest_cover"} and "value" in data:
        if scalar is not None and not isinstance(scalar, bool):
            measurements.append(scalar)
    if not measurements:
        return None
    if any(not finite_number(item) or not 0 <= item <= 1 for item in measurements):
        raise ValueError("A cover fraction must be a finite number between 0 and 1.")
    if any(item != measurements[0] for item in measurements[1:]):
        raise ValueError("Conflicting cover fraction measurements.")
    return float(measurements[0])


def validate_asset_value(asset_class: str, value) -> None:
    if canonical_asset_class(asset_class) not in ASSET_CLASSES:
        raise ValueError(f"Unsupported asset class: {asset_class or 'missing'}.")
    data = observed_value(value)
    fraction = cover_fraction(asset_class, data)
    if "present" in data:
        if not isinstance(data["present"], bool):
            raise ValueError("Observed presence must be a boolean.")
        if fraction is not None and data["present"] != (fraction > 0):
            raise ValueError("Observed presence conflicts with the cover fraction.")


def observed_presence(asset_class: str, value) -> bool | None:
    validate_asset_value(asset_class, value)
    data = observed_value(value)
    if "present" in data:
        return data["present"]
    fraction = cover_fraction(asset_class, data)
    if fraction is not None:
        return fraction > 0
    if isinstance(data.get("value"), bool):
        return data["value"]
    # A detected feature without a separate value records presence only.
    return True if not data else None
