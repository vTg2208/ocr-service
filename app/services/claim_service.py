"""Transactional, idempotent creation of exclusive parcel claims."""

from decimal import Decimal

from sqlalchemy import select

from app.db.models import Claim, Document, OCRResult
from app.services.audit import record_audit
from app.services.claim_eligibility import ensure_land_available
from app.services.conflict_detection import ordered_claim_pair
from app.services.fra_intake import ensure_intake_for_legacy_claim

__all__ = ["ClaimService", "ordered_claim_pair"]


class ClaimService:
    def __init__(self, session, *, eligibility_checker=None, overlap_min_sqm=1.0, overlap_min_percent=1.0):
        self.session = session
        self.eligibility_checker = eligibility_checker or ensure_land_available
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
            intake = ensure_intake_for_legacy_claim(
                self.session, existing, actor_id=claimant_id, request_id=request_id,
            )
            return self._serialize(existing, intake)
        document = self.session.get(Document, document_id)
        if document is None or document.uploaded_by != claimant_id:
            raise PermissionError("Document does not belong to the authenticated user.")
        ocr_result = self.session.scalar(select(OCRResult).where(OCRResult.document_id == document_id))
        valid_ids = (ocr_result.structured_result_json if ocr_result else {}).get("valid_parcel_ids", [])
        if str(parcel_id) not in valid_ids:
            raise ValueError("Selected parcel was not returned as a valid resolution candidate.")
        self.eligibility_checker(
            self.session, parcel_id, min_sqm=self.overlap_min_sqm,
            min_percent=self.overlap_min_percent,
        )
        claimed_area = confirmed_fields.get("document_area_sqm")
        claim = Claim(
            claimant_id=claimant_id, parcel_id=parcel_id, document_id=document_id,
            claimed_area_sqm=Decimal(str(claimed_area)) if claimed_area is not None else None,
            confirmed_fields_json=confirmed_fields, status="matched", match_confidence=1,
            match_method="exact_composite_key", idempotency_key=idempotency_key,
        )
        self.session.add(claim)
        self.session.flush()
        record_audit(
            self.session, actor_id=claimant_id, action="claim_submitted",
            entity_type="claim", entity_id=claim.id, after={"status": claim.status},
            request_id=request_id,
        )
        self.session.flush()
        intake = ensure_intake_for_legacy_claim(
            self.session, claim, actor_id=claimant_id, request_id=request_id,
        )
        return self._serialize(claim, intake)

    def _serialize(self, claim, intake):
        return {
            "claim_id": str(claim.id), "status": claim.status,
            "fra_intake_id": str(intake.id), "fra_intake_state": intake.state,
            "conflicts": [],
        }
