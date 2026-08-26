import json
import unittest
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_models import DSSRecommendation, FRAClaim, RightsHolder, SchemeRuleSet
from app.db.models import User
from app.services.dss_engine import InvalidRuleError, evaluate_rules, validate_rule_definition


class DSSEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            admin = User(external_id="dss-admin", role="admin")
            holder = RightsHolder(display_name="Ramu Naik", holder_type="individual")
            claim = FRAClaim(
                claim_number="IFR-DSS-1",
                right_type="IFR",
                status="granted",
                rights_holder=holder,
                submitter=admin,
            )
            rule = SchemeRuleSet(
                scheme_code="DEMO-WATER",
                display_name="Demo Water Support",
                version="demo-1",
                required_facts_json=["has_title", "water_body_present"],
                condition_json={
                    "all": [
                        {"eq": {"fact": "has_title", "value": True}},
                        {"eq": {"fact": "water_body_present", "value": False}},
                    ]
                },
                recommendation_text="Refer for departmental water-support review.",
                source_reference="demo://water-support/v1",
                creator=admin,
            )
            session.add_all([admin, claim, rule])
            session.commit()
            self.admin_id = admin.id
            self.claim_id = claim.id

    def tearDown(self):
        self.engine.dispose()

    def test_missing_fact_returns_insufficient_data(self):
        with Session(self.engine) as session:
            result = evaluate_rules(
                session,
                claim_id=self.claim_id,
                facts={"has_title": True},
                actor_id=self.admin_id,
                idempotency_key="dss-1",
            )[0]
            session.commit()

            self.assertEqual(result.outcome, "insufficient_data")
            self.assertEqual(result.output_json["missing_inputs"], ["water_body_present"])
            self.assertTrue(result.output_json["advisory_only"])

    def test_recommendation_retains_rule_version_and_reasons(self):
        with Session(self.engine) as session:
            result = evaluate_rules(
                session,
                claim_id=self.claim_id,
                facts={"has_title": True, "water_body_present": False},
                actor_id=self.admin_id,
                idempotency_key="dss-2",
            )[0]
            session.commit()

            self.assertEqual(result.outcome, "recommended")
            self.assertEqual(result.rule_version, "demo-1")
            self.assertTrue(result.output_json["reasons"])
            self.assertTrue(result.output_json["advisory_only"])
            self.assertIn("departmental review", result.output_json["disclaimer"])

    def test_false_condition_is_not_recommended(self):
        with Session(self.engine) as session:
            result = evaluate_rules(
                session,
                claim_id=self.claim_id,
                facts={"has_title": False, "water_body_present": True},
                actor_id=self.admin_id,
                idempotency_key="dss-3",
            )[0]
            self.assertEqual(result.outcome, "not_recommended")

    def test_repeated_idempotency_key_reuses_recommendation(self):
        with Session(self.engine) as session:
            first = evaluate_rules(
                session,
                claim_id=self.claim_id,
                facts={"has_title": True, "water_body_present": False},
                actor_id=self.admin_id,
                idempotency_key="same-evaluation",
            )[0]
            session.flush()
            second = evaluate_rules(
                session,
                claim_id=self.claim_id,
                facts={"has_title": False, "water_body_present": True},
                actor_id=self.admin_id,
                idempotency_key="same-evaluation",
            )[0]
            self.assertEqual(first.id, second.id)
            self.assertEqual(
                session.scalar(select(func.count()).select_from(DSSRecommendation)), 1
            )

    def test_rule_language_rejects_arbitrary_operator(self):
        with self.assertRaises(InvalidRuleError):
            validate_rule_definition({"exec": "import os"})

    def test_rule_language_validates_nested_comparisons(self):
        condition = {
            "any": [
                {"gte": {"fact": "forest_cover", "value": 0.5}},
                {"present": {"fact": "community_plan"}},
            ]
        }
        self.assertEqual(validate_rule_definition(condition), condition)

    def test_seed_rules_are_explicitly_non_authoritative(self):
        rules = json.loads(Path("data/demo_dss_rules.json").read_text(encoding="utf-8"))
        self.assertEqual(len(rules), 3)
        for rule in rules:
            with self.subTest(code=rule["scheme_code"]):
                self.assertIn("Demo", rule["display_name"])
                self.assertTrue(rule["source_reference"].startswith("demo://"))
                self.assertTrue(rule["advisory_only"])


if __name__ == "__main__":
    unittest.main()
