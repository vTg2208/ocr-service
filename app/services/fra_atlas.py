"""Tamil Nadu reference-data import and privacy-safe FRA Atlas queries."""

from dataclasses import dataclass, field
from typing import Any

from shapely.geometry import shape
from sqlalchemy import select

from app.db.fra_completion_models import AssetFeature, FRAVillageProfile
from app.db.fra_models import FRAClaim, FRATitle
from app.db.models import User
from app.models.fra_schemas import normalize_geometry
from app.services.audit import record_audit
from app.services.fra_spatial_policy import _area_sqm
from app.services.state_profiles import get_state_profile


ATLAS_LAYERS = {"village", "claim", "title", "asset"}


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
    right_type: str | None = None
    status: str | None = None
    year: int | None = None
    layers: tuple[str, ...] = field(default_factory=lambda: tuple(sorted(ATLAS_LAYERS)))

    def __post_init__(self):
        get_state_profile(self.state)
        invalid = set(self.layers) - ATLAS_LAYERS
        if invalid:
            raise AtlasValidationError(f"Unsupported Atlas layer: {sorted(invalid)[0]}.")
        if self.year is not None and not 1900 <= self.year <= 2100:
            raise AtlasValidationError("Atlas year is invalid.")


@dataclass(frozen=True)
class AtlasSummary:
    village_count: int
    claim_count: int
    title_count: int
    asset_count: int
    claimed_area_sqm: float
    by_right_type: dict[str, int]
    by_status: dict[str, int]
    by_district: dict[str, int]


def _normalized_multipolygon(raw: Any) -> dict:
    try:
        geometry = normalize_geometry(raw)
        parsed = shape(geometry)
    except (TypeError, ValueError) as error:
        raise AtlasValidationError(str(error)) from error
    min_x, min_y, max_x, max_y = parsed.bounds
    if min_x < 76 or max_x > 81 or min_y < 8 or max_y > 14:
        raise AtlasValidationError("Village geometry must fall within the Tamil Nadu demo extent.")
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
    if metadata.get("synthetic") is not True:
        raise AtlasValidationError("This importer requires visibly synthetic reference data.")
    source = _required_text(metadata, "source")
    version = _required_text(metadata, "version")
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
                "synthetic": True,
                "source": source,
                "version": version,
            },
            "reference_version": version,
            "synthetic": True,
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
            "synthetic": True,
        },
    )
    return VillageImportReport(inserted, updated, unchanged, version)


def _matches(value: str | None, expected: str | None) -> bool:
    return expected is None or (value or "").casefold() == expected.casefold()


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


def _claim_gram_sabha(claim: FRAClaim):
    return claim.gram_sabha or claim.rights_holder.gram_sabha


def _claim_matches(claim: FRAClaim, filters: AtlasFilters, villages: list[FRAVillageProfile]) -> bool:
    gram_sabha = _claim_gram_sabha(claim)
    if gram_sabha is None or (gram_sabha.state or "Tamil Nadu").casefold() != "tamil nadu":
        return False
    if not _matches(gram_sabha.district, filters.district):
        return False
    if not _matches(gram_sabha.block, filters.block):
        return False
    if not _matches(gram_sabha.village, filters.village):
        return False
    if filters.right_type and claim.right_type != filters.right_type.upper():
        return False
    if filters.status and claim.status.casefold() != filters.status.casefold():
        return False
    if filters.year and claim.created_at.year != filters.year:
        return False
    if filters.tribal_group:
        village = next(
            (
                item
                for item in villages
                if item.village_name.casefold() == (gram_sabha.village or "").casefold()
                and item.block_name.casefold() == (gram_sabha.block or "").casefold()
            ),
            None,
        )
        if village is None or not _village_matches(village, filters):
            return False
    return True


def _current_geometry(claim: FRAClaim):
    return max(claim.geometry_versions, key=lambda item: item.version).geometry if claim.geometry_versions else None


def atlas_features(session, filters: AtlasFilters, *, privileged: bool) -> dict:
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
            features.append(
                {"type": "Feature", "id": str(claim.id), "geometry": geometry, "properties": properties}
            )
    if "title" in filters.layers:
        for claim in scoped_claims:
            for title in sorted(claim.titles, key=lambda item: (item.version, str(item.id))):
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
    kind_order = {"village": 0, "claim": 1, "title": 2, "asset": 3}
    features.sort(key=lambda item: (kind_order[item["properties"]["kind"]], str(item["id"])))
    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "state_code": "TN",
            "synthetic_warning": "Synthetic demonstration data are not authoritative.",
        },
    }


def atlas_summary(session, filters: AtlasFilters) -> AtlasSummary:
    feature_collection = atlas_features(session, filters, privileged=False)
    counts = {kind: 0 for kind in ATLAS_LAYERS}
    claimed_area = 0.0
    by_right: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_district: dict[str, int] = {}
    for feature in feature_collection["features"]:
        properties = feature["properties"]
        kind = properties["kind"]
        counts[kind] += 1
        if kind == "claim":
            claimed_area += float(properties.get("area_sqm") or 0)
            by_right[properties["right_type"]] = by_right.get(properties["right_type"], 0) + 1
            by_status[properties["status"]] = by_status.get(properties["status"], 0) + 1
        elif kind == "village":
            district = properties["district"]
            by_district[district] = by_district.get(district, 0) + 1
    return AtlasSummary(
        village_count=counts["village"],
        claim_count=counts["claim"],
        title_count=counts["title"],
        asset_count=counts["asset"],
        claimed_area_sqm=round(claimed_area, 2),
        by_right_type=dict(sorted(by_right.items())),
        by_status=dict(sorted(by_status.items())),
        by_district=dict(sorted(by_district.items())),
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
