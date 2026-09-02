"""Versioned, privacy-minimized DSS facts derived from verified FRA records."""

from datetime import date, datetime, timezone

from sqlalchemy import or_, select
from shapely.geometry import shape

from app.db.fra_completion_models import AssetFeature, FRAVillageProfile
from app.db.fra_models import FRAClaim, FRATitle
from app.db.fra_operational_models import DSSFactSnapshot, ImageryArtifact, SpatialReferenceFeature
from app.db.models import User
from app.services.audit import record_audit


CURRENT_FACT_VERSION = "tn-facts-v1"
MAX_OBSERVATION_AGE_DAYS = 730
ASSET_FACTS = {
    "agricultural_observation": {"agricultural_land", "cropland", "agriculture"},
    "forest_observation": {"forest_cover", "forest", "tree_cover"},
    "water_source_present": {"water_body", "well", "pipeline", "water_source"},
    "homestead_observation": {"homestead", "built_up", "house"},
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


def _asset_fact(assets: list[AssetFeature], classes: set[str]):
    relevant = [asset for asset in assets if asset.asset_class.casefold() in classes]
    if not relevant:
        return _unknown("no_verified_source")
    current = [asset for asset in relevant if not _is_stale(asset.acquired_at)]
    if not current:
        return _unknown("verified_source_stale")
    asset = max(current, key=lambda item: item.acquired_at or date.min)
    value = (asset.observed_value_json or {}).get("present", True)
    return _known(
        bool(value), entity_type="asset_feature", entity_id=asset.id,
        source_version=str((asset.provenance_json or {}).get("source_version") or asset.source_type),
        observed_at=asset.acquired_at,
    )


def _verified_water_absence(session, claim: FRAClaim):
    artifacts = session.scalars(
        select(ImageryArtifact).where(
            ImageryArtifact.claim_id == claim.id,
            ImageryArtifact.state == "completed",
            ImageryArtifact.verification_state == "verified",
        ).order_by(ImageryArtifact.created_at.desc())
    ).all()
    for artifact in artifacts:
        stats = dict(artifact.statistics_json or {})
        acquired = artifact.imagery_scene.acquired_at if artifact.imagery_scene else artifact.created_at
        if _is_stale(acquired):
            continue
        if stats.get("observation_coverage", 0) >= 0.8 and isinstance(stats.get("water_source_present"), bool):
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
    if version != CURRENT_FACT_VERSION:
        raise ValueError(f"Unsupported DSS fact derivation version: {version}.")
    if session.get(User, actor_id) is None:
        raise ValueError("The DSS fact actor does not exist.")
    existing = session.scalar(select(DSSFactSnapshot).where(
        DSSFactSnapshot.claim_id == claim.id,
        DSSFactSnapshot.derivation_version == version,
        DSSFactSnapshot.idempotency_key == key,
    ))
    if existing is not None:
        return existing

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
    village = _overlapping_record(session.scalars(select(FRAVillageProfile)), geometry)
    if village is None:
        facts["village_socioeconomic"], sources["village_socioeconomic"] = _unknown("no_matching_village_profile")
    else:
        facts["village_socioeconomic"], sources["village_socioeconomic"] = _known(
            _safe_attributes(village.socioeconomic_json), entity_type="fra_village_profile",
            entity_id=village.id, source_version=village.reference_version,
            observed_at=village.updated_at,
        )

    water_references = session.scalars(select(SpatialReferenceFeature).where(
        SpatialReferenceFeature.dataset_kind.in_(("water_stress", "groundwater", "groundwater_stress")),
        SpatialReferenceFeature.published.is_(True),
    ))
    water_reference = _overlapping_record(water_references, geometry)
    if water_reference is None:
        facts["water_stress_reference"], sources["water_stress_reference"] = _unknown("no_published_reference")
    else:
        facts["water_stress_reference"], sources["water_stress_reference"] = _known(
            _safe_attributes(water_reference.properties_json), entity_type="spatial_reference_feature",
            entity_id=water_reference.id, source_version=water_reference.source_version,
            observed_at=water_reference.created_at,
        )

    asset_filters = [AssetFeature.claim_id == claim.id]
    if village is not None:
        asset_filters.append(AssetFeature.village_id == village.id)
    verified_assets = list(session.scalars(select(AssetFeature).where(
        AssetFeature.verification_state == "verified",
        or_(*asset_filters),
    )))
    for fact_name, classes in ASSET_FACTS.items():
        facts[fact_name], sources[fact_name] = _asset_fact(verified_assets, classes)
    if facts["water_source_present"]["value"] == "unknown":
        explicit_water = _verified_water_absence(session, claim)
        if explicit_water is not None:
            facts["water_source_present"], sources["water_source_present"] = explicit_water

    infrastructure_classes = {
        "well", "pipeline", "road", "school", "health_centre", "electricity",
        "irrigation", "community_building",
    }
    infrastructure = sorted({
        asset.asset_class for asset in verified_assets
        if asset.asset_class.casefold() in infrastructure_classes and not _is_stale(asset.acquired_at)
    })
    if infrastructure:
        representative = next(asset for asset in verified_assets if asset.asset_class in infrastructure)
        facts["infrastructure_services"], sources["infrastructure_services"] = _known(
            infrastructure, entity_type="asset_feature_set", entity_id=representative.id,
            source_version="verified-assets-v1", observed_at=representative.acquired_at,
        )
    else:
        facts["infrastructure_services"], sources["infrastructure_services"] = _unknown("no_verified_current_source")

    unknown_facts = sorted(name for name, item in facts.items() if item.get("value") == "unknown")
    stale_facts = sorted(name for name, item in facts.items() if item.get("reason") == "verified_source_stale")
    facts["source_quality_flags"], sources["source_quality_flags"] = _known(
        {"unknown_facts": unknown_facts, "stale_facts": stale_facts},
        entity_type="dss_derivation", entity_id=claim.id, source_version=version,
        observed_at=datetime.now(timezone.utc),
    )

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
    return {
        name: None if item.get("value") == "unknown" else item.get("value")
        for name, item in (snapshot.facts_json or {}).items()
    }


__all__ = ["CURRENT_FACT_VERSION", "derive_facts", "fact_values"]
