"""Idempotent triage and promotion of legacy registry claims into native FRA cases."""

from sqlalchemy import select

from app.db.fra_models import FRAClaim
from app.db.fra_operational_models import FRAIntakeItem
from app.db.models import Claim
from app.services.audit import record_audit
from app.services.fra_claims import promote_legacy_claim


class IntakeConflictError(RuntimeError):
    pass


REVIEW_STATES = {"awaiting_triage", "ready_for_promotion", "not_fra", "duplicate"}


def ensure_intake_for_legacy_claim(
    session, legacy_claim: Claim, *, actor_id, request_id: str | None = None
) -> FRAIntakeItem:
    existing = session.scalar(
        select(FRAIntakeItem).where(FRAIntakeItem.legacy_claim_id == legacy_claim.id)
    )
    if existing is not None:
        return existing
    intake = FRAIntakeItem(
        legacy_claim_id=legacy_claim.id,
        state="awaiting_triage",
        created_by=actor_id,
    )
    session.add(intake)
    session.flush()
    record_audit(
        session,
        actor_id=actor_id,
        action="fra_intake_created",
        entity_type="fra_intake",
        entity_id=intake.id,
        after={"legacy_claim_id": str(legacy_claim.id), "state": intake.state},
        request_id=request_id,
    )
    return intake


def update_intake(
    session,
    intake: FRAIntakeItem,
    *,
    target_state: str,
    expected_revision: int,
    reasons: list[str],
    actor_id,
    triage: dict | None = None,
    request_id: str | None = None,
) -> FRAIntakeItem:
    if intake.state == "promoted":
        raise IntakeConflictError("A promoted FRA intake cannot be changed.")
    if intake.revision != expected_revision:
        raise IntakeConflictError("The FRA intake changed since it was loaded.")
    if target_state not in REVIEW_STATES:
        raise ValueError("Unsupported FRA intake state.")
    normalized_reasons = [str(item).strip() for item in reasons if str(item).strip()]
    if target_state in {"not_fra", "duplicate"} and not normalized_reasons:
        raise ValueError("A reason is required for this intake outcome.")
    before = {
        "state": intake.state,
        "triage": dict(intake.triage_json or {}),
        "revision": intake.revision,
    }
    intake.state = target_state
    intake.reasons_json = normalized_reasons
    intake.triage_json = dict(triage or intake.triage_json or {})
    intake.updated_by = actor_id
    intake.revision += 1
    record_audit(
        session,
        actor_id=actor_id,
        action="fra_intake_reviewed",
        entity_type="fra_intake",
        entity_id=intake.id,
        before=before,
        after={
            "state": intake.state,
            "triage": dict(intake.triage_json),
            "reasons": list(intake.reasons_json),
            "revision": intake.revision,
        },
        request_id=request_id,
    )
    session.flush()
    return intake


def promote_intake(
    session,
    intake: FRAIntakeItem,
    *,
    right_type: str,
    rights_holder_id,
    gram_sabha_id,
    expected_revision: int,
    actor_id,
    request_id: str | None = None,
) -> FRAClaim:
    if intake.revision != expected_revision:
        raise IntakeConflictError("The FRA intake changed since it was loaded.")
    if intake.promoted_claim_id is not None:
        existing = session.get(FRAClaim, intake.promoted_claim_id)
        if existing is None:
            raise IntakeConflictError("The promoted FRA claim no longer exists.")
        return existing
    if intake.state != "ready_for_promotion":
        raise IntakeConflictError("Only a reviewed FRA intake can be promoted.")
    claim = promote_legacy_claim(
        session,
        legacy_claim_id=intake.legacy_claim_id,
        rights_holder_id=rights_holder_id,
        right_type=right_type,
        gram_sabha_id=gram_sabha_id,
        actor_id=actor_id,
        request_id=request_id,
    )
    intake.promoted_claim_id = claim.id
    intake.state = "promoted"
    intake.updated_by = actor_id
    intake.revision += 1
    record_audit(
        session,
        actor_id=actor_id,
        action="fra_intake_promoted",
        entity_type="fra_intake",
        entity_id=intake.id,
        after={"fra_claim_id": str(claim.id), "state": intake.state},
        request_id=request_id,
    )
    session.flush()
    return claim
