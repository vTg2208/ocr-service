"""Persist privacy-safe planning profiles derived from reviewed village assets."""

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json

from shapely.geometry import shape
from shapely.ops import unary_union
from sqlalchemy import select

from app.db.fra_completion_models import AssetFeature, FRAVillageProfile, VillageAssetProfile
from app.db.fra_models import FRAClaim
from app.services.asset_contracts import (
    ASSET_TAXONOMY_VERSION,
    asset_subtype,
    cover_fraction,
    finite_number,
    observed_presence,
)
from app.services.audit import record_audit
from app.services.fra_locations import location_matches, village_location
from app.services.fra_spatial_policy import _area_sqm, _project


CALCULATION_VERSION = "village-assets-v2"
NEAR_FRA_DISTANCE_METRES = 1_000
LOW_COVERAGE_PERCENT = 10
SATELLITE_SOURCE_TYPES = {"model", "manual_correction"}


def _area(asset: AssetFeature, village_area: float) -> float:
    values = dict(asset.observed_value_json or {})
    measured = values.get("area_sqm")
    if finite_number(measured) and measured >= 0:
        return float(measured)
    if asset.polygon_geometry:
        return _area_sqm(asset.polygon_geometry)
    try:
        fraction = cover_fraction(asset.asset_class, values)
    except ValueError:
        fraction = None
    return village_area * fraction if fraction is not None else 0.0


def _is_present(asset: AssetFeature) -> bool:
    try:
        return observed_presence(asset.asset_class, asset.observed_value_json) is True
    except ValueError:
        return False


def _asset_geometry(asset: AssetFeature):
    raw = asset.polygon_geometry or asset.point_geometry_json
    if raw is None:
        return None
    try:
        parsed = shape(raw)
    except (TypeError, ValueError):
        return None
    return parsed if not parsed.is_empty else None


def _claim_geometry(claim: FRAClaim):
    if not claim.geometry_versions:
        return None
    return shape(max(claim.geometry_versions, key=lambda item: item.version).geometry)


def _distance_metres(left, right) -> float:
    latitude = (left.centroid.y + right.centroid.y) / 2
    return float(_project(left, latitude).distance(_project(right, latitude)))


def _is_satellite_asset(asset: AssetFeature) -> bool:
    return (
        asset.source_type in SATELLITE_SOURCE_TYPES
        and asset.verification_state not in {"rejected", "superseded"}
    )


def _claims_for_village(
    village: FRAVillageProfile, claims: list[FRAClaim]
) -> list[FRAClaim]:
    village_shape = shape(village.boundary)
    result = []
    for claim in claims:
        geometry = _claim_geometry(claim)
        if claim.village_id == village.id or (
            geometry is not None and geometry.intersects(village_shape)
        ):
            result.append(claim)
    return result


def _assets_for_village(
    village: FRAVillageProfile,
    assets: list[AssetFeature],
    village_claims: list[FRAClaim],
) -> list[AssetFeature]:
    village_shape = shape(village.boundary)
    claim_ids = {claim.id for claim in village_claims}
    result = []
    for asset in assets:
        if asset.village_id is not None:
            if asset.village_id == village.id:
                result.append(asset)
            continue
        if asset.claim_id is not None:
            if asset.claim_id in claim_ids:
                result.append(asset)
            continue
        geometry = _asset_geometry(asset)
        if geometry is not None and geometry.intersects(village_shape):
            result.append(asset)
    return result


def _deficiency_indicators(
    verified_assets: list[AssetFeature],
    *,
    coverage_percent: float,
    intersecting_count: int,
    has_fra_claims: bool,
) -> list[str]:
    classes = {asset.asset_class for asset in verified_assets if _is_present(asset)}
    indicators = []
    if not verified_assets:
        indicators.append("no_verified_satellite_assets")
    if "water_body" not in classes:
        indicators.append("water_asset_observation_gap")
    if "road" not in classes:
        indicators.append("road_asset_observation_gap")
    if "infrastructure" not in classes:
        indicators.append("infrastructure_asset_observation_gap")
    if coverage_percent < LOW_COVERAGE_PERCENT:
        indicators.append("low_mapped_asset_coverage")
    if has_fra_claims and intersecting_count == 0:
        indicators.append("no_verified_assets_intersect_fra_land")
    return indicators


