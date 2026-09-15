"""Tamil Nadu reference-data import and privacy-safe FRA Atlas queries."""

from dataclasses import dataclass, field
import hashlib
from typing import Any

from shapely.geometry import shape
from sqlalchemy import select

from app.db.fra_completion_models import AssetFeature, FRAVillageProfile
from app.db.fra_models import FRAClaim, FRATitle
from app.db.fra_operational_models import ImageryArtifact, SpatialReferenceFeature
from app.db.models import User
from app.models.fra_schemas import normalize_geometry
from app.services.audit import record_audit
from app.services.asset_contracts import ASSET_CLASSES, ASSET_TAXONOMY_VERSION, asset_subtype
from app.services.fra_spatial_policy import _area_sqm
from app.services.state_profiles import get_state_profile
from app.services.fra_locations import claim_location, location_matches, matches


ADMINISTRATIVE_LAYERS = {"country", "state", "district", "block"}
SUPPORTING_LAYERS = {
    "forest_area", "protected_area", "cadastral_parcel", "water_body",
    "infrastructure", "forest_compartment", "groundwater",
    "groundwater_stress", "water_stress",
}
IMAGERY_LAYERS = {"satellite_imagery", "historical_imagery"}
ATLAS_LAYERS = ADMINISTRATIVE_LAYERS | SUPPORTING_LAYERS | {
    "village", "claim", "title", "asset",
} | IMAGERY_LAYERS


class AtlasValidationError(ValueError):
    pass


@dataclass(frozen=True)
class VillageImportReport:
    inserted: int
    updated: int
    unchanged: int
    version: str


@dataclass(frozen=True)
class AtlasFilters:
    state: str = "TN"
    district: str | None = None
    block: str | None = None
    village: str | None = None
    tribal_group: str | None = None
    claimant_category: str | None = None
    right_type: str | None = None
    status: str | None = None
    year: int | None = None
    min_area_sqm: float | None = None
    max_area_sqm: float | None = None
    layers: tuple[str, ...] = field(default_factory=lambda: tuple(sorted(ATLAS_LAYERS)))

    def __post_init__(self):
        get_state_profile(self.state)
        invalid = set(self.layers) - ATLAS_LAYERS
        if invalid:
            raise AtlasValidationError(f"Unsupported Atlas layer: {sorted(invalid)[0]}.")
        if self.year is not None and not 1900 <= self.year <= 2100:
            raise AtlasValidationError("Atlas year is invalid.")
        if self.min_area_sqm is not None and self.min_area_sqm < 0:
            raise AtlasValidationError("Atlas minimum area must not be negative.")
        if self.max_area_sqm is not None and self.max_area_sqm < 0:
            raise AtlasValidationError("Atlas maximum area must not be negative.")
        if (
            self.min_area_sqm is not None and self.max_area_sqm is not None
            and self.min_area_sqm > self.max_area_sqm
        ):
            raise AtlasValidationError("Atlas minimum area cannot exceed maximum area.")


@dataclass(frozen=True)
class AtlasSummary:
    village_count: int
    claim_count: int
    title_count: int
    asset_count: int
    claimed_area_sqm: float
    granted_area_sqm: float
    granted_claim_count: int
    rejected_claim_count: int
    pending_claim_count: int
    by_right_type: dict[str, int]
    by_status: dict[str, int]
    by_district: dict[str, int]
    progress: dict[str, list[dict[str, Any]]]


def _normalized_multipolygon(raw: Any) -> dict:
    try:
        geometry = normalize_geometry(raw)
        parsed = shape(geometry)
    except (TypeError, ValueError) as error:
        raise AtlasValidationError(str(error)) from error
    min_x, min_y, max_x, max_y = parsed.bounds
    if min_x < 76 or max_x > 81 or min_y < 8 or max_y > 14:
        raise AtlasValidationError("Village geometry must fall within the Tamil Nadu reference extent.")
    return geometry


