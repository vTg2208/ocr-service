"""Versioned, privacy-minimized DSS facts derived from verified FRA records."""

from datetime import date, datetime, timezone

from sqlalchemy import or_, select
from shapely.geometry import shape

from app.db.fra_completion_models import AssetFeature, FRAVillageProfile
from app.db.fra_models import FRAClaim, FRATitle
from app.db.fra_operational_models import DSSFactSnapshot, ImageryArtifact, SpatialReferenceFeature
from app.db.models import User
from app.services.audit import record_audit
from app.services.asset_contracts import (
    asset_subtype,
    canonical_asset_class,
    cover_fraction,
    finite_number,
    observed_presence,
)
from app.services.village_asset_profiles import (
    CALCULATION_VERSION as VILLAGE_ASSET_CALCULATION_VERSION,
    claim_asset_context,
)


CURRENT_FACT_VERSION = "fra-dss-facts-v1"
MAX_OBSERVATION_AGE_DAYS = 730
ASSET_FACTS = {
    "agricultural_observation": {"agricultural_land"},
    "forest_cover_present": {"forest_cover"},
    "water_source_present": {"water_body"},
    "homestead_observation": {"homestead"},
    "road_access": {"road"},
}
DSS_FACT_CONTRACT = {
    "claim_right_type": "string",
    "claim_status": "string",
    "has_active_title": "boolean",
    "village_socioeconomic": "object",
    "water_source_present": "boolean",
    "homestead_observation": "boolean",
    "agricultural_observation": "boolean",
    "agricultural_land_fraction": "number",
    "forest_cover_present": "boolean",
    "groundwater_status": "string",
    "water_stress_status": "string",
    "road_access": "boolean",
    "infrastructure_services": "list",
    "mapped_asset_coverage_percent": "number",
    "assets_intersecting_fra_land_count": "number",
    "assets_near_fra_land_count": "number",
    "asset_deficiency_indicators": "list",
    "source_quality_flags": "object",
}
DSS_FACT_NAMES = frozenset(DSS_FACT_CONTRACT)
WATER_INFRASTRUCTURE_SUBTYPES = {
    "well", "open_well", "borewell", "pipeline", "tap_water", "water_tank",
    "rainwater_harvesting", "check_dam", "irrigation", "irrigation_canal",
}


def _iso(value) -> str | None:
    return value.isoformat() if isinstance(value, (date, datetime)) else None


def _known(value, *, entity_type: str, entity_id, source_version: str, observed_at=None, verification_state="verified"):
    source = {
        "source_entity_type": entity_type,
        "source_entity_id": str(entity_id),
        "source_version": source_version,
        "observed_at": _iso(observed_at),
        "verification_state": verification_state,
    }
    return {"value": value, **source}, source


def _unknown(reason: str):
    return {"value": "unknown", "reason": reason, "verification_state": "unavailable"}, {"reason": reason}


def _is_stale(observed_at) -> bool:
    if observed_at is None:
        return True
    observed_date = observed_at.date() if isinstance(observed_at, datetime) else observed_at
    return (date.today() - observed_date).days > MAX_OBSERVATION_AGE_DAYS


def _asset_fact(
    assets: list[AssetFeature], classes: set[str], *, numeric=False,
    infrastructure_subtypes: set[str] | None = None,
):
    relevant = [asset for asset in assets if canonical_asset_class(asset.asset_class) in classes]
    if infrastructure_subtypes:
        relevant.extend(
            asset for asset in assets
            if canonical_asset_class(asset.asset_class) == "infrastructure"
            and asset_subtype(asset.asset_class, asset.observed_value_json) in infrastructure_subtypes
            and asset not in relevant
        )
    if not relevant:
        return _unknown("no_verified_source")
    current = [asset for asset in relevant if not _is_stale(asset.acquired_at)]
    if not current:
        return _unknown("verified_source_stale")
    asset = max(current, key=lambda item: (
        item.acquired_at or date.min, _iso(item.created_at) or "", str(item.id)))
    source_version = str((asset.provenance_json or {}).get("source_version")
                         or (asset.provenance_json or {}).get("model_version") or asset.source_type)
    reason = "no_measured_fraction" if numeric else "no_explicit_presence"
    try:
        value = (cover_fraction(asset.asset_class, asset.observed_value_json) if numeric
                 else observed_presence(asset.asset_class, asset.observed_value_json))
    except ValueError:
        value, reason = None, "incompatible_observation"
    fact, source = _known(
        value if value is not None else "unknown", entity_type="asset_feature", entity_id=asset.id,
        source_version=source_version,
        observed_at=asset.acquired_at,
    )
    if value is None:
        fact["reason"] = source["reason"] = reason
    return fact, source


