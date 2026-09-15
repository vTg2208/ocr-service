import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_completion_models import FRAVillageProfile
from app.db.fra_models import DSSRecommendation, FRAClaim, RightsHolder, SchemeRuleSet
from app.db.fra_operational_models import SchemeCatalogEntry
from app.db.models import User
from app.services.scheme_convergence import list_scheme_convergence
from tests.test_fra_reports import BOUNDARY


class SchemeConvergenceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def _seed(self, session):
        reviewer = User(external_id="convergence-reviewer", role="reviewer")
        session.add(reviewer); session.flush()
        village = FRAVillageProfile(
            state_code="TN", state_name="Tamil Nadu", district_code="TN-01",
            district_name="Salem", block_code="TN-01-01", block_name="Yercaud",
            village_code="TN-01-01-001", village_name="Kottachedu",
            boundary=BOUNDARY, tribal_groups_json=["Scheduled Tribe"],
            socioeconomic_json={}, provenance_json={}, reference_version="test-v1",
        )
        holder = RightsHolder(display_name="Test FRA holder", holder_type="individual")
        claim = FRAClaim(
            claim_number="TN-FRA-CONV-1", right_type="IFR", status="granted",
            rights_holder=holder, village=village, submitted_by=reviewer.id,
        )
        session.add(claim); session.flush()
        return reviewer, claim

    @staticmethod
    def _catalog(session, reviewer, code, *, target_scope="holder"):
        entry = SchemeCatalogEntry(
            scheme_code=code, display_name=f"{code} planning reference",
            version="tn-draft-1", department="Line department",
            description="Candidate convergence reference",
            source_reference=f"https://example.gov.in/{code.lower()}",
            definition_json={
                "target_scope": target_scope,
                "convergence_prerequisites": [
                    {"fact": "has_active_title", "label": "Current FRA title evidence"},
                    {"fact": "water_source_present", "label": "Current water-source observation"},
                ],
                "evidence_facts": ["has_active_title", "water_source_present"],
            },
            authoritative=False, active=False, created_by=reviewer.id,
        )
        session.add(entry); session.flush()
        return entry

    @staticmethod
    def _recommendation(session, reviewer, claim, code, outcome, *, created_at=None):
        rule_version = f"rule-{outcome}-{created_at.isoformat() if created_at else 'current'}"
        rule = SchemeRuleSet(
            scheme_code=code, display_name=f"{code} candidate review", version=rule_version,
            required_facts_json=["has_active_title", "water_source_present"],
            condition_json={"present": {"fact": "has_active_title"}},
            required_evidence_json=["has_active_title", "water_source_present"],
            recommendation_text="Refer for departmental review.", source_reference="candidate://rule",
            created_by=reviewer.id,
        )
        session.add(rule); session.flush()
        output = {
            "reasons": ["Current FRA title evidence is present."],
            "missing_inputs": ["water_source_present"] if outcome == "insufficient_data" else [],
            "required_evidence": ["has_active_title", "water_source_present"],
            "required_assets": [], "unmet_assets": [], "priority": "high",
            "priority_reasons": ["Water stress requires earlier review."],
            "recommendation": {
                "recommended": "Refer for departmental review.",
                "not_recommended": "Current evidence does not indicate a referral.",
                "insufficient_data": "Collect current water evidence.",
            }[outcome],
        }
        recommendation = DSSRecommendation(
            claim=claim, rule_set=rule, rule_version=rule.version, actor_id=reviewer.id,
            idempotency_key=f"{code}-{outcome}-{created_at}", outcome=outcome,
            input_json={
                "facts": {
                    "has_active_title": True, "water_source_present": None,
                    "agricultural_observation": True,
                    "forest_cover_present": False,
                    "infrastructure_services": ["school"],
                    "asset_deficiency_indicators": ["water_body_not_observed"],
                    "source_quality_flags": {
                        "unknown_facts": ["road_access"], "stale_facts": []
                    },
                },
                "fact_sources": {
                    "has_active_title": {
                        "source_entity_type": "fra_title", "source_entity_id": "title-1",
                        "source_version": "1", "verification_state": "verified",
                        "observed_at": "2026-08-01",
                    }
                },
            },
            output_json=output,
            created_at=created_at or datetime.now(timezone.utc),
        )
        session.add(recommendation); session.flush()
        return recommendation

    def test_convergence_exposes_positive_candidate_evidence_and_catalog_status(self):
        with Session(self.engine) as session:
            reviewer, claim = self._seed(session)
            self._catalog(session, reviewer, "JJM")
            recommendation = self._recommendation(session, reviewer, claim, "JJM", "recommended")
            result = list_scheme_convergence(session, claim_id=claim.id)

            self.assertEqual(result["summary"], {
                "potentially_eligible": 1, "not_indicated": 0,
                "insufficient_data": 0, "not_evaluated": 0,
            })
            item = result["items"][0]
            self.assertEqual(item["recommendation_id"], str(recommendation.id))
            self.assertEqual(item["convergence_status"], "potentially_eligible")
            self.assertTrue(item["potentially_eligible"])
            self.assertEqual(item["catalog_status"], "draft_inactive")
            self.assertEqual(item["priority"], "high")
            self.assertEqual(item["reason_for_recommendation"], "Refer for departmental review.")
            self.assertIsNone(item["reason_for_rejection"])
            self.assertEqual(item["missing_prerequisites"], [])
            self.assertEqual(item["supporting_evidence"][0]["fact"], "has_active_title")
            self.assertEqual(
                {asset["asset"] for asset in item["mapped_assets"]},
                {"agricultural_land", "infrastructure"},
            )
            self.assertEqual(item["deficiencies"], ["water_body_not_observed"])
            self.assertIn("road_access", item["unknown_data"])
            self.assertEqual(item["recommended_interventions"], [])
            self.assertEqual(item["holder"]["display_name"], "Test FRA holder")
            self.assertEqual(item["village"]["name"], "Kottachedu")
            self.assertFalse(item["official_eligibility_decision"])

    def test_latest_result_reports_rejection_or_missing_information(self):
        with Session(self.engine) as session:
            reviewer, claim = self._seed(session)
            self._catalog(session, reviewer, "PM-KISAN")
            old = datetime.now(timezone.utc) - timedelta(days=1)
            self._recommendation(session, reviewer, claim, "PM-KISAN", "recommended", created_at=old)
            current = self._recommendation(session, reviewer, claim, "PM-KISAN", "insufficient_data")
            item = list_scheme_convergence(session, claim_id=claim.id)["items"][0]

            self.assertEqual(item["recommendation_id"], str(current.id))
            self.assertEqual(item["convergence_status"], "insufficient_data")
            self.assertIsNone(item["potentially_eligible"])
            self.assertIn("water_source_present", item["missing_information"])
            self.assertEqual(
                item["missing_prerequisites"],
                [{"fact": "water_source_present", "label": "Current water-source observation"}],
            )
            self.assertIsNone(item["reason_for_rejection"])

    def test_catalog_only_scheme_remains_visible_as_not_evaluated(self):
        with Session(self.engine) as session:
            reviewer, claim = self._seed(session)
            self._catalog(session, reviewer, "DAJGUA", target_scope="village")
            item = list_scheme_convergence(session, claim_id=claim.id)["items"][0]

            self.assertEqual(item["convergence_status"], "not_evaluated")
            self.assertIsNone(item["recommendation_id"])
            self.assertIsNone(item["potentially_eligible"])
            self.assertEqual(item["missing_information"], ["dss_evaluation_required"])
            self.assertEqual(len(item["missing_prerequisites"]), 2)

    def test_visible_claim_scope_and_location_filters_are_enforced(self):
        with Session(self.engine) as session:
            reviewer, claim = self._seed(session)
            self._catalog(session, reviewer, "JJM")
            visible = list_scheme_convergence(
                session, visible_claim_ids=[claim.id], district=" salem ", village="kottachedu"
            )
            hidden = list_scheme_convergence(session, visible_claim_ids=[], district="Salem")
            wrong_place = list_scheme_convergence(session, district="Dharmapuri")
            self.assertEqual(len(visible["items"]), 1)
            self.assertEqual(hidden["items"], [])
            self.assertEqual(wrong_place["items"], [])

    def test_fra_category_and_intervention_filters_apply_to_holder_and_village_rows(self):
        with Session(self.engine) as session:
            reviewer, claim = self._seed(session)
            catalog = self._catalog(session, reviewer, "JJM", target_scope="holder_and_village")
            catalog.definition_json = {
                **catalog.definition_json,
                "applicable_right_types": ["IFR", "CR", "CFR"],
                "intervention_types": ["drinking_water", "water_source_strengthening"],
            }
            self._recommendation(session, reviewer, claim, "JJM", "recommended")
            matching = list_scheme_convergence(
                session, right_type="IFR", intervention_type="drinking_water"
            )
            self.assertEqual(len(matching["items"]), 1)
            self.assertEqual(matching["items"][0]["recommended_interventions"], [
                "drinking_water", "water_source_strengthening"
            ])
            self.assertEqual(list_scheme_convergence(
                session, right_type="CFR", intervention_type="drinking_water"
            )["items"], [])
            self.assertEqual(list_scheme_convergence(
                session, right_type="IFR", intervention_type="rural_housing"
            )["items"], [])


if __name__ == "__main__":
    unittest.main()