def _required_text(properties: dict, key: str) -> str:
    value = " ".join(str(properties.get(key) or "").split())
    if not value:
        raise AtlasValidationError(f"Village property {key} is required.")
    return value


def import_village_profiles(session, payload: dict, *, actor_id) -> VillageImportReport:
    actor = session.get(User, actor_id)
    if actor is None or actor.role != "admin":
        raise PermissionError("Tamil Nadu reference-data import requires an administrator.")
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
        raise AtlasValidationError("Village reference data must be a GeoJSON FeatureCollection.")
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise AtlasValidationError("Village reference metadata is required.")
    if metadata.get("state_code") != "TN" or metadata.get("state_name") != "Tamil Nadu":
        raise AtlasValidationError("Only the Tamil Nadu state profile is supported.")
    if not isinstance(metadata.get("synthetic"), bool):
        raise AtlasValidationError("Village reference metadata must explicitly declare synthetic true or false.")
    synthetic = metadata["synthetic"]
    source = _required_text(metadata, "source")
    version = _required_text(metadata, "version")
    source_authority = source_reference = license_reference = None
    classification = "published_synthetic_reference"
    if not synthetic:
        source_authority = _required_text(metadata, "source_authority")
        source_reference = _required_text(metadata, "source_reference")
        license_reference = _required_text(metadata, "license_reference")
        classification = _required_text(metadata, "classification")
        if classification != "published_authoritative_reference":
            raise AtlasValidationError(
                "Real village reference data must be classified as published_authoritative_reference."
            )
    features = payload.get("features")
    if not isinstance(features, list) or not features:
        raise AtlasValidationError("Village reference data must contain features.")

    prepared: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    profile = get_state_profile("TN")
    for feature in features:
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise AtlasValidationError("Each village must be a GeoJSON Feature.")
        properties = feature.get("properties")
        if not isinstance(properties, dict):
            raise AtlasValidationError("Each village feature requires properties.")
        values = {
            "state_code": "TN",
            "state_name": "Tamil Nadu",
            "district_code": _required_text(properties, "district_code"),
            "district_name": profile.normalize_district(
                _required_text(properties, "district_name")
            ),
            "block_code": _required_text(properties, "block_code"),
            "block_name": profile.normalize_block(_required_text(properties, "block_name")),
            "village_code": _required_text(properties, "village_code"),
            "village_name": profile.normalize_village(
                _required_text(properties, "village_name")
            ),
            "boundary": _normalized_multipolygon(feature.get("geometry")),
            "tribal_groups_json": list(properties.get("tribal_groups") or []),
            "socioeconomic_json": dict(properties.get("socioeconomic") or {}),
            "provenance_json": {
                "synthetic": synthetic,
                "source": source,
                "version": version,
                "classification": classification,
                **({
                    "source_authority": source_authority,
                    "source_reference": source_reference,
                    "license_reference": license_reference,
                    "source_record_id": properties.get("source_record_id"),
                } if not synthetic else {}),
            },
            "reference_version": version,
            "synthetic": synthetic,
        }
        natural_key = (
            values["state_code"],
            values["district_code"],
            values["block_code"],
            values["village_code"],
        )
        if natural_key in seen:
            raise AtlasValidationError("Village codes must be unique within the import.")
        seen.add(natural_key)
        prepared.append(values)

    inserted = updated = unchanged = 0
    for values in prepared:
        existing = session.scalar(
            select(FRAVillageProfile).where(
                FRAVillageProfile.state_code == values["state_code"],
                FRAVillageProfile.district_code == values["district_code"],
                FRAVillageProfile.block_code == values["block_code"],
                FRAVillageProfile.village_code == values["village_code"],
            )
        )
        if existing is None:
            session.add(FRAVillageProfile(**values))
            inserted += 1
            continue
        changed = any(getattr(existing, key) != value for key, value in values.items())
        if changed:
            for key, value in values.items():
                setattr(existing, key, value)
            updated += 1
        else:
            unchanged += 1
    session.flush()
    record_audit(
        session,
        actor_id=actor_id,
        action="fra_village_reference_imported",
        entity_type="state_reference",
        entity_id=actor_id,
        after={
            "state_code": "TN",
            "version": version,
            "inserted": inserted,
            "updated": updated,
            "unchanged": unchanged,
            "synthetic": synthetic,
        },
    )
    return VillageImportReport(inserted, updated, unchanged, version)


