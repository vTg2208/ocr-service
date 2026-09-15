import json
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_completion_models import FRAVillageProfile
from app.db.fra_models import FRAClaim, FRAGeometryVersion, RightsHolder, SchemeRuleSet
from app.db.fra_operational_models import (
    ImageryArtifact,
    SchemeCatalogEntry,
    SpatialImportBatch,
    SpatialReferenceFeature,
)
from app.db.models import User
from app.services.dss_engine import evaluate_rules
from app.services.dss_facts import CURRENT_FACT_VERSION, derive_facts, fact_values
from app.services.scheme_convergence import list_scheme_convergence
from tests.test_fra_reports import BOUNDARY


class CompleteWaterDSSJourneyTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    @staticmethod
    def _reference(session, actor, kind, version, properties):
        batch = SpatialImportBatch(
            dataset_kind=kind, source_authority="Test published reference authority",
            source_version=version, state="published", record_count=1, valid_count=1,
            created_by=actor.id, reviewed_by=actor.id,
            idempotency_key=f"water-journey-{kind}",
        )
        session.add(batch); session.flush()
        reference = SpatialReferenceFeature(
            import_batch=batch, dataset_kind=kind,
            source_authority=batch.source_authority, source_version=version,
            source_record_id=f"TN-{kind}-1", geometry=BOUNDARY,
            properties_json=properties, provenance_json={"published_for_test": True},
            published=True,
        )
        session.add(reference)
        return reference

    def test_database_to_fact_to_jjm_rule_to_visible_spatial_evidence(self):
        with Session(self.engine) as session:
            actor = User(external_id="water-journey-reviewer", role="reviewer")
            village = FRAVillageProfile(
                state_code="TN", state_name="Tamil Nadu", district_code="TN-01",
                district_name="Salem", block_code="TN-01-01", block_name="Yercaud",
                village_code="TN-01-01-001", village_name="Water Gap Village",
                boundary=BOUNDARY, tribal_groups_json=["Recorded tribal group"],
                socioeconomic_json={"households": 80}, provenance_json={},
                reference_version="village-v1",
            )
            claim = FRAClaim(
                claim_number="TN-FRA-WATER-1", right_type="CFR", status="granted",
                rights_holder=RightsHolder(display_name="Water Gap Community", holder_type="community"),
                village=village, submitter=actor,
            )
            session.add_all([actor, village, claim]); session.flush()
            geometry = FRAGeometryVersion(
                claim=claim, version=1, geometry=BOUNDARY, source="community_mapping",
                boundary_quality="reviewed", created_by=actor.id,
            )
            session.add(geometry); session.flush()
            water_evidence = ImageryArtifact(
                claim_id=claim.id, geometry_version_id=geometry.id,
                artifact_type="asset_assessment:water_body", target_year=2026,
                storage_key="private/water-assessment.tif", content_sha256="a" * 64,
                processor_version="water-assessment-v1", parameters_json={},
                statistics_json={"observation_coverage": 0.96, "water_source_present": False},
                quality_flags_json=[], provenance_json={"legal_role": "supporting_observation"},
                state="completed", verification_state="verified", reviewed_by=actor.id,
                reviewed_at=datetime.now(timezone.utc),
            )
            groundwater = self._reference(
                session, actor, "groundwater_stress", "groundwater-2026", {"status": "critical"}
            )
            water_stress = self._reference(
                session, actor, "water_stress", "water-stress-2026", {"category": "high"}
            )
            catalog = SchemeCatalogEntry(
                scheme_code="JJM", display_name="Jal Jeevan Mission planning reference",
                version="tn-draft-2026-02", department="Water Supply",
                description="Candidate convergence reference",
                source_reference="https://jaljeevanmission.gov.in/",
                definition_json={
                    "target_scope": "holder_and_village",
                    "intervention_types": ["drinking_water", "water_source_strengthening"],
                    "convergence_prerequisites": [
                        {"fact": "water_source_present", "label": "Current verified water-source observation"},
                        {"fact": "groundwater_status", "label": "Published groundwater context"},
                        {"fact": "water_stress_status", "label": "Published water-stress context"},
                    ],
                    "evidence_facts": ["water_source_present", "groundwater_status", "water_stress_status"],
                },
                effective_from=date(2026, 1, 1), effective_to=date(2026, 12, 31),
                approving_authority="Competent test authority",
                authoritative=True, active=True, created_by=actor.id,
            )
            definition = next(
                row for row in json.loads(Path("data/demo_dss_rules.json").read_text(encoding="utf-8"))
                if row["scheme_code"] == "JJM"
            )
            rule = SchemeRuleSet(
                catalog_entry_id=catalog.id,
                scheme_code=definition["scheme_code"], display_name=definition["display_name"],
                version=definition["version"], effective_from=date(2026, 1, 1),
                effective_to=date(2026, 12, 31), required_facts_json=definition["required_facts"],
                condition_json=definition["condition"],
                required_evidence_json=definition["required_evidence"],
                required_assets_json=definition["required_assets"],
                exclusion_condition_json=definition["exclusion_condition"],
                priority_conditions_json=definition["priority_conditions"],
                freshness_requirements_json=definition["freshness_requirements"],
                recommendation_logic_json=definition["recommendation_logic"],
                recommendation_text=definition["recommendation_text"],
                source_reference=definition["source_reference"], active=True, created_by=actor.id,
            )
            session.add_all([water_evidence, catalog, rule]); session.flush()

            snapshot = derive_facts(
                session, claim, CURRENT_FACT_VERSION, actor.id, "complete-water-facts"
            )
            self.assertIs(snapshot.facts_json["water_source_present"]["value"], False)
            self.assertEqual(snapshot.facts_json["groundwater_status"]["value"], "critical")
            self.assertEqual(snapshot.facts_json["water_stress_status"]["value"], "high")
            recommendations = evaluate_rules(
                session, claim_id=claim.id, facts=fact_values(snapshot), actor_id=actor.id,
                idempotency_key="complete-water-evaluation", rule_set_ids={rule.id},
                fact_snapshot_id=snapshot.id, fact_sources=snapshot.sources_json,
            )
            self.assertEqual(recommendations[0].outcome, "recommended")
            self.assertEqual(recommendations[0].output_json["priority"], "urgent")

            item = list_scheme_convergence(session, claim_id=claim.id, scheme_code="JJM")["items"][0]
            self.assertEqual(item["convergence_status"], "potentially_eligible")
            self.assertEqual(item["priority"], "urgent")
            self.assertEqual(item["missing_prerequisites"], [])
            evidence = {entry["fact"]: entry for entry in item["supporting_evidence"]}
            self.assertEqual(evidence["water_source_present"]["source_entity_id"], str(water_evidence.id))
            self.assertEqual(evidence["groundwater_status"]["source_entity_id"], str(groundwater.id))
            self.assertEqual(evidence["water_stress_status"]["source_entity_id"], str(water_stress.id))
            self.assertIn("Jal Jeevan Mission", item["reason_for_recommendation"])
            self.assertFalse(item["official_eligibility_decision"])


if __name__ == "__main__":
    unittest.main()
