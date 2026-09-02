import unittest
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_completion_models import AssetFeature, DSSReferral, ProcessingJob
from app.db.fra_models import DSSRecommendation, FRAClaim, FRAGeometryVersion, FRATitle, GramSabha, RightsHolder, SchemeRuleSet
from app.db.fra_operational_models import DSSFactSnapshot, ImageryArtifact
from app.db.models import User
from app.services.fra_dashboards import planner_dashboard, verifier_dashboard


GEOMETRY = {"type": "MultiPolygon", "coordinates": [[[[79, 10], [79.1, 10], [79.1, 10.1], [79, 10], [79, 10]]]]}


def seed_dashboard(session):
    reviewer = User(external_id="dashboard-reviewer", display_name="Verifier", role="reviewer")
    staff = User(external_id="dashboard-staff", display_name="Planner", role="user")
    sabha_a = GramSabha(name="A Sabha", village="Village A", block="Block A", district="District A", state="Tamil Nadu")
    sabha_b = GramSabha(name="B Sabha", village="Village B", block="Block B", district="District B", state="Tamil Nadu")
    holder_a = RightsHolder(display_name="Private A", holder_type="individual", gram_sabha=sabha_a)
    holder_b = RightsHolder(display_name="Private B", holder_type="community", gram_sabha=sabha_b)
    session.add_all([reviewer, staff, holder_a, holder_b]); session.flush()
    submitted = FRAClaim(claim_number="TN-DASH-1", right_type="IFR", status="submitted", rights_holder=holder_a, gram_sabha=sabha_a, submitted_by=staff.id, claimed_area_sqm=500)
    granted = FRAClaim(claim_number="TN-DASH-2", right_type="CFR", status="granted", rights_holder=holder_b, gram_sabha=sabha_b, submitted_by=staff.id, claimed_area_sqm=1000)
    session.add_all([submitted, granted]); session.flush()
    geometry = FRAGeometryVersion(claim=submitted, version=1, geometry=GEOMETRY, source="survey", boundary_quality="surveyed", created_by=reviewer.id)
    session.add(geometry); session.flush()
    session.add_all([
        FRATitle(claim=granted, version=1, title_number="TN-DASH-TITLE", active=True, metadata_json={}, issued_by=reviewer.id),
        AssetFeature(claim_id=granted.id, asset_class="water_body", observed_value_json={"present": True}, source_type="field", provenance_json={}, verification_state="verified", verified_by=reviewer.id),
        AssetFeature(claim_id=submitted.id, asset_class="well", observed_value_json={"present": True}, source_type="model", provenance_json={}, verification_state="unverified"),
        ImageryArtifact(claim_id=submitted.id, geometry_version_id=geometry.id, artifact_type="historical_land_observation:2005", target_year=2005, processor_version="v1", parameters_json={}, statistics_json={}, quality_flags_json=[], provenance_json={}, state="completed", verification_state="unverified"),
        ProcessingJob(task_type="historical_evidence", entity_type="fra_claim", entity_id=submitted.id, state="failed", attempts=3, max_attempts=3, idempotency_key="dash-job", payload_json={}, result_json={}, requested_by=reviewer.id),
    ])
    rule = SchemeRuleSet(scheme_code="JJM", display_name="Water planning", version="v1", required_facts_json=[], condition_json={"present": {"fact": "water_source_present"}}, recommendation_text="Refer for review", source_reference="https://example.gov.in/jjm", active=True, created_by=reviewer.id)
    session.add(rule); session.flush()
    recommendation = DSSRecommendation(claim=granted, rule_set=rule, rule_version="v1", actor_id=reviewer.id, idempotency_key="dash-rec", outcome="insufficient_data", input_json={}, output_json={"missing_inputs": ["water_stress_reference"], "advisory_only": True})
    session.add(recommendation); session.flush()
    session.add_all([
        DSSReferral(recommendation_id=recommendation.id, department="Rural Development", priority="normal", status="under_review", history_json=[], advisory_only=True, created_by=reviewer.id, idempotency_key="dash-ref"),
        DSSFactSnapshot(claim_id=granted.id, derivation_version="tn-facts-v1", idempotency_key="dash-facts", facts_json={"source_quality_flags": {"value": {"unknown_facts": ["water_stress_reference"]}}}, sources_json={}, created_by=reviewer.id),
    ])
    session.flush()
    return reviewer, staff, submitted, granted


class FRADashboardTests(unittest.TestCase):
    def setUp(self): self.engine = create_engine("sqlite+pysqlite:///:memory:"); Base.metadata.create_all(self.engine)
    def tearDown(self): self.engine.dispose()

    def test_verifier_dashboard_has_actionable_privacy_minimized_queues(self):
        with Session(self.engine) as session:
            seed_dashboard(session)
            result = verifier_dashboard(session)
            self.assertEqual(result["totals"]["claims_awaiting_review"], 1)
            self.assertEqual(result["totals"]["unverified_observations"], 2)
            self.assertEqual(result["totals"]["failed_or_overdue_jobs"], 1)
            self.assertIn("TN-DASH-1", str(result["queues"]))
            self.assertNotIn("Private A", str(result))

    def test_planner_dashboard_aggregates_lifecycle_assets_area_referrals_and_missing_inputs(self):
        with Session(self.engine) as session:
            seed_dashboard(session)
            result = planner_dashboard(session)
            self.assertEqual(result["claims_by_status"], {"granted": 1, "submitted": 1})
            self.assertEqual(result["claims_by_right_type"], {"CFR": 1, "IFR": 1})
            self.assertEqual(result["active_titles"], 1)
            self.assertEqual(result["granted_area_sqm"], 1000)
            self.assertEqual(result["verified_assets"], {"water_body": 1})
            self.assertEqual(result["referrals"][0]["department"], "Rural Development")
            self.assertEqual(result["missing_inputs"][0]["fact"], "water_stress_reference")
            filtered = planner_dashboard(session, district="District A")
            self.assertEqual(filtered["claims_by_status"], {"submitted": 1})
            self.assertEqual(filtered["active_titles"], 0)


if __name__ == "__main__": unittest.main()
