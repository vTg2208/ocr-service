"""Human-managed, advisory-only follow-up for DSS recommendations."""

from datetime import datetime, timezone

from sqlalchemy import select

from app.db.fra_completion_models import DSSReferral
from app.db.fra_models import DSSRecommendation
from app.db.models import User
from app.services.audit import record_audit


ALLOWED_PRIORITIES = {"low", "normal", "high", "urgent"}
TRANSITIONS = {
    "draft": {"referred", "withdrawn"},
    "referred": {"under_review", "withdrawn"},
    "under_review": {"closed", "withdrawn"},
    "closed": set(),
    "withdrawn": set(),
}
BANNED_STATUSES = {"approved", "sanctioned", "eligible"}


class ReferralValidationError(ValueError):
    pass


class ReferralConflictError(RuntimeError):
    pass


def _reviewer(session, actor_id) -> User:
    actor = session.get(User, actor_id)
    if actor is None or actor.role not in {"reviewer", "admin"}:
        raise PermissionError("DSS referral mutation requires a reviewer or admin.")
    return actor


def list_recommendations(
    session,
    *,
    claim_id=None,
    outcome: str | None = None,
    scheme_code: str | None = None,
) -> list[DSSRecommendation]:
    statement = select(DSSRecommendation)
    if claim_id is not None:
        statement = statement.where(DSSRecommendation.claim_id == claim_id)
    if outcome:
        statement = statement.where(DSSRecommendation.outcome == outcome)
    recommendations = session.scalars(
        statement.order_by(DSSRecommendation.created_at.desc(), DSSRecommendation.id)
    ).all()
    if scheme_code:
        recommendations = [
            item for item in recommendations if item.rule_set.scheme_code == scheme_code
        ]
    return list(recommendations)


def create_referral(
    session,
    *,
    recommendation_id,
    department: str,
    priority: str,
    actor_id,
    idempotency_key: str,
    assigned_to: str | None = None,
    notes: str | None = None,
    request_id: str | None = None,
) -> DSSReferral:
    _reviewer(session, actor_id)
    recommendation = session.get(DSSRecommendation, recommendation_id)
    if recommendation is None:
        raise ReferralValidationError("DSS recommendation does not exist.")
    department = " ".join(department.split())
    priority = priority.strip().casefold()
    key = idempotency_key.strip()
    if not department or not key:
        raise ReferralValidationError("Department and idempotency key are required.")
    if priority not in ALLOWED_PRIORITIES:
        raise ReferralValidationError("Referral priority must be low, normal, high, or urgent.")
    existing = session.scalar(
        select(DSSReferral).where(DSSReferral.recommendation_id == recommendation_id)
    )
    if existing is not None:
        if existing.created_by == actor_id and existing.idempotency_key == key:
            return existing
        raise ReferralConflictError("This recommendation already has a referral.")
    now = datetime.now(timezone.utc)
    referral = DSSReferral(
        recommendation=recommendation,
        department=department,
        priority=priority,
        status="referred",
        assigned_to=" ".join(assigned_to.split()) if assigned_to else None,
        notes=notes.strip() if notes else None,
        history_json=[
            {
                "status": "referred",
                "department": department,
                "priority": priority,
                "assigned_to": " ".join(assigned_to.split()) if assigned_to else None,
                "notes": notes.strip() if notes else None,
                "actor_id": str(actor_id),
                "at": now.isoformat(),
            }
        ],
        advisory_only=True,
        created_by=actor_id,
        idempotency_key=key,
    )
    session.add(referral)
    session.flush()
    record_audit(
        session,
        actor_id=actor_id,
        action="dss_referral_created",
        entity_type="dss_referral",
        entity_id=referral.id,
        after={
            "recommendation_id": str(recommendation.id),
            "department": department,
            "status": "referred",
            "advisory_only": True,
        },
        request_id=request_id,
    )
    return referral


def update_referral(
    session,
    referral: DSSReferral,
    *,
    status: str,
    notes: str | None,
    assigned_to: str | None,
    actor_id,
    expected_revision: int,
    request_id: str | None = None,
) -> DSSReferral:
    _reviewer(session, actor_id)
    target = status.strip().casefold()
    if target in BANNED_STATUSES:
        raise ReferralValidationError("A referral cannot approve or sanction a benefit.")
    if target not in TRANSITIONS:
        raise ReferralValidationError("Unknown referral status.")
    if referral.revision != expected_revision:
        raise ReferralConflictError("The referral changed since it was loaded.")
    if target not in TRANSITIONS.get(referral.status, set()):
        raise ReferralConflictError(
            f"Cannot move a referral from {referral.status} to {target}."
        )
    normalized_notes = notes.strip() if notes else None
    if target in {"closed", "withdrawn"} and not normalized_notes:
        raise ReferralValidationError(f"Notes are required when a referral is {target}.")
    before = {"status": referral.status, "revision": referral.revision}
    referral.status = target
    referral.notes = normalized_notes
    if assigned_to is not None:
        referral.assigned_to = " ".join(assigned_to.split()) or None
    referral.revision += 1
    history = list(referral.history_json or [])
    history.append(
        {
            "status": target,
            "department": referral.department,
            "priority": referral.priority,
            "assigned_to": referral.assigned_to,
            "notes": normalized_notes,
            "actor_id": str(actor_id),
            "at": datetime.now(timezone.utc).isoformat(),
        }
    )
    referral.history_json = history
    session.flush()
    record_audit(
        session,
        actor_id=actor_id,
        action="dss_referral_updated",
        entity_type="dss_referral",
        entity_id=referral.id,
        before=before,
        after={"status": target, "revision": referral.revision, "advisory_only": True},
        request_id=request_id,
    )
    return referral
