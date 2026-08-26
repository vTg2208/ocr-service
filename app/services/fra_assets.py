"""Versioned, non-adjudicative asset inference and human review."""

from datetime import date, datetime, timezone
import uuid

from sqlalchemy import select

from app.db.fra_completion_models import (
    AssetFeature,
    FRAVillageProfile,
    InferenceRun,
    ModelVersion,
    ProcessingJob,
)
from app.db.fra_models import FRAClaim
from app.db.models import User
from app.services.audit import record_audit
from app.services.model_gateway import validate_model_output
from app.services.processing_jobs import enqueue_job


class AssetValidationError(ValueError):
    pass


class AssetReviewConflict(RuntimeError):
    pass


def _target_geometry(session, *, village_id, claim_id):
    if (village_id is None) == (claim_id is None):
        raise AssetValidationError("Choose exactly one Tamil Nadu village or FRA claim target.")
    if village_id is not None:
        village = session.get(FRAVillageProfile, village_id)
        if village is None:
            raise AssetValidationError("FRA village profile does not exist.")
        if village.state_code != "TN":
            raise AssetValidationError("Asset inference currently supports only Tamil Nadu.")
        return "village", village.id, village.boundary
    claim = session.get(FRAClaim, claim_id)
    if claim is None:
        raise AssetValidationError("FRA claim does not exist.")
    if not claim.geometry_versions:
        raise AssetValidationError("The FRA claim requires a geometry version.")
    geometry = max(claim.geometry_versions, key=lambda item: item.version).geometry
    return "fra_claim", claim.id, geometry


def enqueue_asset_inference(
    session,
    *,
    village_id,
    claim_id,
    model_version_id,
    scene_id: str,
    actor_id,
    idempotency_key: str,
    manifest: dict,
) -> ProcessingJob:
    model = session.get(ModelVersion, model_version_id)
    if model is None:
        raise AssetValidationError("Model version does not exist.")
    if model.task != "asset_detection":
        raise AssetValidationError("The selected model is not an asset-detection model.")
    if model.status != "active":
        raise AssetValidationError("An active asset-detection model is required.")
    entity_type, entity_id, _geometry = _target_geometry(
        session, village_id=village_id, claim_id=claim_id
    )
    scene = " ".join(scene_id.split())
    if not scene:
        raise AssetValidationError("A scene ID is required.")
    validate_model_output(manifest)
    return enqueue_job(
        session,
        task_type="asset_inference",
        entity_type=entity_type,
        entity_id=entity_id,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        payload={
            "village_id": str(village_id) if village_id else None,
            "claim_id": str(claim_id) if claim_id else None,
            "model_version_id": str(model_version_id),
            "scene_id": scene,
            "manifest": dict(manifest),
        },
    )


def process_asset_inference(session, job: ProcessingJob, *, adapter) -> list[AssetFeature]:
    if job.task_type != "asset_inference":
        raise AssetValidationError("The job is not an asset-inference task.")
    existing_run = session.scalar(
        select(InferenceRun).where(InferenceRun.processing_job_id == job.id)
    )
    if existing_run is not None:
        return list(existing_run.assets)
    payload = dict(job.payload_json or {})
    model_identifier = payload.get("model_version_id")
    model = session.get(
        ModelVersion,
        uuid.UUID(model_identifier) if isinstance(model_identifier, str) else model_identifier,
    )
    if model is None:
        raise AssetValidationError("Model version does not exist.")
    if model.task != "asset_detection" or model.status != "active":
        raise AssetValidationError("An active asset-detection model is required.")
    if adapter.version != model.version:
        raise AssetValidationError("Adapter and registered model versions do not match.")
    village_identifier = payload.get("village_id")
    claim_identifier = payload.get("claim_id")
    entity_type, entity_id, geometry = _target_geometry(
        session,
        village_id=(
            uuid.UUID(village_identifier) if isinstance(village_identifier, str) else village_identifier
        ),
        claim_id=(uuid.UUID(claim_identifier) if isinstance(claim_identifier, str) else claim_identifier),
    )
    result = adapter.detect(payload.get("scene_id", ""), geometry, payload.get("manifest") or {})
    validate_model_output(result.features)
    acquired_at = None
    raw_date = (payload.get("manifest") or {}).get("acquired_at")
    if raw_date:
        try:
            acquired_at = date.fromisoformat(raw_date)
        except (TypeError, ValueError) as error:
            raise AssetValidationError("Manifest acquisition date must use ISO YYYY-MM-DD.") from error
    run = InferenceRun(
        model_version=model,
        processing_job=job,
        input_entity_type=entity_type,
        input_entity_id=entity_id,
        state="completed",
        input_json={
            "scene_id": payload["scene_id"],
            "target_type": entity_type,
            "target_id": str(entity_id),
        },
        output_json={"features": list(result.features)},
        confidence=result.confidence,
        processing_time_ms=result.processing_time_ms,
        provenance_json={
            **result.provenance,
            "legal_role": "supporting_observation",
            "model_name": model.name,
            "model_version": model.version,
        },
        completed_at=datetime.now(timezone.utc),
    )
    session.add(run)
    session.flush()
    assets: list[AssetFeature] = []
    for item in result.features:
        feature_geometry = item["geometry"]
        asset = AssetFeature(
            village_id=entity_id if entity_type == "village" else None,
            claim_id=entity_id if entity_type == "fra_claim" else None,
            asset_class=item["asset_class"],
            polygon_geometry=(feature_geometry if feature_geometry.get("type") == "MultiPolygon" else None),
            point_geometry_json=(feature_geometry if feature_geometry.get("type") == "Point" else None),
            observed_value_json=(
                dict(item["value"]) if isinstance(item.get("value"), dict) else {"value": item.get("value")}
            ),
            acquired_at=acquired_at,
            confidence=item["confidence"],
            inference_run=run,
            source_type="model",
            source_reference=payload["scene_id"],
            provenance_json={
                **result.provenance,
                "model_name": model.name,
                "model_version": model.version,
                "legal_role": "supporting_observation",
            },
            verification_state="unverified",
            synthetic=bool(result.provenance.get("synthetic")),
        )
        session.add(asset)
        assets.append(asset)
    session.flush()
    record_audit(
        session,
        actor_id=job.requested_by,
        action="fra_asset_inference_completed",
        entity_type=entity_type,
        entity_id=entity_id,
        after={
            "inference_run_id": str(run.id),
            "asset_count": len(assets),
            "legal_role": "supporting_observation",
        },
    )
    return assets


