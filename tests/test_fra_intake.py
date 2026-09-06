import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_models import FRAClaim, RightsHolder
from app.db.fra_operational_models import FRAIntakeItem
from app.db.models import Claim, Document, Parcel, User
from app.services.fra_intake import (
    IntakeConflictError,
    ensure_intake_for_legacy_claim,
    promote_intake,
    update_intake,
)


GEOMETRY = {
    "type": "MultiPolygon",
    "coordinates": [[[[78.0, 11.0], [78.01, 11.0], [78.01, 11.01], [78.0, 11.01], [78.0, 11.0]]]],
}


class FRAIntakeTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def _case(self, session):
        reviewer = User(external_id="intake-reviewer", role="reviewer")
        parcel = Parcel(
            state="Tamil Nadu", district="Salem", taluk="Yercaud", village="Kottur",
            survey_number="12", subdivision_number="A", geometry=GEOMETRY,
            source="test", source_version="v1", source_record_id="p-1",
        )
        session.add_all([reviewer, parcel]); session.flush()
        document = Document(
            uploaded_by=reviewer.id, storage_key="private/intake.pdf",
            original_filename="intake.pdf", content_type="application/pdf",
            sha256="b" * 64, idempotency_key="intake-doc",
        )
        session.add(document); session.flush()
        legacy = Claim(
            claimant_id=reviewer.id, parcel_id=parcel.id, document_id=document.id,
            match_method="exact", idempotency_key="legacy-intake",
        )
        holder = RightsHolder(
            display_name="Ramu", holder_type="individual", external_reference="intake-holder",
        )
        session.add_all([legacy, holder]); session.flush()
        return reviewer, legacy, holder

    def test_ensure_intake_is_idempotent_and_audited(self):
        with Session(self.engine) as session:
            reviewer, legacy, _holder = self._case(session)
            first = ensure_intake_for_legacy_claim(
                session, legacy, actor_id=reviewer.id, request_id="request-1"
            )
            second = ensure_intake_for_legacy_claim(
                session, legacy, actor_id=reviewer.id, request_id="request-2"
            )
            session.commit()

            self.assertEqual(first.id, second.id)
            self.assertEqual(first.state, "awaiting_triage")
            self.assertEqual(
                session.scalar(select(func.count()).select_from(FRAIntakeItem)), 1
            )

    def test_review_then_promotion_creates_one_linked_native_claim(self):
        with Session(self.engine) as session:
            reviewer, legacy, holder = self._case(session)
            intake = ensure_intake_for_legacy_claim(session, legacy, actor_id=reviewer.id)
            update_intake(
                session, intake, target_state="ready_for_promotion", expected_revision=0,
                reasons=["Confirmed as an IFR application"], actor_id=reviewer.id,
                triage={"right_type": "IFR", "rights_holder_id": str(holder.id)},
            )
            claim = promote_intake(
                session, intake, right_type="IFR", rights_holder_id=holder.id,
                gram_sabha_id=None, expected_revision=1, actor_id=reviewer.id,
            )
            repeated = promote_intake(
                session, intake, right_type="IFR", rights_holder_id=holder.id,
                gram_sabha_id=None, expected_revision=2, actor_id=reviewer.id,
            )
            session.commit()

            self.assertEqual(claim.id, repeated.id)
            self.assertEqual(claim.legacy_claim_id, legacy.id)
            self.assertEqual(intake.promoted_claim_id, claim.id)
            self.assertEqual(intake.state, "promoted")
            self.assertEqual(session.scalar(select(func.count()).select_from(FRAClaim)), 1)

    def test_stale_revision_and_promotion_without_triage_are_rejected(self):
        with Session(self.engine) as session:
            reviewer, legacy, holder = self._case(session)
            intake = ensure_intake_for_legacy_claim(session, legacy, actor_id=reviewer.id)
            with self.assertRaises(IntakeConflictError):
                update_intake(
                    session, intake, target_state="not_fra", expected_revision=4,
                    reasons=["Wrong revision"], actor_id=reviewer.id,
                )
            with self.assertRaises(IntakeConflictError):
                promote_intake(
                    session, intake, right_type="IFR", rights_holder_id=holder.id,
                    gram_sabha_id=None, expected_revision=0, actor_id=reviewer.id,
                )

    def test_two_sessions_cannot_review_the_same_revision(self):
        from app.db.models import AuditEvent
        with Session(self.engine) as setup:
            reviewer, legacy, holder = self._case(setup)
            item = ensure_intake_for_legacy_claim(setup, legacy, actor_id=reviewer.id)
            item_id, actor_id = item.id, reviewer.id
            setup.commit()
        with Session(self.engine) as first, Session(self.engine) as second:
            current = first.get(FRAIntakeItem, item_id)
            stale = second.get(FRAIntakeItem, item_id)
            update_intake(first, current, target_state="ready_for_promotion", expected_revision=0,
                          reasons=["Reviewed"], actor_id=actor_id)
            first.commit()
            with self.assertRaises(IntakeConflictError):
                update_intake(second, stale, target_state="not_fra", expected_revision=0,
                              reasons=["Different review"], actor_id=actor_id)
            second.rollback()
        with Session(self.engine) as check:
            self.assertEqual(check.get(FRAIntakeItem, item_id).state, "ready_for_promotion")
            self.assertEqual(check.scalar(select(func.count()).select_from(AuditEvent).where(
                AuditEvent.action == "fra_intake_reviewed")), 1)

    def test_stale_promotion_cannot_duplicate_claim_geometry_or_audit(self):
        from app.db.fra_models import FRAGeometryVersion
        from app.db.models import AuditEvent
        with Session(self.engine) as setup:
            reviewer, legacy, holder = self._case(setup)
            item = ensure_intake_for_legacy_claim(setup, legacy, actor_id=reviewer.id)
            update_intake(setup, item, target_state="ready_for_promotion", expected_revision=0,
                          reasons=["Reviewed"], actor_id=reviewer.id)
            item_id, actor_id, holder_id = item.id, reviewer.id, holder.id
            setup.commit()
        with Session(self.engine) as first, Session(self.engine) as second:
            current, stale = first.get(FRAIntakeItem, item_id), second.get(FRAIntakeItem, item_id)
            promote_intake(first, current, right_type="IFR", rights_holder_id=holder_id,
                           gram_sabha_id=None, expected_revision=1, actor_id=actor_id)
            first.commit()
            with self.assertRaises(IntakeConflictError):
                promote_intake(second, stale, right_type="IFR", rights_holder_id=holder_id,
                               gram_sabha_id=None, expected_revision=1, actor_id=actor_id)
            second.rollback()
        with Session(self.engine) as check:
            self.assertEqual(check.scalar(select(func.count()).select_from(FRAClaim)), 1)
            self.assertEqual(check.scalar(select(func.count()).select_from(FRAGeometryVersion)), 1)
            self.assertEqual(check.scalar(select(func.count()).select_from(AuditEvent).where(
                AuditEvent.action == "fra_intake_promoted")), 1)

    def test_review_invalidates_previously_ready_promotion(self):
        from app.db.models import AuditEvent
        with Session(self.engine) as setup:
            reviewer, legacy, holder = self._case(setup)
            item = ensure_intake_for_legacy_claim(setup, legacy, actor_id=reviewer.id)
            update_intake(setup, item, target_state="ready_for_promotion", expected_revision=0,
                          reasons=["Reviewed"], actor_id=reviewer.id)
            item_id, actor_id, holder_id = item.id, reviewer.id, holder.id
            setup.commit()
        with Session(self.engine) as first, Session(self.engine) as second:
            current, stale = first.get(FRAIntakeItem, item_id), second.get(FRAIntakeItem, item_id)
            update_intake(first, current, target_state="duplicate", expected_revision=1,
                          reasons=["Existing claim found"], actor_id=actor_id)
            first.commit()
            with self.assertRaises(IntakeConflictError):
                promote_intake(second, stale, right_type="IFR", rights_holder_id=holder_id,
                               gram_sabha_id=None, expected_revision=1, actor_id=actor_id)
            second.rollback()
        with Session(self.engine) as check:
            self.assertEqual(check.get(FRAIntakeItem, item_id).state, "duplicate")
            self.assertEqual(check.scalar(select(func.count()).select_from(FRAClaim)), 0)
            self.assertEqual(check.scalar(select(func.count()).select_from(AuditEvent).where(
                AuditEvent.action == "fra_intake_promoted")), 0)


if __name__ == "__main__":
    unittest.main()
