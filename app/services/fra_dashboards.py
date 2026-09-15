"""Privacy-minimized operational dashboards for FRA verification and planning."""

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select

from app.db.fra_completion_models import DSSReferral, FRAArchiveRecord, FRAVillageProfile, ProcessingJob
from app.db.fra_models import DSSRecommendation, FRAClaim, FRATitle
from app.db.fra_operational_models import DSSFactSnapshot, FRAIntakeItem, ImageryArtifact
from app.db.models import Claim, Parcel
from app.services.asset_contracts import canonical_asset_class
from app.services.dss_referrals import list_recommendations
from app.services.fra_assets import list_assets
from app.services.fra_locations import archive_location, claim_location, location_matches, normalized, parcel_location, village_location
from app.services.fra_spatial_policy import _area_sqm
from app.services.scheme_convergence import list_scheme_convergence


QUEUE_DISPLAY_LIMIT = 100
PENDING_CLAIM_STATUSES = {
    "draft", "submitted", "gram_sabha_verified", "sdlc_review", "dlc_decided", "remanded",
}
DEVELOPMENT_DIMENSIONS = {
    "water_availability": {
        "assets": ("water_body",),
        "deficiencies": ("water_source_present",),
    },
    "agriculture": {
        "assets": ("agricultural_land",),
        "deficiencies": ("agricultural_land_observation",),
    },
    "homesteads": {
        "assets": ("homestead",),
        "deficiencies": ("homestead_observation",),
    },
    "infrastructure": {
        "assets": ("infrastructure", "road"),
        "deficiencies": ("infrastructure_services_available", "road_access_present"),
    },
}


def _claims(session, scope):
    return [claim for claim in session.scalars(select(FRAClaim).order_by(FRAClaim.created_at, FRAClaim.id))
            if location_matches(claim_location(claim), **scope)]


def _claim_area(claim: FRAClaim) -> float:
    if claim.claimed_area_sqm is not None:
        return float(claim.claimed_area_sqm)
    geometry = max(claim.geometry_versions, key=lambda item: item.version, default=None)
    return _area_sqm(geometry.geometry) if geometry is not None else 0.0


def _case_row(claim: FRAClaim, queue: str, reason: str) -> dict:
    return {"queue": queue, "claim_id": str(claim.id), "reference": claim.claim_number,
            "right_type": claim.right_type, "status": claim.status, "reason": reason,
            **{k: v for k, v in claim_location(claim).items() if k != "state"}, "workspace": "/fra#cases"}


def _record_row(record):
    return {"record_id": str(record.id), "reference": record.legacy_reference, "status": record.review_state,
            "district": record.district, "block": record.block, "village": record.village, "workspace": "/fra#archive"}


def _village_row(village):
    return {"village_id": str(village.id), "reference": village.village_name,
            **{k: v for k, v in village_location(village).items() if k != "state"}, "workspace": "/fra#assets"}


