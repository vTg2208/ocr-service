import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_models import FRAClaim, FRAGeometryVersion, RightsHolder
from app.db.models import AuditEvent, User
from app.services.fra_workflow import (
    InvalidTransitionError,
    TitleIssuanceError,
    issue_title,
    transition_claim,
)


GEOMETRY = {
    "type": "MultiPolygon",
    "coordinates": [[[[79.0, 10.0], [79.001, 10.0], [79.001, 10.001], [79.0, 10.001], [79.0, 10.0]]]],
}


class FRAWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            reviewer = User(external_id="reviewer", display_name="Reviewer", role="reviewer")
            holder = RightsHolder(display_name="Ramu Naik", holder_type="individual")
            claim = FRAClaim(
                claim_number="IFR-001",
                right_type="IFR",
                status="draft",
                rights_holder=holder,
                submitter=reviewer,
            )
            session.add(claim)
            session.commit()
            self.reviewer_id = reviewer.id
            self.claim_id = claim.id

    def tearDown(self):
        self.engine.dispose()

    def test_valid_transition_changes_state_and_appends_decision_and_audit(self):
        with Session(self.engine) as session:
            claim = session.get(FRAClaim, self.claim_id)
            decision = transition_claim(
                session,
                claim,
                target_status="submitted",
                authority_level="frc",
                outcome="submitted",
                reasons=["Form A received"],
                actor_id=self.reviewer_id,
                request_id="req-1",
            )
            session.commit()

            self.assertEqual(claim.status, "submitted")
            self.assertEqual(decision.from_status, "draft")
            self.assertEqual(decision.to_status, "submitted")
            audit = session.scalar(select(AuditEvent).where(AuditEvent.entity_id == claim.id))
            self.assertEqual(audit.action, "fra_claim_transitioned")

    def test_invalid_transition_preserves_claim_state_and_lists_allowed_states(self):
        with Session(self.engine) as session:
            claim = session.get(FRAClaim, self.claim_id)
            with self.assertRaises(InvalidTransitionError) as raised:
                transition_claim(
                    session,
                    claim,
                    target_status="granted",
                    authority_level="dlc",
                    outcome="granted",
                    reasons=[],
                    actor_id=self.reviewer_id,
                    request_id="req-2",
                )

            self.assertEqual(claim.status, "draft")
            self.assertEqual(raised.exception.allowed_states, {"submitted"})

    def test_adverse_transition_requires_a_reason(self):
        with Session(self.engine) as session:
            claim = session.get(FRAClaim, self.claim_id)
            claim.status = "dlc_decided"
            with self.assertRaises(InvalidTransitionError) as raised:
                transition_claim(
                    session,
                    claim,
                    target_status="rejected",
                    authority_level="dlc",
                    outcome="rejected",
                    reasons=[],
                    actor_id=self.reviewer_id,
                    request_id="req-3",
                )
            self.assertIn("reason", str(raised.exception).casefold())
            self.assertEqual(claim.status, "dlc_decided")

    def test_titles_are_versioned_and_only_latest_is_active(self):
        with Session(self.engine) as session:
            claim = session.get(FRAClaim, self.claim_id)
            claim.status = "granted"
            geometry = FRAGeometryVersion(
                claim=claim,
                version=1,
                geometry=GEOMETRY,
                source="verified_map",
                boundary_quality="verified",
                created_by=self.reviewer_id,
            )
            session.add(geometry)
            session.flush()
            first = issue_title(
                session,
                claim,
                title_number="TITLE-001",
                geometry_version_id=geometry.id,
                issued_by=self.reviewer_id,
                metadata={"correction": False},
                request_id="title-1",
            )
            second = issue_title(
                session,
                claim,
                title_number="TITLE-001-R1",
                geometry_version_id=geometry.id,
                issued_by=self.reviewer_id,
                metadata={"correction": True},
                request_id="title-2",
            )
            session.commit()

            self.assertEqual((first.version, first.active), (1, False))
            self.assertEqual((second.version, second.active), (2, True))

    def test_title_requires_granted_claim(self):
        with Session(self.engine) as session:
            claim = session.get(FRAClaim, self.claim_id)
            with self.assertRaises(TitleIssuanceError):
                issue_title(
                    session,
                    claim,
                    title_number="TITLE-INVALID",
                    geometry_version_id=None,
                    issued_by=self.reviewer_id,
                    metadata={},
                    request_id="title-invalid",
                )


if __name__ == "__main__":
    unittest.main()