def _verified_water_absence(session, claim: FRAClaim):
    current_geometry = max(claim.geometry_versions, key=lambda item: item.version, default=None)
    if current_geometry is None:
        return None
    artifacts = session.scalars(
        select(ImageryArtifact).where(
            ImageryArtifact.claim_id == claim.id,
            ImageryArtifact.geometry_version_id == current_geometry.id,
            ImageryArtifact.state == "completed",
            ImageryArtifact.verification_state == "verified",
        ).order_by(ImageryArtifact.created_at.desc(), ImageryArtifact.id.desc())
    ).all()
    for artifact in artifacts:
        stats = dict(artifact.statistics_json or {})
        acquired = artifact.imagery_scene.acquired_at if artifact.imagery_scene else artifact.created_at
        if _is_stale(acquired):
            continue
        coverage = stats.get("observation_coverage")
        if finite_number(coverage) and .8 <= coverage <= 1 and isinstance(stats.get("water_source_present"), bool):
            return _known(
                stats["water_source_present"], entity_type="imagery_artifact",
                entity_id=artifact.id, source_version=artifact.processor_version,
                observed_at=acquired, verification_state=artifact.verification_state,
            )
    return None


def _safe_attributes(values: dict) -> dict:
    blocked = {"name", "phone", "mobile", "contact", "aadhaar", "address", "holder"}
    return {
        str(key): value
        for key, value in (values or {}).items()
        if not any(token in str(key).casefold() for token in blocked)
    }


def _reference_status(reference: SpatialReferenceFeature | None):
    if reference is None:
        return _unknown("no_published_reference")
    properties = _safe_attributes(reference.properties_json)
    value = next(
        (
            str(properties[key]).strip().casefold().replace(" ", "_")
            for key in (
                "groundwater_status",
                "water_stress_status",
                "stress_category",
                "category",
                "classification",
                "status",
            )
            if properties.get(key) not in (None, "")
        ),
        None,
    )
    if value is None and finite_number(properties.get("stress_score")):
        score = float(properties["stress_score"])
        value = (
            "critical"
            if score >= 0.8
            else "high"
            if score >= 0.6
            else "moderate"
            if score >= 0.4
            else "low"
        )
    if value is None:
        fact, source = _unknown("reference_status_missing")
        source.update(
            {
                "source_entity_type": "spatial_reference_feature",
                "source_entity_id": str(reference.id),
                "source_version": reference.source_version,
            }
        )
        return fact, source
    return _known(
        value,
        entity_type="spatial_reference_feature",
        entity_id=reference.id,
        source_version=reference.source_version,
        observed_at=reference.created_at,
    )


def validate_fact_contract(facts: dict) -> None:
    names = set(facts)
    missing = DSS_FACT_NAMES - names
    extra = names - DSS_FACT_NAMES
    if missing or extra:
        detail = []
        if missing:
            detail.append(f"missing: {', '.join(sorted(missing))}")
        if extra:
            detail.append(f"unsupported: {', '.join(sorted(extra))}")
        raise ValueError(f"DSS fact contract mismatch ({'; '.join(detail)}).")
    for name, expected in DSS_FACT_CONTRACT.items():
        item = facts[name]
        if not isinstance(item, dict) or "value" not in item:
            raise ValueError(f"DSS fact {name} must use the evidence envelope.")
        value = item["value"]
        if value == "unknown":
            continue
        valid = (
            isinstance(value, str)
            if expected == "string"
            else isinstance(value, bool)
            if expected == "boolean"
            else finite_number(value) and not isinstance(value, bool)
            if expected == "number"
            else isinstance(value, dict)
            if expected == "object"
            else isinstance(value, list)
        )
        if not valid:
            raise ValueError(f"DSS fact {name} must be {expected}.")