def verifier_dashboard(session, *, district=None, block=None, village=None) -> dict:
    scope = dict(district=district, block=block, village=village)
    claims = _claims(session, scope)
    claim_by_id = {claim.id: claim for claim in claims}
    queues = {name: [] for name in ("archive_review", "intake_triage", "claims_review",
                                   "spatial_disposition", "unverified_observations", "processing_failures")}
    # Full scoped populations supply totals and job targets; cap only the displayed rows.
    archives = {record.id: record for record in session.scalars(
        select(FRAArchiveRecord).order_by(FRAArchiveRecord.created_at, FRAArchiveRecord.id))
        if location_matches(archive_location(record), **scope)}
    villages = {record.id: record for record in session.scalars(
        select(FRAVillageProfile).order_by(FRAVillageProfile.created_at, FRAVillageProfile.id))
        if location_matches(village_location(record), **scope)}
    for record in archives.values():
        if record.review_state in {"pending", "needs_review"}:
            queues["archive_review"].append({**_record_row(record), "queue": "archive_review",
                                              "reason": "Archive field review required"})
    intake_statement = (select(FRAIntakeItem, Parcel)
        .join(Claim, FRAIntakeItem.legacy_claim_id == Claim.id).join(Parcel, Claim.parcel_id == Parcel.id)
        .where(FRAIntakeItem.state == "awaiting_triage").order_by(FRAIntakeItem.created_at, FRAIntakeItem.id))
    for intake, parcel in session.execute(intake_statement):
        if location_matches(parcel_location(parcel), **scope):
            queues["intake_triage"].append({"queue": "intake_triage", "intake_id": str(intake.id),
                "reference": f"Survey {parcel.survey_number}/{parcel.subdivision_number or '—'}",
                "status": intake.state, "reason": "Registry intake requires FRA triage",
                "district": parcel.district, "block": parcel.taluk, "village": parcel.village, "workspace": "/fra#cases"})
    for claim in claims:
        if claim.status in {"submitted", "remanded"}:
            queues["claims_review"].append(_case_row(claim, "claims_review", "Lifecycle review required"))
        geometry = max(claim.geometry_versions, key=lambda item: item.version, default=None)
        has_disposition = geometry is not None and any(
            item.source == "spatial_evaluation_disposition" and item.source_verified
            and (item.provenance_json or {}).get("geometry_version_id") == str(geometry.id)
            for item in claim.evidence_items)
        if geometry is not None and not has_disposition:
            queues["spatial_disposition"].append(_case_row(claim, "spatial_disposition", "Spatial checks require reviewer disposition"))
    for asset in list_assets(session, verification_state="unverified", **scope):
        if asset.claim_id in claim_by_id:
            row = _case_row(claim_by_id[asset.claim_id], "unverified_observation", "Asset observation requires human verification")
        elif asset.village_id in villages:
            row = {**_village_row(villages[asset.village_id]), "queue": "unverified_observation",
                   "reason": "Asset observation requires human verification"}
        else:
            continue
        queues["unverified_observations"].append({**row, "observation_type": asset.asset_class, "observation_id": str(asset.id)})
    for artifact in session.scalars(select(ImageryArtifact).where(
            ImageryArtifact.claim_id.in_(claim_by_id), ImageryArtifact.verification_state == "unverified"
            ).order_by(ImageryArtifact.created_at, ImageryArtifact.id)):
        row = _case_row(claim_by_id[artifact.claim_id], "unverified_observation", "Historical observation requires human verification")
        queues["unverified_observations"].append({**row, "observation_type": artifact.artifact_type, "observation_id": str(artifact.id)})
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    jobs = session.scalars(select(ProcessingJob).where(or_(
        ProcessingJob.state.in_(("failed", "quarantined")),
        ProcessingJob.state.in_(("queued", "running")) & (ProcessingJob.created_at < cutoff)
    )).order_by(ProcessingJob.created_at, ProcessingJob.id))
    for job in jobs:
        if job.entity_type == "fra_claim" and job.entity_id in claim_by_id:
            row = _case_row(claim_by_id[job.entity_id], "processing_failure", "Processing failed or is overdue")
        elif job.entity_type == "archive_record" and job.entity_id in archives:
            row = _record_row(archives[job.entity_id])
        elif job.entity_type == "village" and job.entity_id in villages:
            row = _village_row(villages[job.entity_id])
        else:
            continue
        queues["processing_failures"].append({**row, "queue": "processing_failure", "reason": "Processing failed or is overdue",
            "job_id": str(job.id), "task_type": job.task_type, "job_state": job.state, "error_code": job.error_code})
    total_keys = {"archive_records_needing_review": "archive_review", "intake_awaiting_triage": "intake_triage",
                  "claims_awaiting_review": "claims_review", "spatial_findings_awaiting_disposition": "spatial_disposition",
                  "unverified_observations": "unverified_observations", "failed_or_overdue_jobs": "processing_failures"}
    return {"totals": {key: len(queues[queue]) for key, queue in total_keys.items()},
            "queues": {key: rows[:QUEUE_DISPLAY_LIMIT] for key, rows in queues.items()}}


