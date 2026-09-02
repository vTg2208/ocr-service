"""Privacy-minimized operational dashboards for FRA verification and planning."""

from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select

from app.db.fra_completion_models import AssetFeature, DSSReferral, FRAArchiveRecord, ProcessingJob
from app.db.fra_models import DSSRecommendation, FRAClaim, FRAEvidenceItem, FRATitle, GramSabha, SchemeRuleSet
from app.db.fra_operational_models import DSSFactSnapshot, FRAIntakeItem, ImageryArtifact
from app.db.models import Claim, Parcel


def _location_conditions(district=None, block=None, village=None):
    conditions = []
    if district:
        conditions.append(GramSabha.district == district)
    if block:
        conditions.append(GramSabha.block == block)
    if village:
        conditions.append(GramSabha.village == village)
    return conditions


def _claim_ids(session, *, district=None, block=None, village=None):
    return list(session.scalars(
        select(FRAClaim.id).outerjoin(GramSabha, FRAClaim.gram_sabha_id == GramSabha.id)
        .where(*_location_conditions(district, block, village))
    ))


def _case_row(claim: FRAClaim, queue: str, reason: str) -> dict:
    return {
        "queue": queue, "claim_id": str(claim.id), "reference": claim.claim_number,
        "right_type": claim.right_type, "status": claim.status, "reason": reason,
        "district": claim.gram_sabha.district if claim.gram_sabha else None,
        "block": claim.gram_sabha.block if claim.gram_sabha else None,
        "village": claim.gram_sabha.village if claim.gram_sabha else None,
        "workspace": "/fra#cases",
    }


def verifier_dashboard(session, *, district=None, block=None, village=None) -> dict:
    claim_ids = _claim_ids(session, district=district, block=block, village=village)
    claims = list(session.scalars(select(FRAClaim).where(FRAClaim.id.in_(claim_ids)))) if claim_ids else []
    claim_by_id = {claim.id: claim for claim in claims}
    queues = {
        "archive_review": [], "intake_triage": [], "claims_review": [],
        "spatial_disposition": [], "unverified_observations": [], "processing_failures": [],
    }
    archive_conditions = [FRAArchiveRecord.review_state.in_(("pending", "needs_review"))]
    if district: archive_conditions.append(FRAArchiveRecord.district == district)
    if block: archive_conditions.append(FRAArchiveRecord.block == block)
    if village: archive_conditions.append(FRAArchiveRecord.village == village)
    for record in session.scalars(select(FRAArchiveRecord).where(*archive_conditions).limit(100)):
        queues["archive_review"].append({"queue": "archive_review", "record_id": str(record.id), "reference": record.legacy_reference, "status": record.review_state, "reason": "Archive field review required", "district": record.district, "block": record.block, "village": record.village, "workspace": "/fra#archive"})

    intake_statement = (
        select(FRAIntakeItem, Parcel)
        .join(Claim, FRAIntakeItem.legacy_claim_id == Claim.id)
        .join(Parcel, Claim.parcel_id == Parcel.id)
        .where(FRAIntakeItem.state == "awaiting_triage")
    )
    if district: intake_statement = intake_statement.where(Parcel.district == district)
    if block: intake_statement = intake_statement.where(Parcel.taluk == block)
    if village: intake_statement = intake_statement.where(Parcel.village == village)
    for intake, parcel in session.execute(intake_statement.limit(100)):
        queues["intake_triage"].append({
            "queue": "intake_triage", "intake_id": str(intake.id),
            "reference": f"Survey {parcel.survey_number}/{parcel.subdivision_number or '—'}",
            "status": intake.state, "reason": "Registry intake requires FRA triage",
            "district": parcel.district, "block": parcel.taluk, "village": parcel.village,
            "workspace": "/fra#cases",
        })

    for claim in claims:
        if claim.status in {"submitted", "remanded"}:
            queues["claims_review"].append(_case_row(claim, "claims_review", "Lifecycle review required"))
        has_boundary = bool(claim.geometry_versions)
        has_disposition = any(item.source == "spatial_evaluation_disposition" for item in claim.evidence_items)
        if has_boundary and not has_disposition:
            queues["spatial_disposition"].append(_case_row(claim, "spatial_disposition", "Spatial checks require reviewer disposition"))

    if claim_ids:
        for asset in session.scalars(select(AssetFeature).where(AssetFeature.claim_id.in_(claim_ids), AssetFeature.verification_state == "unverified").limit(100)):
            claim = claim_by_id.get(asset.claim_id)
            if claim: queues["unverified_observations"].append({**_case_row(claim, "unverified_observation", "Asset observation requires human verification"), "observation_type": asset.asset_class, "observation_id": str(asset.id)})
        for artifact in session.scalars(select(ImageryArtifact).where(ImageryArtifact.claim_id.in_(claim_ids), ImageryArtifact.verification_state == "unverified").limit(100)):
            claim = claim_by_id.get(artifact.claim_id)
            if claim: queues["unverified_observations"].append({**_case_row(claim, "unverified_observation", "Historical observation requires human verification"), "observation_type": artifact.artifact_type, "observation_id": str(artifact.id)})
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        jobs = session.scalars(select(ProcessingJob).where(
            ProcessingJob.entity_type == "fra_claim", ProcessingJob.entity_id.in_(claim_ids),
            or_(ProcessingJob.state.in_(("failed", "quarantined")), ProcessingJob.state.in_(("queued", "running")) & (ProcessingJob.created_at < cutoff)),
        ).limit(100))
        for job in jobs:
            claim = claim_by_id.get(job.entity_id)
            if claim: queues["processing_failures"].append({**_case_row(claim, "processing_failure", "Processing failed or is overdue"), "job_id": str(job.id), "task_type": job.task_type, "job_state": job.state, "error_code": job.error_code})
    return {
        "totals": {
            "archive_records_needing_review": len(queues["archive_review"]),
            "intake_awaiting_triage": len(queues["intake_triage"]),
            "claims_awaiting_review": len(queues["claims_review"]),
            "spatial_findings_awaiting_disposition": len(queues["spatial_disposition"]),
            "unverified_observations": len(queues["unverified_observations"]),
            "failed_or_overdue_jobs": len(queues["processing_failures"]),
        },
        "queues": queues,
    }


