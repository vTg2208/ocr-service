import unittest
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_completion_models import AssetFeature, FRAVillageProfile
from app.db.fra_models import FRAClaim, FRAGeometryVersion, FRATitle, RightsHolder
from app.db.fra_operational_models import DSSFactSnapshot, ImageryArtifact, SpatialImportBatch, SpatialReferenceFeature
from app.db.models import User
from app.services.dss_facts import derive_facts


GEOMETRY = {"type": "MultiPolygon", "coordinates": [[[[79, 10], [79.1, 10], [79.1, 10.1], [79, 10], [79, 10]]]]}


class DSSFactsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            reviewer = User(external_id="facts-reviewer", role="reviewer")
            holder = RightsHolder(display_name="Private Holder", holder_type="individual")
            claim = FRAClaim(claim_number="TN-FACT-1", right_type="IFR", status="granted", rights_holder=holder, submitter=reviewer)
            session.add_all([reviewer, claim]); session.flush()
            geometry = FRAGeometryVersion(claim=claim, version=1, geometry=GEOMETRY, source="survey", boundary_quality="surveyed", created_by=reviewer.id)
            session.add(geometry); session.flush()
            village = FRAVillageProfile(state_code="TN", state_name="Tamil Nadu", district_code="TN-01", district_name="Test", block_code="TN-01-01", block_name="Test", village_code="TN-01-01-001", village_name="Test Village", boundary=GEOMETRY, tribal_groups_json=["Recorded group"], socioeconomic_json={"households": 120, "water_access": "recorded"}, provenance_json={"source": "tn-profile"}, reference_version="tn-profile-v1", synthetic=False)
            batch = SpatialImportBatch(dataset_kind="water_stress", source_authority="Tamil Nadu reference authority", source_version="tn-water-2026", state="published", record_count=1, valid_count=1, created_by=reviewer.id, reviewed_by=reviewer.id, idempotency_key="water-stress-1")
            session.add_all([village, batch]); session.flush()
            session.add(SpatialReferenceFeature(import_batch=batch, dataset_kind="water_stress", source_authority=batch.source_authority, source_version=batch.source_version, source_record_id="TN-WS-1", geometry=GEOMETRY, properties_json={"stress_score": .74, "category": "high"}, provenance_json={}, published=True))
            session.add(FRATitle(claim=claim, version=1, title_number="TN-TITLE-1", geometry_version_id=geometry.id, active=True, metadata_json={}, issued_by=reviewer.id))
            session.add_all([
                AssetFeature(claim_id=claim.id, asset_class="agricultural_land", observed_value_json={"present": True}, acquired_at=date.today(), confidence=.9, source_type="field", provenance_json={"source_version": "field-v1"}, verification_state="verified", verified_by=reviewer.id),
                AssetFeature(claim_id=claim.id, asset_class="water_body", observed_value_json={"present": True}, acquired_at=date.today(), confidence=.8, source_type="model", provenance_json={}, verification_state="unverified"),
                AssetFeature(claim_id=claim.id, asset_class="forest_cover", observed_value_json={"present": True}, acquired_at=date.today() - timedelta(days=900), confidence=.8, source_type="field", provenance_json={}, verification_state="verified", verified_by=reviewer.id),
            ])
            session.commit(); self.reviewer_id, self.claim_id, self.geometry_id = reviewer.id, claim.id, geometry.id

    def tearDown(self): self.engine.dispose()

    def test_verified_current_sources_are_derived_while_missing_and_stale_remain_unknown(self):
        with Session(self.engine) as session:
            snapshot = derive_facts(session, session.get(FRAClaim, self.claim_id), "tn-facts-v1", self.reviewer_id, "facts-1")
            self.assertIs(snapshot.facts_json["has_active_title"]["value"], True)
            self.assertIs(snapshot.facts_json["agricultural_observation"]["value"], True)
            self.assertEqual(snapshot.facts_json["water_source_present"]["value"], "unknown")
            self.assertEqual(snapshot.facts_json["forest_observation"]["value"], "unknown")
            self.assertEqual(snapshot.facts_json["forest_observation"]["reason"], "verified_source_stale")
            self.assertNotIn("Private Holder", str(snapshot.facts_json))
            self.assertTrue(snapshot.sources_json["agricultural_observation"]["source_entity_id"])
            self.assertEqual(snapshot.facts_json["village_socioeconomic"]["value"]["households"], 120)
            self.assertEqual(snapshot.facts_json["water_stress_reference"]["value"]["category"], "high")
            self.assertIn("water_source_present", snapshot.facts_json["source_quality_flags"]["value"]["unknown_facts"])

    def test_verified_coverage_can_record_explicit_absence_and_idempotency(self):
        with Session(self.engine) as session:
            session.add(ImageryArtifact(claim_id=self.claim_id, geometry_version_id=self.geometry_id, artifact_type="current_land_observation", target_year=date.today().year, storage_key="private/fact.json", content_sha256="a" * 64, processor_version="v1", parameters_json={}, statistics_json={"observation_coverage": .91, "water_source_present": False}, quality_flags_json=[], provenance_json={"legal_role": "supporting_observation"}, state="completed", verification_state="verified", reviewed_by=self.reviewer_id, reviewed_at=datetime.now(timezone.utc)))
            session.flush()
            claim = session.get(FRAClaim, self.claim_id)
            first = derive_facts(session, claim, "tn-facts-v1", self.reviewer_id, "facts-2")
            second = derive_facts(session, claim, "tn-facts-v1", self.reviewer_id, "facts-2")
            self.assertIs(first.facts_json["water_source_present"]["value"], False)
            self.assertEqual(first.id, second.id)
            self.assertEqual(session.scalar(select(func.count()).select_from(DSSFactSnapshot)), 1)


if __name__ == "__main__": unittest.main()