def _matches(value: str | None, expected: str | None) -> bool:
    return matches(value, expected)


def _village_matches(village: FRAVillageProfile, filters: AtlasFilters) -> bool:
    if village.state_code != get_state_profile(filters.state).code:
        return False
    if not _matches(village.district_name, filters.district):
        return False
    if not _matches(village.block_name, filters.block):
        return False
    if not _matches(village.village_name, filters.village):
        return False
    if filters.tribal_group and not any(
        item.casefold() == filters.tribal_group.casefold()
        for item in village.tribal_groups_json
    ):
        return False
    return True


def _claim_matches(
    claim: FRAClaim,
    filters: AtlasFilters,
    villages: list[FRAVillageProfile],
    *,
    match_year: bool = True,
) -> bool:
    location = claim_location(claim)
    if not location_matches(location, state=filters.state, district=filters.district,
                            block=filters.block, village=filters.village):
        return False
    if filters.right_type and claim.right_type != filters.right_type.upper():
        return False
    if filters.status and claim.status.casefold() != filters.status.casefold():
        return False
    if (
        filters.claimant_category
        and (claim.rights_holder.claimant_category or "").casefold()
        != filters.claimant_category.casefold()
    ):
        return False
    claim_year = (
        (claim.provenance_json or {}).get("legacy_mapping", {}).get("claim_year")
        or claim.created_at.year
    )
    if match_year and filters.year and int(claim_year) != filters.year:
        return False
    if filters.tribal_group:
        village = next(
            (
                item
                for item in villages
                if matches(item.village_name, location["village"])
                and matches(item.block_name, location["block"])
                and matches(item.district_name, location["district"])
            ),
            None,
        )
        if village is None or not _village_matches(village, filters):
            return False
    geometry = _current_geometry(claim)
    area = (
        float(claim.claimed_area_sqm)
        if claim.claimed_area_sqm is not None
        else _area_sqm(geometry) if geometry is not None else None
    )
    return _area_matches(area, filters)


def _area_matches(area: float | None, filters: AtlasFilters) -> bool:
    if filters.min_area_sqm is not None and (area is None or area < filters.min_area_sqm):
        return False
    if filters.max_area_sqm is not None and (area is None or area > filters.max_area_sqm):
        return False
    return True


def _current_geometry(claim: FRAClaim):
    return max(claim.geometry_versions, key=lambda item: item.version).geometry if claim.geometry_versions else None


def _reference_layer(feature: SpatialReferenceFeature) -> tuple[str, str] | None:
    if feature.dataset_kind == "administrative_boundary":
        level = str((feature.properties_json or {}).get("admin_level") or "").strip().casefold()
        return ("administrative", level) if level in ADMINISTRATIVE_LAYERS else None
    if feature.dataset_kind in SUPPORTING_LAYERS:
        return "reference", feature.dataset_kind
    return None


def _reference_matches(
    feature: SpatialReferenceFeature,
    filters: AtlasFilters,
    villages: list[FRAVillageProfile],
) -> bool:
    properties = feature.properties_json or {}
    for key, expected in (
        ("district", filters.district), ("block", filters.block), ("village", filters.village),
    ):
        actual = properties.get(key) or properties.get(f"{key}_name")
        if expected and actual and not matches(actual, expected):
            return False
    if not any((filters.district, filters.block, filters.village)):
        return True
    scoped = [village for village in villages if _village_matches(village, filters)]
    if not scoped:
        return False
    candidate = shape(feature.geometry)
    return any(candidate.intersects(shape(village.boundary)) for village in scoped)


