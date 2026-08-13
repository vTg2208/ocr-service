"""Transactional, idempotent claim creation with conflict detection."""

from decimal import Decimal

from sqlalchemy import select

from app.db.models import Claim, Document, Notification, OCRResult
from app.services.audit import record_audit
from app.services.conflict_detection import detect_conflicts, ordered_claim_pair

__all__ = ["ClaimService", "ordered_claim_pair"]


def privacy_safe_conflict(conflict, claim_id) -> dict:
    existing_claim_id = conflict.claim_b_id if conflict.claim_a_id == claim_id else conflict.claim_a_id
    return {
        "id": str(conflict.id), "type": conflict.conflict_type,
        "existing_claim_id": str(existing_claim_id),
        "overlap_area_sqm": float(conflict.overlap_area_sqm) if conflict.overlap_area_sqm is not None else None,
        "overlap_percent": float(conflict.overlap_percent) if conflict.overlap_percent is not None else None,
        "status": conflict.status,
    }


class ClaimService:
    def __init__(self, session, *, conflict_detector=None, overlap_min_sqm=1.0, overlap_min_percent=1.0):
        self.session = session
        self.conflict_detector = conflict_detector or detect_conflicts
        self.overlap_min_sqm = overlap_min_sqm
        self.overlap_min_percent = overlap_min_percent

    def submit(
        self, *, claimant_id, document_id, parcel_id, confirmed_fields: dict,
        idempotency_key: str, request_id: str | None,
    ) -> dict:
        existing = self.session.scalar(select(Claim).where(
            Claim.claimant_id == claimant_id, Claim.idempotency_key == idempotency_key,
        ))
        if existing:
            return self._serialize(existing)
        document = self.session.get(Document, document_id)
        if document is None or document.uploaded_by != claimant_id:
            raise PermissionError("Document does not belong to the authenticated user.")
        ocr_result = self.session.scalar(select(OCRResult).where(OCRResult.document_id == document_id))
        valid_ids = (ocr_result.structured_result_json if ocr_result else {}).get("valid_parcel_ids", [])
        if str(parcel_id) not in valid_ids:
            raise ValueError("Selected parcel was not returned as a valid resolution candidate.")
        claimed_area = confirmed_fields.get("document_area_sqm")
        claim = Claim(
            claimant_id=claimant_id, parcel_id=parcel_id, document_id=document_id,
            claimed_area_sqm=Decimal(str(claimed_area)) if claimed_area is not None else None,
            confirmed_fields_json=confirmed_fields, status="matched", match_confidence=1,
            match_method="exact_composite_key", idempotency_key=idempotency_key,
        )
        self.session.add(claim)
        self.session.flush()
        conflicts = self.conflict_detector(
            self.session, claim, min_sqm=self.overlap_min_sqm,
            min_percent=self.overlap_min_percent,
        )
        if conflicts:
            claim.status = "conflicting"
            notification_targets = {claimant_id: claim.id}
            for conflict in conflicts:
                record_audit(
                    self.session, actor_id=claimant_id, action="conflict_created",
                    entity_type="claim_conflict", entity_id=conflict.id,
                    after={"type": conflict.conflict_type, "status": conflict.status},
                    request_id=request_id,
                )
                existing_claim_id = (
                    conflict.claim_b_id if conflict.claim_a_id == claim.id else conflict.claim_a_id
                )
                existing_claim = self.session.get(Claim, existing_claim_id)
                notification_targets[existing_claim.claimant_id] = existing_claim.id
            message = "A parcel claim requires administrative review. No claimant identity is disclosed."
            self.session.add_all([
                Notification(
                    user_id=user_id, notification_type="claim_conflict", message=message,
                    entity_type="claim", entity_id=entity_id,
                )
                for user_id, entity_id in notification_targets.items()
            ])
        record_audit(
            self.session, actor_id=claimant_id, action="claim_submitted",
            entity_type="claim", entity_id=claim.id, after={"status": claim.status},
            request_id=request_id,
        )
        self.session.flush()
        return self._serialize(claim, conflicts)

    def _serialize(self, claim, conflicts=None):
        if conflicts is None:
            from app.db.models import ClaimConflict
            conflicts = list(self.session.scalars(select(ClaimConflict).where(
                (ClaimConflict.claim_a_id == claim.id) | (ClaimConflict.claim_b_id == claim.id)
            )))
        return {
            "claim_id": str(claim.id), "status": claim.status,
            "conflicts": [privacy_safe_conflict(item, claim.id) for item in conflicts],
        }