def _group_counts(session, column, claim_ids):
    if not claim_ids:
        return {}
    return {
        str(key): int(count)
        for key, count in session.execute(
            select(column, func.count(FRAClaim.id)).where(FRAClaim.id.in_(claim_ids)).group_by(column)
        )
    }


def planner_dashboard(session, *, district=None, block=None, village=None) -> dict:
    claim_ids = _claim_ids(session, district=district, block=block, village=village)
    if not claim_ids:
        return {"claims_by_status": {}, "claims_by_right_type": {}, "active_titles": 0, "granted_area_sqm": 0, "verified_assets": {}, "deficit_counts": {}, "recommendations": [], "referrals": [], "missing_inputs": []}
    active_titles = session.scalar(select(func.count(FRATitle.id)).where(FRATitle.claim_id.in_(claim_ids), FRATitle.active.is_(True))) or 0
    granted_area = session.scalar(select(func.coalesce(func.sum(FRAClaim.claimed_area_sqm), 0)).where(FRAClaim.id.in_(claim_ids), FRAClaim.status == "granted")) or 0
    asset_rows = session.execute(select(AssetFeature.asset_class, func.count(AssetFeature.id)).where(AssetFeature.claim_id.in_(claim_ids), AssetFeature.verification_state == "verified").group_by(AssetFeature.asset_class))
    recommendation_rows = session.execute(select(SchemeRuleSet.scheme_code, DSSRecommendation.outcome, func.count(DSSRecommendation.id)).join(DSSRecommendation, DSSRecommendation.rule_set_id == SchemeRuleSet.id).where(DSSRecommendation.claim_id.in_(claim_ids)).group_by(SchemeRuleSet.scheme_code, DSSRecommendation.outcome))
    referral_rows = session.execute(select(DSSReferral.department, DSSReferral.status, func.count(DSSReferral.id)).join(DSSRecommendation, DSSReferral.recommendation_id == DSSRecommendation.id).where(DSSRecommendation.claim_id.in_(claim_ids)).group_by(DSSReferral.department, DSSReferral.status))
    missing, deficits = Counter(), Counter()
    recommendations = list(session.scalars(select(DSSRecommendation).where(DSSRecommendation.claim_id.in_(claim_ids))))
    for recommendation in recommendations:
        for fact in (recommendation.output_json or {}).get("missing_inputs", []): missing[str(fact)] += 1
    snapshots = session.scalars(select(DSSFactSnapshot).where(DSSFactSnapshot.claim_id.in_(claim_ids)))
    for snapshot in snapshots:
        for name, fact in (snapshot.facts_json or {}).items():
            if name.endswith(("_present", "_observation")) and fact.get("value") is False: deficits[name] += 1
        for fact in ((snapshot.facts_json or {}).get("source_quality_flags", {}).get("value", {}).get("unknown_facts", [])): missing[str(fact)] += 1
    return {
        "claims_by_status": _group_counts(session, FRAClaim.status, claim_ids),
        "claims_by_right_type": _group_counts(session, FRAClaim.right_type, claim_ids),
        "active_titles": int(active_titles), "granted_area_sqm": float(granted_area),
        "verified_assets": {str(key): int(count) for key, count in asset_rows},
        "deficit_counts": dict(sorted(deficits.items())),
        "recommendations": [{"scheme_code": code, "outcome": outcome, "count": int(count)} for code, outcome, count in recommendation_rows],
        "referrals": [{"department": department, "status": status, "count": int(count)} for department, status, count in referral_rows],
        "missing_inputs": [{"fact": fact, "count": count} for fact, count in sorted(missing.items())],
    }


__all__ = ["planner_dashboard", "verifier_dashboard"]
