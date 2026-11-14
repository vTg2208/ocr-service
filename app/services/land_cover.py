"""Boundary -> Sentinel-2 -> model -> private results -> explicit human review."""

from datetime import date, datetime, timezone
import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import select

from app.api.fra_access import claim_for_user
from app.config import get_settings
from app.db.community_models import PlotSubmission, UserResource
from app.db.fra_completion_models import AssetFeature, ProcessingJob
from app.db.fra_models import FRAClaim
from app.db.land_cover_models import LandCoverAnalysis
from app.db.models import utcnow
from app.services.audit import record_audit
from app.services.concurrency import reserve_revision
from app.services.land_cover_raster import (
    BAND_KEYS, MODEL_VERSION, SOURCE_REVISION, WEIGHTS_SHA256, DynamicWorldPredictor,
    LandCoverError, SentinelLandCoverReader, encode_preview, encode_raster, summarize,
)
from app.services.processing_jobs import enqueue_job, JobExecutionError
from app.services.stac_imagery import STACClient, STACProviderError
from app.services.storage import create_storage


WARNING = "Satellite land cover is supporting evidence; human review does not establish ownership or scheme eligibility."


def target_for_actor(db, target_type, target_id, user):
    if target_type == "fra_claim":
        return claim_for_user(db, target_id, user)
    if target_type != "plot_submission":
        raise HTTPException(404, "Analysis target not found.")
    row = db.get(PlotSubmission, target_id)
    if row is None or (user.role not in {"reviewer", "admin"} and row.owner_id != user.id):
        raise HTTPException(404, "Submission not found.")
    return row


def target_snapshot(target):
    if isinstance(target, FRAClaim):
        if not target.geometry_versions:
            raise LandCoverError("Map the FRA case boundary before requesting analysis.")
        version = max(target.geometry_versions, key=lambda item: item.version)
        return version.geometry, str(version.id)
    if target.status != "approved":
        raise LandCoverError("Only an approved boundary snapshot can be analysed.")
    return target.geometry, str(target.plot_revision)


def request_analysis(db, *, target_type, target, actor_id, start_date, end_date):
    geometry, version = target_snapshot(target)
    if start_date > end_date or end_date > datetime.now(timezone.utc).date() or (end_date-start_date).days > 93:
        raise LandCoverError("Choose a past date range of at most 93 days.")
    parameters = {"start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
                  "max_cloud": get_settings().land_cover_max_cloud, "collection": "sentinel-2-l1c"}
    signature = hashlib.sha256(json.dumps([version, geometry, parameters, MODEL_VERSION], sort_keys=True).encode()).hexdigest()
    existing = db.scalar(select(LandCoverAnalysis).where(LandCoverAnalysis.target_type == target_type,
        LandCoverAnalysis.target_id == target.id, LandCoverAnalysis.input_signature == signature))
    if existing:
        return existing
    job = enqueue_job(db, task_type="land_cover", entity_type=target_type, entity_id=target.id,
        actor_id=actor_id, idempotency_key=signature, payload={"signature": signature})
    analysis = LandCoverAnalysis(target_type=target_type, target_id=target.id, input_signature=signature,
        geometry_json=geometry, boundary_version=version, processing_job_id=job.id,
        requested_by=actor_id, model_version=MODEL_VERSION, parameters_json=parameters)
    db.add(analysis)
    db.flush()
    record_audit(db, actor_id=actor_id, action="land_cover_requested", entity_type="land_cover_analysis",
        entity_id=analysis.id, after={"model_version": MODEL_VERSION, "boundary_version": version})
    return analysis


def current_target(db, analysis, *, lock=False):
    model = FRAClaim if analysis.target_type == "fra_claim" else PlotSubmission
    query = select(model).where(model.id == analysis.target_id).execution_options(populate_existing=True)
    if lock:
        query = query.with_for_update()
    target = db.scalar(query)
    if target is None:
        raise LandCoverError("The analysis boundary no longer exists.")
    if isinstance(target, FRAClaim):
        db.expire(target, ["geometry_versions"])
    _geometry, version = target_snapshot(target)
    if version != analysis.boundary_version:
        raise LandCoverError("The boundary has changed; request analysis of the new version.")
    return target