def review_asset(
    session,
    asset: AssetFeature,
    *,
    outcome: str,
    reviewer_id,
    reasons: list[str],
    expected_revision: int,
    corrected_value: dict | None = None,
    corrected_geometry: dict | None = None,
    request_id: str | None = None,
) -> AssetFeature:
    reviewer = session.get(User, reviewer_id)
    if reviewer is None or reviewer.role not in {"reviewer", "admin"}:
        raise PermissionError("Asset review requires a reviewer or admin.")
    if asset.revision != expected_revision:
        raise AssetReviewConflict("The asset changed since it was loaded.")
    if asset.verification_state not in {"unverified", "verified"}:
        raise AssetReviewConflict("The asset is no longer reviewable.")
    outcome = outcome.strip().casefold()
    if outcome not in {"verified", "rejected", "corrected"}:
        raise AssetValidationError("Asset review outcome must be verified, rejected, or corrected.")
    normalized_reasons = [str(reason).strip() for reason in reasons if str(reason).strip()]
    if outcome in {"rejected", "corrected"} and not normalized_reasons:
        raise AssetValidationError("A reason is required for rejection or correction.")
    now = datetime.now(timezone.utc)
    if outcome != "corrected":
        asset.verification_state = outcome
        asset.verification_reasons_json = normalized_reasons
        asset.verified_by = reviewer_id
        asset.verified_at = now
        asset.revision += 1
        result = asset
    else:
        if not isinstance(corrected_value, dict):
            raise AssetValidationError("A corrected observed value is required.")
        validate_model_output(corrected_value)
        polygon = asset.polygon_geometry
        point = asset.point_geometry_json
        if corrected_geometry is not None:
            if corrected_geometry.get("type") == "MultiPolygon":
                polygon, point = corrected_geometry, None
            elif corrected_geometry.get("type") == "Point":
                point, polygon = corrected_geometry, None
            else:
                raise AssetValidationError("Corrected geometry must be a Point or MultiPolygon.")
        asset.verification_state = "superseded"
        asset.verification_reasons_json = normalized_reasons
        asset.verified_by = reviewer_id
        asset.verified_at = now
        asset.revision += 1
        result = AssetFeature(
            village_id=asset.village_id,
            claim_id=asset.claim_id,
            asset_class=asset.asset_class,
            polygon_geometry=polygon,
            point_geometry_json=point,
            observed_value_json=dict(corrected_value),
            acquired_at=asset.acquired_at,
            confidence=None,
            source_type="manual_correction",
            provenance_json={
                "source": "human_review",
                "superseded_asset_id": str(asset.id),
                "synthetic": asset.synthetic,
            },
            verification_state="verified",
            verification_reasons_json=normalized_reasons,
            verified_by=reviewer_id,
            verified_at=now,
            supersedes=asset,
            synthetic=asset.synthetic,
        )
        session.add(result)
    session.flush()
    record_audit(
        session,
        actor_id=reviewer_id,
        action="fra_asset_reviewed",
        entity_type="asset_feature",
        entity_id=asset.id,
        before={"verification_state": "unverified", "revision": expected_revision},
        after={
            "verification_state": asset.verification_state,
            "outcome": outcome,
            "superseding_asset_id": str(result.id) if result.id != asset.id else None,
        },
        request_id=request_id,
    )
    return result


def list_assets(
    session,
    *,
    district: str | None = None,
    block: str | None = None,
    village: str | None = None,
    claim_id=None,
    asset_class: str | None = None,
    verification_state: str | None = None,
) -> list[AssetFeature]:
    assets = session.scalars(select(AssetFeature).order_by(AssetFeature.created_at.desc(), AssetFeature.id)).all()
    result = []
    for asset in assets:
        if claim_id and asset.claim_id != claim_id:
            continue
        if asset_class and asset.asset_class != asset_class:
            continue
        if verification_state and asset.verification_state != verification_state:
            continue
        if district and (asset.village is None or asset.village.district_name.casefold() != district.casefold()):
            continue
        if block and (asset.village is None or asset.village.block_name.casefold() != block.casefold()):
            continue
        if village and (asset.village is None or asset.village.village_name.casefold() != village.casefold()):
            continue
        result.append(asset)
    return result
