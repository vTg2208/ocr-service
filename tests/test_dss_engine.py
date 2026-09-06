import json
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_models import DSSRecommendation, FRAClaim, RightsHolder, SchemeRuleSet
from app.db.models import User
from app.services.dss_engine import (
    InvalidRuleError,
    evaluate_condition,
    evaluate_rules,
    validate_rule_definition,
    validate_rule_fact_contract,
    validate_rule_configuration,
)


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

    def test_registered_rules_use_only_declared_canonical_fact_names(self):
        condition = {
            "all": [
                {"eq": {"fact": "has_active_title", "value": True}},
                {"eq": {"fact": "water_source_present", "value": False}},
            ]
        }
        validate_rule_fact_contract(
            ["has_active_title", "water_source_present"], condition
        )
        with self.assertRaisesRegex(InvalidRuleError, "Unsupported DSS fact"):
            validate_rule_fact_contract(
                ["has_title"], {"present": {"fact": "has_title"}}
            )
        with self.assertRaisesRegex(InvalidRuleError, "declared"):
            validate_rule_fact_contract(
                ["has_active_title"], condition
            )

    def test_presence_operator_does_not_treat_a_missing_row_as_absence(self):
        result = evaluate_condition({"absent": {"fact": "water_source_present"}}, {})
        self.assertIsNone(result.value)
        self.assertEqual(result.missing_inputs, {"water_source_present"})

    def test_seed_rules_are_explicitly_non_authoritative(self):
        rules = json.loads(Path("data/demo_dss_rules.json").read_text(encoding="utf-8"))
        self.assertEqual(len(rules), 5)
        self.assertEqual(
            {rule["scheme_code"] for rule in rules},
            {"PM-KISAN", "MGNREGA", "PMAY-G", "JJM", "DAJGUA"},
        )
        for rule in rules:
            with self.subTest(code=rule["scheme_code"]):
                self.assertNotIn("demo", rule["display_name"].casefold())
                self.assertNotIn("demo", rule["scheme_code"].casefold())
                self.assertNotIn("demo", rule["version"].casefold())
                self.assertTrue(rule["source_reference"].startswith("candidate-convergence://"))
                self.assertTrue(rule["advisory_only"])
                validate_rule_fact_contract(
                    rule["required_facts"], rule["condition"]
                )
                validate_rule_configuration(
                    required_facts=rule["required_facts"],
                    required_evidence=rule["required_evidence"],
                    required_assets=rule["required_assets"],
                    exclusion_condition=rule["exclusion_condition"],
                    priority_conditions=rule["priority_conditions"],
                    freshness_requirements=rule["freshness_requirements"],
                    recommendation_logic=rule["recommendation_logic"],
                )

    def test_complete_rule_evaluates_evidence_assets_exclusions_freshness_and_priority(self):
        with Session(self.engine) as session:
            rule = SchemeRuleSet(
                scheme_code="COMPLETE-RULE",
                display_name="Complete advisory rule",
                version="1",
                required_facts_json=[
                    "has_active_title", "water_source_present", "road_access"
                ],
                condition_json={"all": [
                    {"eq": {"fact": "has_active_title", "value": True}},
                    {"eq": {"fact": "water_source_present", "value": False}},
                ]},
                required_evidence_json=["water_source_present"],
                required_assets_json=["road"],
                exclusion_condition_json={
                    "eq": {"fact": "claim_status", "value": "rejected"}
                },
                priority_conditions_json=[{
                    "priority": "high",
                    "condition": {
                        "eq": {"fact": "groundwater_status", "value": "critical"}
                    },
                    "reason": "Critical groundwater context.",
                }],
                freshness_requirements_json={"water_source_present": 30},
                recommendation_logic_json={
                    "recommended": "Send for high priority departmental review.",
                    "not_recommended": "Do not refer under this rule.",
                    "insufficient_data": "Collect current water evidence.",
                },
                recommendation_text="Fallback review text.",
                source_reference="policy://complete",
                creator=session.get(User, self.admin_id),
            )
            session.add(rule)
            session.flush()
            facts = {
                "has_active_title": True,
                "water_source_present": False,
                "road_access": True,
                "claim_status": "granted",
                "groundwater_status": "critical",
            }
            sources = {
                "water_source_present": {
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "verification_state": "verified",
                }
            }

            recommended = evaluate_rules(
                session, claim_id=self.claim_id, facts=facts,
                actor_id=self.admin_id, idempotency_key="complete-current",
                rule_set_ids={rule.id}, fact_snapshot_id=uuid.uuid4(),
                fact_sources=sources,
            )[0]
            stale = evaluate_rules(
                session, claim_id=self.claim_id, facts=facts,
                actor_id=self.admin_id, idempotency_key="complete-stale",
                rule_set_ids={rule.id}, fact_snapshot_id=uuid.uuid4(),
                fact_sources={"water_source_present": {
                    "observed_at": (
                        datetime.now(timezone.utc) - timedelta(days=31)
                    ).isoformat(),
                    "verification_state": "verified",
                }},
            )[0]
            excluded = evaluate_rules(
                session, claim_id=self.claim_id,
                facts={**facts, "claim_status": "rejected"},
                actor_id=self.admin_id, idempotency_key="complete-excluded",
                rule_set_ids={rule.id}, fact_snapshot_id=uuid.uuid4(),
                fact_sources=sources,
            )[0]

            self.assertEqual(recommended.outcome, "recommended")
            self.assertEqual(recommended.output_json["priority"], "high")
            self.assertEqual(
                recommended.output_json["recommendation"],
                "Send for high priority departmental review.",
            )
            self.assertEqual(recommended.output_json["required_assets"], ["road"])
            self.assertEqual(stale.outcome, "insufficient_data")
            self.assertEqual(
                stale.output_json["recommendation"],
                "Collect current water evidence.",
            )
            self.assertIn(
                "water_source_present", stale.output_json["missing_inputs"]
            )
            self.assertEqual(excluded.outcome, "not_recommended")
            self.assertEqual(
                excluded.output_json["recommendation"],
                "Do not refer under this rule.",
            )


if __name__ == "__main__":
    unittest.main()
