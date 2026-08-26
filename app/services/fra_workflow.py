"""Validated FRA lifecycle transitions and versioned title issuance."""

from dataclasses import dataclass

from app.db.fra_models import FRADecision, FRATitle
from app.services.audit import record_audit


TRANSITIONS: dict[str, set[str]] = {
    "draft": {"submitted"},
    "submitted": {"gram_sabha_verified", "remanded", "withdrawn"},
    "gram_sabha_verified": {"sdlc_review", "remanded"},
    "sdlc_review": {"dlc_decided", "remanded"},
    "dlc_decided": {"granted", "rejected", "remanded"},
    "remanded": {"submitted", "withdrawn"},
    "granted": {"superseded"},
    "rejected": {"remanded"},
    "withdrawn": set(),
    "superseded": set(),
}

REASON_REQUIRED_STATES = {"rejected", "remanded", "superseded"}


@dataclass
class InvalidTransitionError(ValueError):
    current_state: str
    target_state: str
    allowed_states: set[str]
    message: str | None = None

    def __str__(self) -> str:
        if self.message:
            return self.message
        allowed = ", ".join(sorted(self.allowed_states)) or "none"
        return (
            f"Cannot transition an FRA claim from {self.current_state!r} to "
            f"{self.target_state!r}. Allowed next states: {allowed}."
        )


class TitleIssuanceError(ValueError):
    pass


def allowed_transitions(status: str) -> set[str]:
    return set(TRANSITIONS.get(status, set()))


def transition_claim(
    session,
    claim,
    *,
    target_status: str,
    authority_level: str,
    outcome: str,
    reasons: list[str],
    actor_id,
    request_id: str | None,
) -> FRADecision:
    current_status = claim.status
    allowed = allowed_transitions(current_status)
    if target_status not in allowed:
        raise InvalidTransitionError(current_status, target_status, allowed)
    normalized_reasons = [str(reason).strip() for reason in reasons if str(reason).strip()]
    if target_status in REASON_REQUIRED_STATES and not normalized_reasons:
        raise InvalidTransitionError(
            current_status,
            target_status,
            allowed,
            f"A reason is required when an FRA claim is {target_status}.",
        )

    decision = FRADecision(
        claim=claim,
        authority_level=authority_level,
        from_status=current_status,
        to_status=target_status,
        outcome=outcome,
        reasons_json=normalized_reasons,
        actor_id=actor_id,
        request_id=request_id,
    )
    claim.status = target_status
    session.add(decision)
    record_audit(
        session,
        actor_id=actor_id,
        action="fra_claim_transitioned",
        entity_type="fra_claim",
        entity_id=claim.id,
        before={"status": current_status},
        after={
            "status": target_status,
            "authority_level": authority_level,
            "outcome": outcome,
        },
        request_id=request_id,
    )
    return decision


def issue_title(
    session,
    claim,
    *,
    title_number: str,
    geometry_version_id,
    issued_by,
    metadata: dict,
    request_id: str | None,
) -> FRATitle:
    if claim.status != "granted":
        raise TitleIssuanceError("A title can be issued only for a granted FRA claim.")
    normalized_number = title_number.strip()
    if not normalized_number:
        raise TitleIssuanceError("A title number is required.")

    titles = list(claim.titles)
    for existing in titles:
        if existing.active:
            existing.active = False
    title = FRATitle(
        claim=claim,
        version=max((item.version for item in titles), default=0) + 1,
        title_number=normalized_number,
        geometry_version_id=geometry_version_id,
        active=True,
        metadata_json=dict(metadata),
        issued_by=issued_by,
    )
    session.add(title)
    record_audit(
        session,
        actor_id=issued_by,
        action="fra_title_issued",
        entity_type="fra_claim",
        entity_id=claim.id,
        after={"title_number": normalized_number, "version": title.version},
        request_id=request_id,
    )
    return title
