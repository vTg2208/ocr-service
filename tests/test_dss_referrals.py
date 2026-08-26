import unittest
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_models import DSSRecommendation, FRAClaim, RightsHolder, SchemeRuleSet
from app.db.models import User
from app.services.dss_referrals import (
    ReferralConflictError,
    ReferralValidationError,
    create_referral,
    update_referral,
)


class DSSReferralTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def _seed(self, session):
        reviewer = User(
            external_id=f"planner-{uuid.uuid4()}", display_name="Planner", role="reviewer"
        )
        session.add(reviewer)
        session.flush()
        holder = RightsHolder(display_name="Synthetic holder", holder_type="individual")
        claim = FRAClaim(
            claim_number=f"TN-DSS-{uuid.uuid4()}", right_type="IFR", status="granted",
            rights_holder=holder, submitted_by=reviewer.id,
        )
        rule = SchemeRuleSet(
            scheme_code="DEMO-WATER", display_name="Demo Water Support", version="demo-v1",
            required_facts_json=["has_water"],
            condition_json={"eq": {"fact": "has_water", "value": False}},
            recommendation_text="Refer for departmental water review.",
            source_reference="demo://water", created_by=reviewer.id,
        )
        session.add_all([claim, rule])
        session.flush()
        recommendation = DSSRecommendation(
            claim=claim, rule_set=rule, rule_version=rule.version, actor_id=reviewer.id,
            idempotency_key="evaluation-1", outcome="recommended",
            input_json={"facts": {"has_water": False}},
            output_json={
                "reasons": ["has_water is false"], "missing_inputs": [],
                "advisory_only": True,
            },
        )
        session.add(recommendation)
        session.flush()
        return reviewer, recommendation

    def test_planner_referral_is_advisory_and_retains_history(self):
        with Session(self.engine) as session:
            reviewer, recommendation = self._seed(session)
            referral = create_referral(
                session,
                recommendation_id=recommendation.id,
                department="Rural Development",
                priority="high",
                actor_id=reviewer.id,
                idempotency_key="ref-1",
            )
            update_referral(
                session,
                referral,
                status="under_review",
                notes="Assigned locally",
                assigned_to="District demo desk",
                actor_id=reviewer.id,
                expected_revision=0,
            )
            session.commit()

            self.assertEqual(referral.history_json[-1]["status"], "under_review")
            self.assertTrue(referral.advisory_only)
            self.assertEqual(referral.revision, 1)

    def test_referral_creation_is_idempotent_and_one_per_recommendation(self):
        with Session(self.engine) as session:
            reviewer, recommendation = self._seed(session)
            first = create_referral(
                session, recommendation_id=recommendation.id, department="Tribal Welfare",
                priority="normal", actor_id=reviewer.id, idempotency_key="same",
            )
            second = create_referral(
                session, recommendation_id=recommendation.id, department="Tribal Welfare",
                priority="normal", actor_id=reviewer.id, idempotency_key="same",
            )
            self.assertEqual(first.id, second.id)
            with self.assertRaises(ReferralConflictError):
                create_referral(
                    session, recommendation_id=recommendation.id, department="Another Department",
                    priority="high", actor_id=reviewer.id, idempotency_key="different",
                )

    def test_invalid_statuses_and_stale_updates_are_rejected(self):
        with Session(self.engine) as session:
            reviewer, recommendation = self._seed(session)
            referral = create_referral(
                session, recommendation_id=recommendation.id, department="Tribal Welfare",
                priority="normal", actor_id=reviewer.id, idempotency_key="ref-invalid",
            )
            with self.assertRaisesRegex(ReferralValidationError, "approve or sanction"):
                update_referral(
                    session, referral, status="approved", notes="", assigned_to=None,
                    actor_id=reviewer.id, expected_revision=0,
                )
            with self.assertRaises(ReferralConflictError):
                update_referral(
                    session, referral, status="under_review", notes="", assigned_to=None,
                    actor_id=reviewer.id, expected_revision=4,
                )
            self.assertEqual(referral.status, "referred")


if __name__ == "__main__":
    unittest.main()