def planner_dashboard(session, *, district=None, block=None, village=None) -> dict:
    scope = dict(district=district, block=block, village=village)
    claims = _claims(session, scope)
    claim_ids = [claim.id for claim in claims]
    active_titles = session.scalar(select(func.count(FRATitle.id)).where(
        FRATitle.claim_id.in_(claim_ids), FRATitle.active.is_(True))) or 0
    title_count = sum(len(claim.titles) for claim in claims)
    decision_count = sum(len(claim.decisions) for claim in claims)
    pending_cases = sum(claim.status in PENDING_CLAIM_STATUSES for claim in claims)
    fra_area_sqm = round(sum(_claim_area(claim) for claim in claims), 2)
    granted_area_sqm = round(sum(
        float(title.granted_area_sqm or 0)
        for claim in claims for title in claim.titles if title.active
    ), 2)
    spatialized_claims = sum(bool(claim.geometry_versions) for claim in claims)
    villages_covered = len({
        tuple(normalized(location.get(key)) for key in ("district", "block", "village"))
        for claim in claims
        for location in (claim_location(claim),)
        if normalized(location.get("village"))
    })
    recommendations = list_recommendations(session, **scope)
    recommendation_counts = Counter((item.rule_set.scheme_code, item.outcome) for item in recommendations)
    referral_rows = session.execute(select(DSSReferral.department, DSSReferral.status, func.count(DSSReferral.id))
        .join(DSSRecommendation, DSSReferral.recommendation_id == DSSRecommendation.id)
        .where(DSSRecommendation.claim_id.in_(claim_ids)).group_by(DSSReferral.department, DSSReferral.status))
    missing_by_claim, deficits = defaultdict(set), Counter()
    for recommendation in recommendations:
        missing_by_claim[recommendation.claim_id].update(str(fact) for fact in (recommendation.output_json or {}).get("missing_inputs", []))
    snapshots = session.scalars(select(DSSFactSnapshot).where(DSSFactSnapshot.claim_id.in_(claim_ids))
                               .order_by(DSSFactSnapshot.created_at.desc(), DSSFactSnapshot.id.desc()))
    seen = set()
    for snapshot in snapshots:
        if snapshot.claim_id in seen:
            continue
        seen.add(snapshot.claim_id)
        for name, fact in (snapshot.facts_json or {}).items():
            if isinstance(fact, dict) and fact.get("value") is False and (
                name.endswith(("_present", "_observation", "_available"))
                or any(name in item["deficiencies"] for item in DEVELOPMENT_DIMENSIONS.values())
            ):
                deficits[name] += 1
        unknown = (snapshot.facts_json or {}).get("source_quality_flags", {}).get("value", {}).get("unknown_facts", [])
        missing_by_claim[snapshot.claim_id].update(str(fact) for fact in unknown)
    missing = Counter(fact for inputs in missing_by_claim.values() for fact in inputs)
    assets = Counter(canonical_asset_class(asset.asset_class) for asset in list_assets(session, verification_state="verified", **scope))
    development = {
        label: {
            "mapped_assets": sum(assets[name] for name in definition["assets"]),
            "recorded_deficiencies": sum(deficits[name] for name in definition["deficiencies"]),
        }
        for label, definition in DEVELOPMENT_DIMENSIONS.items()
    }
    convergence = list_scheme_convergence(session, **scope)
    interventions = Counter(
        intervention
        for item in convergence["items"]
        if item["convergence_status"] == "potentially_eligible"
        for intervention in item["recommended_interventions"]
    )
    convergence_rows = Counter(
        (item["scheme_code"], item["convergence_status"])
        for item in convergence["items"]
    )
    insufficient_claims = {
        item["claim_id"] for item in convergence["items"]
        if item["convergence_status"] == "insufficient_data"
    }
    return {
        "claims_by_status": dict(sorted(Counter(claim.status for claim in claims).items())),
        "claims_by_right_type": dict(sorted(Counter(claim.right_type for claim in claims).items())),
        "active_titles": int(active_titles),
        "granted_area_sqm": granted_area_sqm,
        "verified_assets": dict(sorted(assets.items())), "deficit_counts": dict(sorted(deficits.items())),
        "recommendations": [{"scheme_code": code, "outcome": outcome, "count": count}
                            for (code, outcome), count in sorted(recommendation_counts.items())],
        "referrals": [{"department": department, "status": status, "count": int(count)}
                      for department, status, count in referral_rows],
        "missing_inputs": [{"fact": fact, "count": count} for fact, count in sorted(missing.items())],
        "fra": {
            "claims": len(claims), "titles": title_count, "active_titles": int(active_titles),
            "pending_cases": pending_cases, "decisions": decision_count,
        },
        "spatial": {
            "fra_area_sqm": fra_area_sqm, "granted_area_sqm": granted_area_sqm,
            "villages_covered": villages_covered,
            "spatialized_claims": spatialized_claims,
            "unmapped_claims": len(claims) - spatialized_claims,
            "asset_distribution": dict(sorted(assets.items())),
        },
        "development": development,
        "dss": {
            "recommended_interventions": [
                {"intervention": name, "count": count} for name, count in sorted(interventions.items())
            ],
            "scheme_convergence": dict(convergence["summary"]),
            "scheme_convergence_by_programme": [
                {"scheme_code": code, "status": status, "count": count}
                for (code, status), count in sorted(convergence_rows.items())
            ],
            "insufficient_data_cases": len(insufficient_claims),
        },
    }


__all__ = ["planner_dashboard", "verifier_dashboard"]
