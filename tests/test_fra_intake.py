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


if __name__ == "__main__":
    unittest.main()