def _imagery_layer(artifact: ImageryArtifact) -> str | None:
    if artifact.artifact_type.startswith("analysis_ready_raster:"):
        return "satellite_imagery"
    if artifact.artifact_type.startswith("historical_land_observation:"):
        return "historical_imagery"
    return None


def _imagery_feature(
    artifact: ImageryArtifact,
    layer: str,
    *,
    privileged: bool,
    actor_id,
) -> dict:
    claim = artifact.claim
    scene = artifact.imagery_scene
    acquired_at = scene.acquired_at if scene is not None else None
    target_year = artifact.target_year or (acquired_at.year if acquired_at else None)
    label = (
        "Historical imagery coverage"
        if layer == "historical_imagery"
        else "Satellite imagery coverage"
    )
    properties = {
        "kind": "imagery",
        "layer": layer,
        "name": f"{label} · {target_year}" if target_year else label,
        "coverage_type": "claim_analysis_extent",
        "target_year": target_year,
        "provider": scene.provider if scene is not None else None,
        "collection": scene.collection if scene is not None else None,
        "scene_id": scene.scene_id if scene is not None else None,
        "acquired_at": acquired_at.isoformat() if acquired_at else None,
        "cloud_cover": (
            float(scene.cloud_cover)
            if scene is not None and scene.cloud_cover is not None
            else None
        ),
        "license_reference": scene.license_reference if scene is not None else None,
        "processor_version": artifact.processor_version,
        "verification_state": artifact.verification_state,
        "quality_flags": list(artifact.quality_flags_json or []),
        "valid_pixel_percent": (artifact.statistics_json or {}).get(
            "valid_pixel_percent"
        ),
        "legal_role": "supporting_observation",
        "synthetic": bool(
            artifact.synthetic or (scene.synthetic if scene is not None else False)
        ),
    }
    if privileged or actor_id == claim.submitted_by:
        properties.update(
            {
                "artifact_id": str(artifact.id),
                "claim_id": str(claim.id),
                "claim_number": claim.claim_number,
            }
        )
    public_id = hashlib.sha256(str(artifact.id).encode("ascii")).hexdigest()[:20]
    return {
        "type": "Feature",
        "id": f"imagery-{public_id}",
        "geometry": artifact.geometry_version.geometry,
        "properties": properties,
    }


