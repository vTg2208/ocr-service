"""Reference dataset kinds accepted by import and downstream consumers."""

WATER_REFERENCE_KINDS = frozenset({"water_stress", "groundwater", "groundwater_stress"})
DATASET_KINDS = frozenset({
    "administrative_boundary", "protected_area", "forest_compartment",
    "water_body", "cadastral_parcel", "forest_area", "infrastructure",
}) | WATER_REFERENCE_KINDS
