"""Validation for the small, source-traceable Tamil Nadu public-data bundle."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from shapely.geometry import shape


class RealDataValidationError(ValueError):
    pass


PUBLIC_FILES = {
    "village": "arpisampalaiyam_village.geojson",
    "progress": "tamil_nadu_fra_progress_2026-06-30.json",
    "imagery": "arpisampalaiyam_sentinel2_scene.json",
}


def _mapping(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise RealDataValidationError(f"{label} must be an object.")
    return value


def _text(value, label: str) -> str:
    result = " ".join(str(value or "").split())
    if not result:
        raise RealDataValidationError(f"{label} is required.")
    return result


def _public_url(value, label: str) -> str:
    result = _text(value, label)
    parsed = urlparse(result)
    if parsed.scheme != "https" or not parsed.hostname:
        raise RealDataValidationError(f"{label} must be a public HTTPS reference.")
    return result


def _non_synthetic(value: dict, label: str) -> None:
    if value.get("synthetic") is not False:
        raise RealDataValidationError(f"{label} must be explicitly non-synthetic.")


def _count_group(value, label: str) -> dict:
    group = _mapping(value, label)
    counts = {}
    for name in ("individual", "community", "total"):
        raw = group.get(name)
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            raise RealDataValidationError(f"{label} {name} must be a non-negative integer.")
        counts[name] = raw
    if counts["individual"] + counts["community"] != counts["total"]:
        raise RealDataValidationError(f"{label} total does not equal its components.")
    return counts


def validate_public_bundle(*, village: dict, progress: dict, imagery: dict) -> dict:
    """Validate real public inputs without inventing claim-level records."""

    village = _mapping(village, "Village boundary")
    metadata = _mapping(village.get("metadata"), "Village metadata")
    _non_synthetic(metadata, "Village boundary")
    if metadata.get("classification") != "published_authoritative_reference":
        raise RealDataValidationError("Village boundary must have authoritative classification.")
    source_authority = _text(metadata.get("source_authority"), "Village source authority")
    source_reference = _public_url(metadata.get("source_reference"), "Village source reference")
    license_reference = _public_url(metadata.get("license_reference"), "Village license reference")
    if metadata.get("crs") != "EPSG:4326":
        raise RealDataValidationError("Village boundary must be normalized to EPSG:4326.")
    features = village.get("features")
    if not isinstance(features, list) or len(features) != 1:
        raise RealDataValidationError("The validation bundle must contain one village feature.")
    feature = _mapping(features[0], "Village feature")
    properties = _mapping(feature.get("properties"), "Village properties")
    try:
        village_shape = shape(feature.get("geometry"))
    except (TypeError, ValueError) as error:
        raise RealDataValidationError("Village geometry is invalid.") from error
    if village_shape.is_empty or not village_shape.is_valid or village_shape.geom_type not in {"Polygon", "MultiPolygon"}:
        raise RealDataValidationError("Village geometry is invalid.")
    min_x, min_y, max_x, max_y = village_shape.bounds
    if min_x < 76 or max_x > 81 or min_y < 8 or max_y > 14:
        raise RealDataValidationError("Village geometry is outside Tamil Nadu.")

    progress = _mapping(progress, "FRA progress")
    _non_synthetic(progress, "FRA progress")
    if progress.get("state_code") != "TN" or progress.get("granularity") != "state_aggregate":
        raise RealDataValidationError("FRA progress must be a Tamil Nadu state aggregate.")
    progress_source = _mapping(progress.get("source"), "FRA progress source")
    _text(progress_source.get("authority"), "FRA progress authority")
    _public_url(progress_source.get("reference"), "FRA progress source reference")
    checksum = _text(progress_source.get("document_sha256"), "FRA progress document checksum")
    if len(checksum) != 64 or any(character not in "0123456789abcdef" for character in checksum.casefold()):
        raise RealDataValidationError("FRA progress document checksum must be SHA-256.")
    claims = _count_group(progress.get("claims_received"), "FRA claims received")
    titles = _count_group(progress.get("titles_distributed"), "FRA titles distributed")
    if titles["individual"] > claims["individual"] or titles["community"] > claims["community"]:
        raise RealDataValidationError("FRA title totals cannot exceed received claim totals.")

    imagery = _mapping(imagery, "Satellite imagery")
    _non_synthetic(imagery, "Satellite imagery")
    target = _mapping(imagery.get("target"), "Satellite target")
    if str(target.get("village_code")) != str(properties.get("village_code")):
        raise RealDataValidationError("Satellite target does not match the village code.")
    scene = _mapping(imagery.get("selected_scene"), "Selected satellite scene")
    _text(scene.get("scene_id"), "Satellite scene ID")
    _text(scene.get("collection"), "Satellite collection")
    try:
        scene_shape = shape(scene.get("footprint"))
    except (TypeError, ValueError) as error:
        raise RealDataValidationError("Satellite footprint is invalid.") from error
    if scene_shape.is_empty or not scene_shape.is_valid:
        raise RealDataValidationError("Satellite footprint is invalid.")
    if not scene_shape.intersects(village_shape):
        raise RealDataValidationError("Satellite footprint does not intersect the village boundary.")
    imagery_validation = _mapping(imagery.get("validation"), "Satellite validation")
    if imagery_validation.get("private_asset_urls_persisted") is not False:
        raise RealDataValidationError("Private satellite asset URLs must not be persisted.")

    return {
        "status": "validated_with_limitations",
        "synthetic_records": 0,
        "fra_progress": {
            "as_of": progress.get("as_of"),
            "granularity": progress["granularity"],
            "claims_received_total": claims["total"],
            "titles_distributed_total": titles["total"],
            "source_authority": progress_source["authority"],
            "source_reference": progress_source["reference"],
        },
        "village": {
            "village_code": str(properties["village_code"]),
            "village_name": properties.get("village_name"),
            "district_name": properties.get("district_name"),
            "geometry_valid": True,
            "source_authority": source_authority,
            "source_reference": source_reference,
            "license_reference": license_reference,
        },
        "satellite_imagery": {
            "scene_id": scene["scene_id"],
            "collection": scene["collection"],
            "acquired_at": scene.get("acquired_at"),
            "intersects_village": True,
            "private_asset_urls_persisted": False,
        },
        "limitations": [
            "claim_level_fra_records",
            "individual_fra_title_documents",
            "fra_claim_and_title_geometries",
            "asset_model_accuracy_deferred_to_user_model",
        ],
    }


def load_and_validate_public_bundle(directory: Path) -> dict:
    directory = Path(directory)
    values = {}
    for name, filename in PUBLIC_FILES.items():
        path = directory / filename
        try:
            values[name] = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise RealDataValidationError(f"Missing real-data file: {filename}.") from error
        except json.JSONDecodeError as error:
            raise RealDataValidationError(f"Invalid JSON in real-data file: {filename}.") from error
    return validate_public_bundle(**values)


__all__ = [
    "PUBLIC_FILES",
    "RealDataValidationError",
    "load_and_validate_public_bundle",
    "validate_public_bundle",
]