def atlas_features(session, filters: AtlasFilters, *, privileged: bool, actor_id=None) -> dict:
    all_villages = list(
        session.scalars(
            select(FRAVillageProfile).order_by(
                FRAVillageProfile.district_code,
                FRAVillageProfile.block_code,
                FRAVillageProfile.village_code,
            )
        )
    )
    features: list[dict] = []
    references = list(session.scalars(
        select(SpatialReferenceFeature)
        .where(SpatialReferenceFeature.published.is_(True))
        .order_by(
            SpatialReferenceFeature.dataset_kind,
            SpatialReferenceFeature.source_authority,
            SpatialReferenceFeature.source_version,
            SpatialReferenceFeature.source_record_id,
        )
    ))
    for reference in references:
        layer = _reference_layer(reference)
        if layer is None or layer[1] not in filters.layers:
            continue
        if not _reference_matches(reference, filters, all_villages):
            continue
        properties = reference.properties_json or {}
        features.append({
            "type": "Feature",
            "id": str(reference.id),
            "geometry": reference.geometry,
            "properties": {
                "kind": layer[0],
                "layer": layer[1],
                "name": str(
                    properties.get("name") or properties.get("display_name")
                    or reference.source_record_id
                ),
                "district": properties.get("district") or properties.get("district_name"),
                "block": properties.get("block") or properties.get("block_name"),
                "village": properties.get("village") or properties.get("village_name"),
                "source_authority": reference.source_authority,
                "source_version": reference.source_version,
                "source_record_id": reference.source_record_id,
                "synthetic": reference.synthetic,
            },
        })
    if "village" in filters.layers:
        for village in all_villages:
            if not _village_matches(village, filters):
                continue
            features.append(
                {
                    "type": "Feature",
                    "id": str(village.id),
                    "geometry": village.boundary,
                    "properties": {
                        "kind": "village",
                        "state_code": village.state_code,
                        "district": village.district_name,
                        "block": village.block_name,
                        "village": village.village_name,
                        "village_code": village.village_code,
                        "tribal_groups": list(village.tribal_groups_json or []),
                        "reference_version": village.reference_version,
                        "synthetic": village.synthetic,
                        "area_sqm": round(_area_sqm(village.boundary), 2),
                    },
                }
            )
    claims = list(session.scalars(select(FRAClaim).order_by(FRAClaim.claim_number, FRAClaim.id)))
    scoped_claims = [claim for claim in claims if _claim_matches(claim, filters, all_villages)]
    imagery_scoped_claim_ids = {
        claim.id
        for claim in claims
        if _claim_matches(claim, filters, all_villages, match_year=False)
    }
    if "claim" in filters.layers:
        for claim in scoped_claims:
            geometry = _current_geometry(claim)
            if geometry is None:
                continue
            properties = {
                "kind": "claim",
                "claim_number": claim.claim_number,
                "right_type": claim.right_type,
                "status": claim.status,
                "synthetic": bool(claim.provenance_json.get("synthetic")),
                "area_sqm": (
                    float(claim.claimed_area_sqm)
                    if claim.claimed_area_sqm is not None
                    else round(_area_sqm(geometry), 2)
                ),
            }
            if privileged:
                properties.update(
                    {
                        "claim_id": str(claim.id),
                        "rights_holder_id": str(claim.rights_holder_id),
                        "gram_sabha_id": str(claim.gram_sabha_id) if claim.gram_sabha_id else None,
                    }
                )
            elif actor_id == claim.submitted_by:
                properties["claim_id"] = str(claim.id)
            features.append(
                {"type": "Feature", "id": str(claim.id), "geometry": geometry, "properties": properties}
            )
    if "title" in filters.layers:
        for claim in scoped_claims:
            for title in sorted(claim.titles, key=lambda item: (item.version, str(item.id))):
                title_area = (
                    float(title.granted_area_sqm)
                    if title.granted_area_sqm is not None
                    else float(claim.claimed_area_sqm) if claim.claimed_area_sqm is not None
                    else None
                )
                if not _area_matches(title_area, filters):
                    continue
                if filters.year and title.issued_at.year != filters.year:
                    continue
                geometry = title.geometry_version.geometry if title.geometry_version else _current_geometry(claim)
                if geometry is None:
                    continue
                properties = {
                    "kind": "title",
                    "title_number": title.title_number,
                    "right_type": claim.right_type,
                    "status": "active" if title.active else "superseded",
                    "synthetic": bool(claim.provenance_json.get("synthetic")),
                }
                if privileged:
                    properties["claim_id"] = str(claim.id)
                    properties["title_id"] = str(title.id)
                elif actor_id == claim.submitted_by:
                    properties["claim_id"] = str(claim.id)
                features.append(
                    {"type": "Feature", "id": str(title.id), "geometry": geometry, "properties": properties}
                )
    if "asset" in filters.layers:
        assets = session.scalars(select(AssetFeature).order_by(AssetFeature.created_at, AssetFeature.id)).all()
        scoped_village_ids = {item.id for item in all_villages if _village_matches(item, filters)}
        scoped_claim_ids = {item.id for item in scoped_claims}
        for asset in assets:
            if asset.village_id and asset.village_id not in scoped_village_ids:
                continue
            if asset.claim_id and asset.claim_id not in scoped_claim_ids:
                continue
            geometry = asset.polygon_geometry or asset.point_geometry_json
            if geometry is None:
                continue
            properties = {
                "kind": "asset",
                "asset_class": asset.asset_class,
                "asset_subtype": asset_subtype(asset.asset_class, asset.observed_value_json),
                "verification_state": asset.verification_state,
                "confidence": float(asset.confidence) if asset.confidence is not None else None,
                "synthetic": asset.synthetic,
            }
            if privileged:
                properties["asset_id"] = str(asset.id)
                properties["claim_id"] = str(asset.claim_id) if asset.claim_id else None
                properties["village_id"] = str(asset.village_id) if asset.village_id else None
            features.append(
                {"type": "Feature", "id": str(asset.id), "geometry": geometry, "properties": properties}
            )
    if IMAGERY_LAYERS.intersection(filters.layers):
        artifacts = session.scalars(
            select(ImageryArtifact)
            .where(ImageryArtifact.state == "completed")
            .order_by(
                ImageryArtifact.target_year,
                ImageryArtifact.created_at,
                ImageryArtifact.id,
            )
        ).all()
        for artifact in artifacts:
            layer = _imagery_layer(artifact)
            if layer is None or layer not in filters.layers:
                continue
            if artifact.claim_id not in imagery_scoped_claim_ids:
                continue
            artifact_year = artifact.target_year or (
                artifact.imagery_scene.acquired_at.year
                if artifact.imagery_scene
                else None
            )
            if filters.year is not None and artifact_year != filters.year:
                continue
            features.append(
                _imagery_feature(
                    artifact,
                    layer,
                    privileged=privileged,
                    actor_id=actor_id,
                )
            )
    kind_order = {
        "administrative": 0,
        "village": 1,
        "reference": 2,
        "imagery": 3,
        "claim": 4,
        "title": 5,
        "asset": 6,
    }
    features.sort(key=lambda item: (kind_order[item["properties"]["kind"]], str(item["id"])))
    layer_counts: dict[str, int] = {}
    for feature in features:
        layer = feature["properties"].get("layer") or feature["properties"]["kind"]
        layer_counts[layer] = layer_counts.get(layer, 0) + 1
    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "state_code": "TN",
            "synthetic_warning": "Synthetic sample data are not authoritative.",
            "asset_taxonomy_version": ASSET_TAXONOMY_VERSION,
            "asset_classes": sorted(ASSET_CLASSES),
            "layer_counts": dict(sorted(layer_counts.items())),
            "imagery_rendering": "coverage_index",
        },
    }