def default_stac():
    settings = get_settings()
    return STACClient(settings.satellite_stac_endpoint,
        allowed_hosts=set(settings.satellite_stac_allowed_hosts),
        allowed_collections={"sentinel-2-l1c", "sentinel-2-l2a"}, max_pages=2, max_results=5)


def run_analysis(db, job, *, stac=None, reader=None, predictor=None, storage=None):
    analysis = db.scalar(select(LandCoverAnalysis).where(LandCoverAnalysis.processing_job_id == job.id))
    if analysis is None:
        raise LandCoverError("Land-cover analysis not found for this job.")
    current_target(db, analysis)
    if analysis.state in {"completed", "insufficient_imagery", "insufficient_resolution"}:
        return {"analysis_id": str(analysis.id), "status": analysis.state}
    settings = get_settings()
    # Load verified weights before making remote raster requests; never fall back to synthetic output.
    predictor = predictor or DynamicWorldPredictor(settings.land_cover_weights_path)
    stac, storage = stac or default_stac(), storage or create_storage(settings)
    reader = reader or SentinelLandCoverReader(max_pixels=settings.land_cover_max_pixels)
    params = analysis.parameters_json
    selected = discover_scene_pair(stac, analysis.geometry_json, params)
    if selected is None:
        analysis.state = "insufficient_imagery"
        analysis.summary_json = {"warnings": ["No matching nine-band L1C scene and L2A cloud mask were found in the requested range."]}
        analysis.revision += 1
        return {"analysis_id": str(analysis.id), "status": analysis.state}
    scene, quality = selected
    raw, inside, valid, profile = reader.read(scene.private_asset_references, analysis.geometry_json,
                                            quality.private_asset_references["scl"])
    if int(valid.sum()) < 9:
        analysis.state = "insufficient_resolution"
        analysis.summary_json = {"valid_pixels": int(valid.sum()), "target_pixels": int(inside.sum()),
            "warnings": ["Fewer than nine clear 10 m pixel centres fall within this boundary. No land-cover result was inferred."]}
        analysis.revision += 1
        return {"analysis_id": str(analysis.id), "status": analysis.state}
    probabilities = predictor.predict(raw)
    labels, summary, features = summarize(probabilities, inside, valid, profile, analysis.geometry_json)
    current_target(db, analysis, lock=True)
    written = []
    try:
        artifacts = {}
        checksums = {}
        for kind, content, suffix in [
            ("input", encode_raster(raw, profile, nodata=float("nan"), descriptions=BAND_KEYS), ".tif"),
            ("validity", encode_raster(inside.astype("uint8") + valid.astype("uint8"), profile, nodata=0), ".tif"),
            ("labels", encode_raster(labels, profile, nodata=255), ".tif"),
            ("preview", encode_preview(labels), ".png"),
        ]:
            artifacts[kind] = storage.put(content, suffix)
            checksums[kind] = hashlib.sha256(content).hexdigest()
            written.append(artifacts[kind])
        analysis.artifact_keys_json = artifacts
        analysis.summary_json = summary
        analysis.features_json = features
        analysis.provenance_json = {"model": "Dynamic World (open-source PyTorch port)",
            "source_revision": SOURCE_REVISION, "weights_sha256": WEIGHTS_SHA256,
            "license": "Apache-2.0", "pixel_inference": True, "synthetic": False,
            "scene_id": scene.scene_id, "quality_scene_id": quality.scene_id,
            "acquired_at": scene.acquired_at.isoformat(), "collection": scene.collection,
            "bands": list(BAND_KEYS), "normalization": "dynamic-world-log-percentiles-v1",
            "runtime": getattr(predictor, "runtime", {}), "artifact_sha256": checksums,
            "radiometry": "provider_scale_offset_to_TOA_DN", "cloud_mask": "matching_L2A_SCL",
            "boundary_version": analysis.boundary_version, "legal_role": "supporting_observation"}
        analysis.state = "completed"
        analysis.revision += 1
        db.flush()
        record_audit(db, actor_id=job.requested_by, action="land_cover_completed",
            entity_type="land_cover_analysis", entity_id=analysis.id,
            after={"scene_id": scene.scene_id, "model_version": MODEL_VERSION, "review_state": "unverified"})
    except Exception:
        for key in written:
            storage.delete(key)
        raise
    return {"analysis_id": str(analysis.id), "status": "completed"}