def _overlapping_record(records, geometry):
    if geometry is None:
        return None
    target = shape(geometry)
    matches = []
    for record in records:
        try:
            overlap = target.intersection(shape(record.boundary if hasattr(record, "boundary") else record.geometry)).area
        except (TypeError, ValueError):
            continue
        if overlap > 0:
            matches.append((overlap, record))
    return max(matches, key=lambda item: item[0])[1] if matches else None


def derive_facts(
    session,
    claim: FRAClaim,
    derivation_version: str,
    actor_id,
    idempotency_key: str,
    *,
    request_id: str | None = None,
) -> DSSFactSnapshot:
    version = derivation_version.strip()
    key = idempotency_key.strip()
    if not version or not key:
        raise ValueError("Derivation version and idempotency key are required.")
    if session.get(User, actor_id) is None:
        raise ValueError("The DSS fact actor does not exist.")
    existing = session.scalar(select(DSSFactSnapshot).where(
        DSSFactSnapshot.claim_id == claim.id,
        DSSFactSnapshot.derivation_version == version,
        DSSFactSnapshot.idempotency_key == key,
    ))
    if existing is not None:
        return existing
    if version != CURRENT_FACT_VERSION:
        raise ValueError(f"New derivations require {CURRENT_FACT_VERSION}; {version} is available only for existing snapshot replay.")

    facts, sources = {}, {}
    facts["claim_right_type"], sources["claim_right_type"] = _known(
        claim.right_type, entity_type="fra_claim", entity_id=claim.id,
        source_version="claim-record-v1", observed_at=claim.updated_at,
    )
    facts["claim_status"], sources["claim_status"] = _known(
        claim.status, entity_type="fra_claim", entity_id=claim.id,
        source_version="claim-record-v1", observed_at=claim.updated_at,
    )
    title = session.scalar(select(FRATitle).where(
        FRATitle.claim_id == claim.id, FRATitle.active.is_(True),
    ).order_by(FRATitle.version.desc()).limit(1))
    if title is None:
        facts["has_active_title"], sources["has_active_title"] = _known(
            False, entity_type="fra_claim", entity_id=claim.id,
            source_version="title-registry-v1", observed_at=claim.updated_at,
        )
    else:
        facts["has_active_title"], sources["has_active_title"] = _known(
            True, entity_type="fra_title", entity_id=title.id,
            source_version=f"title-v{title.version}", observed_at=title.issued_at,
        )

    geometry = max(claim.geometry_versions, key=lambda item: item.version).geometry if claim.geometry_versions else None
    village = claim.village or _overlapping_record(
        session.scalars(select(FRAVillageProfile)), geometry
    )
    if village is None:
        facts["village_socioeconomic"], sources["village_socioeconomic"] = _unknown("no_matching_village_profile")
    else:
        facts["village_socioeconomic"], sources["village_socioeconomic"] = _known(
            _safe_attributes(village.socioeconomic_json), entity_type="fra_village_profile",
            entity_id=village.id, source_version=village.reference_version,
            observed_at=village.updated_at,
        )

    groundwater_references = session.scalars(select(SpatialReferenceFeature).where(
        SpatialReferenceFeature.dataset_kind.in_({"groundwater", "groundwater_stress"}),
        SpatialReferenceFeature.published.is_(True),
    ))
    groundwater_reference = _overlapping_record(groundwater_references, geometry)
    facts["groundwater_status"], sources["groundwater_status"] = _reference_status(
        groundwater_reference
    )
    water_stress_references = session.scalars(select(SpatialReferenceFeature).where(
        SpatialReferenceFeature.dataset_kind == "water_stress",
        SpatialReferenceFeature.published.is_(True),
    ))
    water_stress_reference = _overlapping_record(water_stress_references, geometry)
    facts["water_stress_status"], sources["water_stress_status"] = _reference_status(
        water_stress_reference
    )

    asset_filters = [AssetFeature.claim_id == claim.id]
    if village is not None:
        asset_filters.append(AssetFeature.village_id == village.id)
    verified_assets = list(session.scalars(select(AssetFeature).where(
        AssetFeature.verification_state == "verified",
        or_(*asset_filters),
    )))
    for fact_name, classes in ASSET_FACTS.items():
        facts[fact_name], sources[fact_name] = _asset_fact(
            verified_assets,
            classes,
            infrastructure_subtypes=(
                WATER_INFRASTRUCTURE_SUBTYPES if fact_name == "water_source_present" else None
            ),
        )
    facts["agricultural_land_fraction"], sources["agricultural_land_fraction"] = _asset_fact(
        verified_assets, {"agricultural_land"}, numeric=True)
    if facts["water_source_present"]["value"] == "unknown":
        explicit_water = _verified_water_absence(session, claim)
        if explicit_water is not None:
            facts["water_source_present"], sources["water_source_present"] = explicit_water

    infrastructure_assets = [
        asset for asset in verified_assets
        if canonical_asset_class(asset.asset_class) in {"infrastructure", "road"}
        and not _is_stale(asset.acquired_at)
        and _asset_fact(
            [asset], {canonical_asset_class(asset.asset_class)}
        )[0]["value"] is True
    ]
    infrastructure = sorted({
        asset_subtype(asset.asset_class, asset.observed_value_json)
        for asset in infrastructure_assets
    })
    if infrastructure:
        representative = infrastructure_assets[0]
        facts["infrastructure_services"], sources["infrastructure_services"] = _known(
            infrastructure, entity_type="asset_feature_set", entity_id=representative.id,
            source_version="verified-assets-v2", observed_at=representative.acquired_at,
        )
    else:
        facts["infrastructure_services"], sources["infrastructure_services"] = _unknown("no_verified_current_source")

    if geometry is None:
        for name in (
            "mapped_asset_coverage_percent",
            "assets_intersecting_fra_land_count",
            "assets_near_fra_land_count",
            "asset_deficiency_indicators",
        ):
            facts[name], sources[name] = _unknown("claim_geometry_unavailable")
    else:
        context = claim_asset_context(session, claim)
        for name, context_key in (
            ("mapped_asset_coverage_percent", "mapped_asset_coverage_percent"),
            ("assets_intersecting_fra_land_count", "intersecting_asset_count"),
            ("assets_near_fra_land_count", "nearby_asset_count"),
            ("asset_deficiency_indicators", "asset_deficiency_indicators"),
        ):
            facts[name], sources[name] = _known(
                context[context_key],
                entity_type="fra_claim_asset_context",
                entity_id=claim.id,
                source_version=VILLAGE_ASSET_CALCULATION_VERSION,
                observed_at=datetime.now(timezone.utc),
            )

    unknown_facts = sorted(name for name, item in facts.items() if item.get("value") == "unknown")
    stale_facts = sorted(name for name, item in facts.items() if item.get("reason") == "verified_source_stale")
    facts["source_quality_flags"], sources["source_quality_flags"] = _known(
        {"unknown_facts": unknown_facts, "stale_facts": stale_facts},
        entity_type="dss_derivation", entity_id=claim.id, source_version=version,
        observed_at=datetime.now(timezone.utc),
    )
    validate_fact_contract(facts)

    snapshot = DSSFactSnapshot(
        claim_id=claim.id, derivation_version=version, idempotency_key=key,
        facts_json=facts, sources_json=sources, created_by=actor_id,
    )
    session.add(snapshot); session.flush()
    record_audit(
        session, actor_id=actor_id, action="dss_facts_derived",
        entity_type="fra_claim", entity_id=claim.id,
        after={"snapshot_id": str(snapshot.id), "derivation_version": version, "fact_names": sorted(facts)},
        request_id=request_id,
    )
    return snapshot


def fact_values(snapshot: DSSFactSnapshot) -> dict:
    if snapshot.derivation_version == CURRENT_FACT_VERSION:
        validate_fact_contract(snapshot.facts_json or {})
    return {
        name: None if item.get("value") == "unknown" else item.get("value")
        for name, item in (snapshot.facts_json or {}).items()
    }


__all__ = [
    "CURRENT_FACT_VERSION",
    "DSS_FACT_CONTRACT",
    "DSS_FACT_NAMES",
    "derive_facts",
    "fact_values",
    "validate_fact_contract",
]