PENDING_STATUSES = {
    "draft", "submitted", "gram_sabha_verified", "sdlc_review", "dlc_decided", "remanded",
}


def _claim_area(claim: FRAClaim) -> float | None:
    if claim.claimed_area_sqm is not None:
        return float(claim.claimed_area_sqm)
    geometry = _current_geometry(claim)
    return _area_sqm(geometry) if geometry is not None else None


def _claim_statistics(claims: list[FRAClaim]) -> dict[str, Any]:
    by_right: dict[str, int] = {}
    by_status: dict[str, int] = {}
    claimed_area = 0.0
    granted_area = 0.0
    title_count = 0
    for claim in claims:
        by_right[claim.right_type] = by_right.get(claim.right_type, 0) + 1
        by_status[claim.status] = by_status.get(claim.status, 0) + 1
        claimed_area += float(_claim_area(claim) or 0)
        for title in claim.titles:
            title_count += 1
            if title.active and title.granted_area_sqm is not None:
                granted_area += float(title.granted_area_sqm)
    return {
        "total_claims": len(claims),
        "granted_claims": by_status.get("granted", 0),
        "rejected_claims": by_status.get("rejected", 0),
        "pending_claims": sum(by_status.get(status, 0) for status in PENDING_STATUSES),
        "claimed_area_sqm": round(claimed_area, 2),
        "granted_area_sqm": round(granted_area, 2),
        "title_count": title_count,
        "by_right_type": dict(sorted(by_right.items())),
        "by_status": dict(sorted(by_status.items())),
    }