def discover_scene_pair(stac, geometry, parameters):
    start, end = date.fromisoformat(parameters["start_date"]), date.fromisoformat(parameters["end_date"])
    scenes = stac.search(geometry, (start, end), ["sentinel-2-l1c"], parameters["max_cloud"])
    for scene in scenes[:3]:
        if not set(BAND_KEYS).issubset(scene.asset_keys):
            continue
        acquired = scene.acquired_at.date()
        quality_scenes = stac.search(geometry, (acquired, acquired), ["sentinel-2-l2a"], 100)
        # Match acquisition and MGRS tile, not merely the closest date/location.
        quality = next((q for q in quality_scenes if "scl" in q.asset_keys
            and q.scene_id.rsplit("_", 1)[0] == scene.scene_id.rsplit("_", 1)[0]
            and abs((q.acquired_at-scene.acquired_at).total_seconds()) < 60), None)
        if quality is not None:
            return scene, quality
    return None


def job_handler(db, job):
    try:
        return run_analysis(db, job)
    except LandCoverError as error:
        raise JobExecutionError("land_cover_unavailable", str(error), retriable=False) from error
    except STACProviderError as error:
        raise JobExecutionError("imagery_provider_unavailable", "Satellite discovery failed; retry later.", retriable=True) from error
    except Exception as error:
        # Provider URLs/keys and local model paths must not enter user-visible job errors.
        raise JobExecutionError("land_cover_processing_failed", "Satellite processing failed; staff can retry the job.", retriable=True) from error


def analysis_json(db, analysis):
    job = db.get(ProcessingJob, analysis.processing_job_id)
    state = analysis.state
    if state == "queued":
        state = job.state if job else "unavailable"
    try:
        current_target(db, analysis)
        current_boundary = True
    except LandCoverError:
        current_boundary = False
    return {"id": str(analysis.id), "target_type": analysis.target_type, "target_id": str(analysis.target_id),
        "boundary_version": analysis.boundary_version, "current_boundary": current_boundary,
        "state": state, "revision": analysis.revision,
        "review_state": analysis.review_state, "review_note": analysis.review_note,
        "model_version": analysis.model_version, "parameters": analysis.parameters_json,
        "summary": analysis.summary_json, "features": analysis.features_json,
        "provenance": analysis.provenance_json, "created_at": analysis.created_at.isoformat(),
        "job_id": str(analysis.processing_job_id), "can_retry": bool(job and job.state in {"quarantined", "failed"}),
        "error": job.error_message if job and job.error_code in {"land_cover_unavailable", "land_cover_processing_failed", "imagery_provider_unavailable"} else None,
        "artifacts": {kind: f"/api/land-cover/{analysis.id}/artifacts/{kind}" for kind in analysis.artifact_keys_json},
        "geojson_url": f"/api/land-cover/{analysis.id}/geojson", "report_url": f"/api/land-cover/{analysis.id}/report",
        "warning": WARNING}


