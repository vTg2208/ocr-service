import hashlib
import unittest
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_completion_models import ModelVersion
from app.db.fra_models import SchemeRuleSet
from app.db.fra_operational_models import SpatialImportBatch, SpatialReferenceFeature
from app.db.models import Document, User
from app.services.dss_engine import evaluate_rules
from app.services.dss_facts import derive_facts, fact_values
from app.services.dss_referrals import create_referral
from app.services.fra_archive import create_archive_record, create_import_batch, process_archive_extraction, promote_archive_record, review_archive_record
from app.services.fra_claims import add_geometry_version
from app.services.fra_dashboards import planner_dashboard, verifier_dashboard
from app.services.fra_entity_extraction import TamilNaduFRAExtractor
from app.services.fra_reference_spatial import evaluate_reference_intersections
from app.services.fra_reports import render_historical_evidence_report
from app.services.historical_evidence import HistoricalProcessingResult, process_historical_evidence_job, request_historical_evidence
from app.services.stac_imagery import SceneCandidate


GEOMETRY = {"type": "MultiPolygon", "coordinates": [[[[78.40, 11.70], [78.42, 11.70], [78.42, 11.72], [78.40, 11.70], [78.40, 11.70]]]]}


class Storage:
    def __init__(self): self.values = {}
    def put(self, content, suffix): key = f"journey/{len(self.values) + 1}{suffix}"; self.values[key] = content; return key
    def delete(self, key): self.values.pop(key, None)


class STAC:
    def search(self, geometry, date_range, collections, max_cloud):
        year = date_range[0].year
        return [SceneCandidate(scene_id=f"journey-{year}", provider="stac.local", collection=collections[0], acquired_at=datetime(year, 2, 1, tzinfo=timezone.utc), footprint=GEOMETRY, cloud_cover=6, asset_keys=("visual",), license_reference="https://example.org/license", private_asset_references={"visual": {"href": "https://signed.local/scene?secret=redacted"}})]


class Processor:
    version = "history-journey-v1"
    def process(self, scene, geometry, target_year):
        return HistoricalProcessingResult(content=b'{"forest_index":0.58}', statistics={"forest_index": .58}, quality_flags=["cloud_screened"], processor_version=self.version, model_version=self.version, provenance={"method": "deterministic_test_adapter"})


class FRAOperationalJourneyTests(unittest.TestCase):
    def test_real_record_to_case_evidence_dss_referral_and_dashboards(self):
        engine = create_engine("sqlite+pysqlite:///:memory:"); Base.metadata.create_all(engine)
        with Session(engine) as session:
            reviewer = User(external_id="journey-reviewer", display_name="Reviewer", role="reviewer")
            session.add(reviewer); session.flush()
            raw_text = "Claim No: TN/JOURNEY/1\nClaimant: Journey Holder\nDistrict: Salem\nBlock: Yercaud\nVillage: Kottur\nRight Type: IFR\nStatus: Pending\nClaim Year: 2025"
            document = Document(uploaded_by=reviewer.id, storage_key="journey/source.txt", original_filename="fra-source.txt", content_type="text/plain", sha256=hashlib.sha256(raw_text.encode()).hexdigest(), idempotency_key="journey-document")
            session.add(document); session.flush()
            batch = create_import_batch(session, source_label="Salem source office", state="TN", actor_id=reviewer.id, idempotency_key="journey-batch", synthetic=False, provenance={"source": "District source register", "synthetic": False})
            record = create_archive_record(session, batch=batch, document_id=document.id, legacy_reference="TN-JOURNEY-LEGACY-1", actor_id=reviewer.id, synthetic=False)
            run = process_archive_extraction(session, record, extractor=TamilNaduFRAExtractor("tn-fra-regex-v1"), manifest={"raw_text": raw_text}, raw_text=raw_text, actor_id=reviewer.id)
            review_archive_record(session, record, reviewed_fields=run.standardized_json, reviewer_id=reviewer.id, expected_revision=0)
            claim = promote_archive_record(session, record, actor_id=reviewer.id)
            geometry = add_geometry_version(session, claim, geometry=GEOMETRY, source="reviewed_boundary", provenance={"source": "field register"}, boundary_quality="surveyed", actor_id=reviewer.id)

            spatial_batch = SpatialImportBatch(dataset_kind="protected_area", source_authority="Tamil Nadu reference authority", source_version="tn-ref-1", state="published", record_count=1, valid_count=1, created_by=reviewer.id, reviewed_by=reviewer.id, idempotency_key="journey-spatial")
            session.add(spatial_batch); session.flush()
            session.add(SpatialReferenceFeature(import_batch=spatial_batch, dataset_kind="protected_area", source_authority=spatial_batch.source_authority, source_version=spatial_batch.source_version, source_record_id="PA-1", geometry=GEOMETRY, properties_json={}, provenance_json={}, published=True)); session.flush()
            findings = evaluate_reference_intersections(session, GEOMETRY, {"protected_area"}, "fra-reference-v1")
            self.assertEqual(findings[0].outcome, "review_required")

            model = ModelVersion(task="historical_evidence", adapter_type="rest", name="history", version="history-journey-v1", status="active", configuration_json={"ready": True}, label_map_json={}, metrics_json={}, registered_by=reviewer.id)
            session.add(model); session.flush()
            job = request_historical_evidence(session, claim, target_years=[2005], actor_id=reviewer.id, idempotency_key="journey-history")
            evidence = process_historical_evidence_job(session, job, stac_client=STAC(), processor=Processor(), storage=Storage(), model=model)
            self.assertEqual(evidence["status"], "completed")
            self.assertNotIn("secret=redacted", render_historical_evidence_report(session, claim.id, actor_id=reviewer.id))

            rule = SchemeRuleSet(scheme_code="TN-JOURNEY-REVIEW", display_name="Journey planning review", version="v1", required_facts_json=["claim_right_type"], condition_json={"eq": {"fact": "claim_right_type", "value": "IFR"}}, recommendation_text="Refer for a documented departmental review.", source_reference="https://example.gov.in/policy", active=True, created_by=reviewer.id)
            session.add(rule); session.flush()
            snapshot = derive_facts(session, claim, "tn-facts-v1", reviewer.id, "journey-facts")
            recommendation = evaluate_rules(session, claim_id=claim.id, facts=fact_values(snapshot), actor_id=reviewer.id, idempotency_key="journey-evaluation", rule_set_ids={rule.id}, fact_snapshot_id=snapshot.id, fact_sources=snapshot.sources_json)[0]
            referral = create_referral(session, recommendation_id=recommendation.id, department="Tribal Welfare", priority="normal", actor_id=reviewer.id, idempotency_key="journey-referral", notes="Advisory referral for human review.")
            self.assertEqual(referral.status, "referred")
            self.assertTrue(planner_dashboard(session)["recommendations"])
            self.assertGreaterEqual(verifier_dashboard(session)["totals"]["unverified_observations"], 1)
        engine.dispose()


if __name__ == "__main__": unittest.main()
