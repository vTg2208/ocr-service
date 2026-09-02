import json
import unittest
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_models import DSSRecommendation, FRAClaim, RightsHolder, SchemeRuleSet
from app.db.models import User
from app.services.dss_engine import InvalidRuleError, evaluate_condition, evaluate_rules, validate_rule_definition


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
                scheme_code="TN-FRA-WATER",
                display_name="Water Security Support",
                version="tn-sample-1",
                required_facts_json=["has_title", "water_body_present"],
                condition_json={
                    "all": [
                        {"eq": {"fact": "has_title", "value": True}},
                        {"eq": {"fact": "water_body_present", "value": False}},
                    ]
                },
                recommendation_text="Refer for departmental water-support review.",
                source_reference="synthetic://water-support/v1",
                creator=admin,
            )
            session.add_all([admin, claim, rule])
            session.commit()
            self.admin_id = admin.id
            self.claim_id = claim.id
            self.rule_id = rule.id

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
            self.assertIn("collect", result.output_json["recommendation"].casefold())
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
            self.assertEqual(result.rule_version, "tn-sample-1")
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
            self.assertIn("human review", result.output_json["recommendation"].casefold())

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

    def test_evaluation_can_be_scoped_to_selected_rule_sets(self):
        with Session(self.engine) as session:
            unrelated = SchemeRuleSet(
                scheme_code="TN-UNRELATED",
                display_name="Unrelated active rule",
                version="1",
                required_facts_json=["has_title"],
                condition_json={"present": {"fact": "has_title"}},
                recommendation_text="Unrelated recommendation",
                source_reference="policy://unrelated",
                creator=session.get(User, self.admin_id),
            )
            session.add(unrelated); session.flush()

            results = evaluate_rules(
                session,
                claim_id=self.claim_id,
                facts={"has_title": True, "water_body_present": False},
                actor_id=self.admin_id,
                idempotency_key="scoped-evaluation",
                rule_set_ids={self.rule_id},
            )

            self.assertEqual([row.rule_set_id for row in results], [self.rule_id])

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

    def test_presence_operator_does_not_treat_a_missing_row_as_absence(self):
        result = evaluate_condition({"absent": {"fact": "water_source_present"}}, {})
        self.assertIsNone(result.value)
        self.assertEqual(result.missing_inputs, {"water_source_present"})

    def test_seed_rules_are_explicitly_non_authoritative(self):
        rules = json.loads(Path("data/demo_dss_rules.json").read_text(encoding="utf-8"))
        self.assertEqual(len(rules), 3)
        for rule in rules:
            with self.subTest(code=rule["scheme_code"]):
                self.assertNotIn("demo", rule["display_name"].casefold())
                self.assertNotIn("demo", rule["scheme_code"].casefold())
                self.assertNotIn("demo", rule["version"].casefold())
                self.assertTrue(rule["source_reference"].startswith("synthetic://"))
                self.assertTrue(rule["advisory_only"])


if __name__ == "__main__":
    unittest.main()