def _village_spatial_metrics(
    village: FRAVillageProfile,
    assets: list[AssetFeature],
    claims: list[FRAClaim],
) -> dict:
    village_shape = shape(village.boundary)
    verified = [
        asset
        for asset in assets
        if _is_satellite_asset(asset) and asset.verification_state == "verified"
    ]
    pending_count = sum(
        _is_satellite_asset(asset) and asset.verification_state != "verified"
        for asset in assets
    )
    claim_shapes = [
        geometry
        for claim in claims
        if (geometry := _claim_geometry(claim)) is not None
    ]
    within_count = intersecting_count = near_count = 0
    polygon_coverage = []
    for asset in verified:
        geometry = _asset_geometry(asset)
        if geometry is None:
            continue
        if village_shape.covers(geometry):
            within_count += 1
        intersects = any(geometry.intersects(claim) for claim in claim_shapes)
        if intersects:
            intersecting_count += 1
        elif claim_shapes and min(
            _distance_metres(geometry, claim) for claim in claim_shapes
        ) <= NEAR_FRA_DISTANCE_METRES:
            near_count += 1
        if geometry.geom_type in {"Polygon", "MultiPolygon"}:
            clipped = geometry.intersection(village_shape)
            if not clipped.is_empty:
                polygon_coverage.append(clipped)
    mapped_area = (
        _area_sqm(unary_union(polygon_coverage).__geo_interface__)
        if polygon_coverage
        else 0.0
    )
    village_area = _area_sqm(village.boundary)
    coverage_percent = min(
        100.0, mapped_area / village_area * 100 if village_area else 0.0
    )
    return {
        "satellite_asset_count": len(verified) + pending_count,
        "verified_satellite_asset_count": len(verified),
        "pending_satellite_asset_count": pending_count,
        "assets_within_village_count": within_count,
        "assets_intersecting_fra_land_count": intersecting_count,
        "assets_near_fra_land_count": near_count,
        "mapped_asset_coverage_sqm": round(mapped_area, 2),
        "mapped_asset_coverage_percent": round(coverage_percent, 4),
        "near_fra_distance_metres": NEAR_FRA_DISTANCE_METRES,
        "fra_claim_count": len(claims),
        "active_title_count": sum(
            title.active for claim in claims for title in claim.titles
        ),
        "asset_deficiency_indicators": _deficiency_indicators(
            verified,
            coverage_percent=coverage_percent,
            intersecting_count=intersecting_count,
            has_fra_claims=bool(claim_shapes),
        ),
        "deficiency_basis": "verified_satellite_observations_only",
        "legal_role": "supporting_analytical_information",
    }