def review_analysis(db, analysis, *, actor_id, state, note, expected_revision):
    if analysis.state != "completed" or analysis.review_state != "unverified":
        raise LandCoverError("Only a completed, unreviewed analysis can receive a final review.")
    target = current_target(db, analysis, lock=True)
    reserve_revision(db, analysis, expected_revision=expected_revision, state_field="review_state",
        conflict=HTTPException(409, "This analysis changed; reload it before reviewing."))
    analysis.review_state, analysis.review_note = state, note
    analysis.reviewed_by, analysis.reviewed_at = actor_id, utcnow()
    if state == "verified":
        if isinstance(target, PlotSubmission):
            for row in analysis.summary_json.get("classes", []):
                if row["pixels"] == 0:
                    continue
                db.add(UserResource(owner_id=target.owner_id, plot_id=target.plot_id,
                    name=f"Satellite land cover: {row['class_name'].replace('_', ' ')}",
                    description=f"Approved boundary revision {target.plot_revision}: estimated {row['estimated_area_sqm']:.0f} m²; {row['fraction']:.1%} of clear sampled pixels. {WARNING}",
                    source=f"Dynamic World / analysis {analysis.id}", verification_state="verified",
                    synthetic=False, recorded_by=actor_id,
                    observed_at=datetime.fromisoformat(analysis.provenance_json["acquired_at"])))
        else:
            existing = db.scalars(select(AssetFeature).where(AssetFeature.claim_id == target.id,
                AssetFeature.verification_state == "verified")).all()
            previous = [asset for asset in existing if (asset.provenance_json or {}).get("analysis_id")]
            scene = analysis.provenance_json["scene_id"]
            acquired = datetime.fromisoformat(analysis.provenance_json["acquired_at"]).date()
            # A repeated scene or an older acquisition must not duplicate/replace newer current evidence.
            already_current = any((asset.provenance_json or {}).get("boundary_version") == analysis.boundary_version
                and (asset.acquired_at > acquired or (asset.provenance_json or {}).get("scene_id") == scene)
                for asset in previous if asset.acquired_at)
            if already_current:
                record_audit(db, actor_id=actor_id, action="land_cover_reviewed_historical", entity_type="land_cover_analysis",
                    entity_id=analysis.id, after={"review_state": state, "asset_publication": "existing_current_evidence"})
                db.flush()
                return
            for asset in previous:
                asset.verification_state = "superseded"
                asset.revision += 1
            # Coarse trees/built cover cannot establish forest status, a homestead, or infrastructure.
            # Only the unambiguous water/crop classes are projected into the FRA asset taxonomy.
            for feature in analysis.features_json.get("features", []):
                row = feature["properties"]
                canonical = {"water": "water_body", "crops": "agricultural_land"}.get(row["class_name"], "other_asset")
                db.add(AssetFeature(claim_id=target.id, asset_class=canonical,
                    polygon_geometry=feature["geometry"],
                    observed_value_json={"asset_subtype": row["class_name"],
                        "cover_fraction": row["pixels"] / analysis.summary_json["target_pixels"],
                        "clear_pixel_fraction": analysis.summary_json["valid_fraction"],
                        "area_sqm": row["estimated_area_sqm"], "present": True},
                    confidence=row["mean_probability"], source_type="model",
                    source_reference=f"land-cover:{analysis.id}", provenance_json={**analysis.provenance_json,
                        "model_version": analysis.model_version, "analysis_id": str(analysis.id)},
                    acquired_at=datetime.fromisoformat(analysis.provenance_json["acquired_at"]).date(),
                    verification_state="verified", verification_reasons_json=[note], verified_by=actor_id,
                    verified_at=utcnow(), synthetic=False))
    record_audit(db, actor_id=actor_id, action="land_cover_reviewed", entity_type="land_cover_analysis",
        entity_id=analysis.id, after={"review_state": state, "boundary_version": analysis.boundary_version})
    db.flush()


def latest_for_submission(db, submission_id):
    row = db.scalar(select(LandCoverAnalysis).where(LandCoverAnalysis.target_type == "plot_submission",
        LandCoverAnalysis.target_id == submission_id).order_by(LandCoverAnalysis.created_at.desc()).limit(1))
    return analysis_json(db, row) if row else None