def _progress_rows(claims: list[FRAClaim], state: str) -> dict[str, list[dict[str, Any]]]:
    state_name = get_state_profile(state).name
    grouped: dict[str, dict[tuple[str, ...], list[FRAClaim]]] = {
        level: {} for level in ("state", "district", "block", "village")
    }
    for claim in claims:
        location = claim_location(claim)
        values = {
            "state": state_name,
            "district": location.get("district"),
            "block": location.get("block"),
            "village": location.get("village"),
        }
        hierarchy: list[str] = []
        for level in grouped:
            value = values[level]
            if not value:
                break
            hierarchy.append(str(value))
            grouped[level].setdefault(tuple(hierarchy), []).append(claim)
    progress: dict[str, list[dict[str, Any]]] = {}
    levels = ("state", "district", "block", "village")
    for level, groups in grouped.items():
        rows = []
        for key, members in sorted(groups.items(), key=lambda item: tuple(part.casefold() for part in item[0])):
            stats = _claim_statistics(members)
            location = {name: key[index] for index, name in enumerate(levels[:len(key)])}
            rows.append({"level": level, **location, **{
                field: stats[field] for field in (
                    "total_claims", "granted_claims", "rejected_claims", "pending_claims",
                    "claimed_area_sqm", "granted_area_sqm",
                )
            }})
        progress[level] = rows
    return progress


def atlas_summary(session, filters: AtlasFilters) -> AtlasSummary:
    all_villages = list(session.scalars(select(FRAVillageProfile)))
    claims = list(session.scalars(select(FRAClaim).order_by(FRAClaim.claim_number, FRAClaim.id)))
    scoped_claims = [claim for claim in claims if _claim_matches(claim, filters, all_villages)]
    stats = _claim_statistics(scoped_claims)
    matching_villages = [village for village in all_villages if _village_matches(village, filters)]
    by_district: dict[str, int] = {}
    for village in matching_villages:
        by_district[village.district_name] = by_district.get(village.district_name, 0) + 1
    feature_collection = atlas_features(
        session,
        AtlasFilters(
            state=filters.state, district=filters.district, block=filters.block,
            village=filters.village, tribal_group=filters.tribal_group,
            claimant_category=filters.claimant_category, right_type=filters.right_type,
            status=filters.status, year=filters.year, min_area_sqm=filters.min_area_sqm,
            max_area_sqm=filters.max_area_sqm, layers=("asset",),
        ),
        privileged=False,
    )
    asset_count = sum(
        feature["properties"]["kind"] == "asset" for feature in feature_collection["features"]
    )
    return AtlasSummary(
        village_count=len(matching_villages),
        claim_count=stats["total_claims"],
        title_count=stats["title_count"],
        asset_count=asset_count,
        claimed_area_sqm=stats["claimed_area_sqm"],
        granted_area_sqm=stats["granted_area_sqm"],
        granted_claim_count=stats["granted_claims"],
        rejected_claim_count=stats["rejected_claims"],
        pending_claim_count=stats["pending_claims"],
        by_right_type=stats["by_right_type"],
        by_status=stats["by_status"],
        by_district=dict(sorted(by_district.items())),
        progress=_progress_rows(scoped_claims, filters.state),
    )


def list_villages(session, filters: AtlasFilters) -> list[FRAVillageProfile]:
    return [
        village
        for village in session.scalars(
            select(FRAVillageProfile).order_by(
                FRAVillageProfile.district_name,
                FRAVillageProfile.block_name,
                FRAVillageProfile.village_name,
            )
        )
        if _village_matches(village, filters)
    ]


def village_detail(session, village_id) -> FRAVillageProfile | None:
    return session.get(FRAVillageProfile, village_id)