def calculate_village_asset_profile(
    village: FRAVillageProfile,
    assets: list[AssetFeature],
    claims: list[FRAClaim] | None = None,
) -> tuple[dict, list[dict]]:
    active = [asset for asset in assets if asset.verification_state not in {"rejected", "superseded"}]
    verified = [asset for asset in active if asset.verification_state == "verified"]
    pending = [asset for asset in active if asset.verification_state != "verified"]
    village_area = _area_sqm(village.boundary)
    by_class = {name: [item for item in verified if item.asset_class == name] for name in (
        "agricultural_land", "forest_cover", "water_body", "homestead",
        "road", "infrastructure", "other_asset",
    )}
    confidences = [float(item.confidence) for item in verified if item.confidence is not None]
    dates = sorted({item.acquired_at.isoformat() for item in verified if item.acquired_at})
    infrastructure_types = sorted({
        asset_subtype(item.asset_class, item.observed_value_json)
        for item in by_class["infrastructure"] if _is_present(item)
    })
    other = Counter(
        asset_subtype(item.asset_class, item.observed_value_json)
        for item in by_class["other_asset"] if _is_present(item)
    )
    metrics = {
        "agricultural_area_sqm": round(sum(_area(item, village_area) for item in by_class["agricultural_land"]), 2),
        "forest_area_sqm": round(sum(_area(item, village_area) for item in by_class["forest_cover"]), 2),
        "water_body_count": sum(_is_present(item) for item in by_class["water_body"]),
        "water_body_area_sqm": round(sum(_area(item, village_area) for item in by_class["water_body"]), 2),
        "homestead_count": sum(_is_present(item) for item in by_class["homestead"]),
        "road_available": any(_is_present(item) for item in by_class["road"]),
        "infrastructure_available": bool(infrastructure_types),
        "infrastructure_types": infrastructure_types,
        "other_detected_assets": dict(sorted(other.items())),
        "asset_confidence_mean": round(sum(confidences) / len(confidences), 5) if confidences else None,
        "imagery_dates": dates,
        "latest_imagery_date": dates[-1] if dates else None,
        **_village_spatial_metrics(village, active, list(claims or [])),
    }
    sources = []
    seen = set()
    for asset in verified:
        provenance = dict(asset.provenance_json or {})
        item = {
            "source_type": asset.source_type,
            "source_reference": asset.source_reference,
            "acquired_at": asset.acquired_at.isoformat() if asset.acquired_at else None,
            "model_name": provenance.get("model_name"),
            "model_version": provenance.get("model_version"),
        }
        key = json.dumps(item, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            sources.append(item)
    return metrics, sorted(sources, key=lambda item: json.dumps(item, sort_keys=True, default=str))


def refresh_village_asset_profiles(session, *, actor_id, request_id=None) -> list[VillageAssetProfile]:
    profiles = []
    villages = session.scalars(select(FRAVillageProfile).order_by(FRAVillageProfile.village_code)).all()
    all_assets = list(session.scalars(select(AssetFeature)))
    all_claims = list(session.scalars(select(FRAClaim)))
    for village in villages:
        claims = _claims_for_village(village, all_claims)
        assets = _assets_for_village(village, all_assets, claims)
        metrics, sources = calculate_village_asset_profile(village, assets, claims)
        active_count = sum(item.verification_state not in {"rejected", "superseded"} for item in assets)
        verified_count = sum(item.verification_state == "verified" for item in assets)
        pending_count = active_count - verified_count
        digest = hashlib.sha256(json.dumps(
            {"metrics": metrics, "sources": sources, "active_count": active_count,
             "verified_count": verified_count, "pending_count": pending_count},
            sort_keys=True, separators=(",", ":"), default=str,
        ).encode()).hexdigest()
        profile = session.scalar(select(VillageAssetProfile).where(
            VillageAssetProfile.village_id == village.id
        ))
        changed = profile is None or profile.source_digest != digest
        if profile is None:
            profile = VillageAssetProfile(village=village, generated_by=actor_id)
            session.add(profile)
        if changed:
            profile.taxonomy_version = ASSET_TAXONOMY_VERSION
            profile.calculation_version = CALCULATION_VERSION
            profile.source_digest = digest
            profile.source_asset_count = active_count
            profile.verified_asset_count = verified_count
            profile.pending_asset_count = pending_count
            profile.metrics_json = metrics
            profile.sources_json = sources
            profile.generated_by = actor_id
            profile.generated_at = datetime.now(timezone.utc)
            session.flush()
            record_audit(
                session, actor_id=actor_id, action="fra_village_asset_profile_refreshed",
                entity_type="fra_village_profile", entity_id=village.id,
                after={"asset_profile_id": str(profile.id), "source_digest": digest,
                       "verified_asset_count": verified_count}, request_id=request_id,
            )
        profiles.append(profile)
    session.flush()
    return profiles


def claim_asset_context(session, claim: FRAClaim) -> dict:
    claim_shape = _claim_geometry(claim)
    if claim_shape is None:
        raise ValueError("The FRA claim requires a current geometry.")
    villages = list(session.scalars(select(FRAVillageProfile)))
    village = claim.village or next(
        (
            item
            for item in villages
            if shape(item.boundary).intersects(claim_shape)
        ),
        None,
    )
    all_assets = list(session.scalars(select(AssetFeature)))
    candidates = (
        _assets_for_village(
            village,
            all_assets,
            _claims_for_village(
                village, list(session.scalars(select(FRAClaim)))
            ),
        )
        if village is not None
        else [asset for asset in all_assets if asset.claim_id == claim.id]
    )
    verified = [
        asset
        for asset in candidates
        if _is_satellite_asset(asset) and asset.verification_state == "verified"
    ]
    links = []
    coverage = []
    for asset in verified:
        geometry = _asset_geometry(asset)
        if geometry is None:
            continue
        distance = 0.0 if geometry.intersects(claim_shape) else _distance_metres(
            geometry, claim_shape
        )
        relation = (
            "intersects_fra_land"
            if distance == 0
            else "near_fra_land"
            if distance <= NEAR_FRA_DISTANCE_METRES
            else "within_village"
            if village is not None and shape(village.boundary).covers(geometry)
            else "outside_analysis_distance"
        )
        if relation == "intersects_fra_land" and geometry.geom_type in {
            "Polygon",
            "MultiPolygon",
        }:
            clipped = geometry.intersection(claim_shape)
            if not clipped.is_empty:
                coverage.append(clipped)
        links.append(
            {
                "asset_id": str(asset.id),
                "asset_class": asset.asset_class,
                "relation": relation,
                "distance_to_fra_metres": round(distance, 2),
                "verification_state": asset.verification_state,
                "acquired_at": asset.acquired_at.isoformat()
                if asset.acquired_at
                else None,
            }
        )
    mapped_area = (
        _area_sqm(unary_union(coverage).__geo_interface__) if coverage else 0.0
    )
    claim_area = _area_sqm(claim_shape.__geo_interface__)
    coverage_percent = min(
        100.0, mapped_area / claim_area * 100 if claim_area else 0.0
    )
    intersecting_count = sum(
        item["relation"] == "intersects_fra_land" for item in links
    )
    return {
        "claim_id": str(claim.id),
        "claim_number": claim.claim_number,
        "right_type": claim.right_type,
        "village": (
            {
                "id": str(village.id),
                "name": village.village_name,
                "block": village.block_name,
                "district": village.district_name,
            }
            if village is not None
            else None
        ),
        "active_titles": [
            {"id": str(title.id), "title_number": title.title_number}
            for title in claim.titles
            if title.active
        ],
        "verified_satellite_asset_count": len(verified),
        "intersecting_asset_count": intersecting_count,
        "nearby_asset_count": sum(
            item["relation"] == "near_fra_land" for item in links
        ),
        "mapped_asset_coverage_sqm": round(mapped_area, 2),
        "mapped_asset_coverage_percent": round(coverage_percent, 4),
        "asset_deficiency_indicators": _deficiency_indicators(
            verified,
            coverage_percent=coverage_percent,
            intersecting_count=intersecting_count,
            has_fra_claims=True,
        ),
        "assets": links,
        "near_fra_distance_metres": NEAR_FRA_DISTANCE_METRES,
        "deficiency_basis": "verified_satellite_observations_only",
        "legal_role": "supporting_analytical_information",
        "warning": (
            "Satellite observations support planning and do not determine "
            "FRA rights or legal validity."
        ),
    }


def list_village_asset_profiles(session, *, district=None, block=None, village=None):
    profiles = session.scalars(
        select(VillageAssetProfile).join(VillageAssetProfile.village)
        .order_by(FRAVillageProfile.district_name, FRAVillageProfile.block_name,
                  FRAVillageProfile.village_name)
    ).all()
    return [item for item in profiles if location_matches(
        village_location(item.village), district=district, block=block, village=village
    )]
