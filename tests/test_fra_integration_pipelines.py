"""End-to-end contracts for the six FRA implementation pipelines.

External ML runtimes are represented by deterministic adapters. The tests exercise
the application's real PDF renderer, persistence, review, spatial, Atlas, asset,
fact, rule, planner, and report boundaries without claiming model accuracy.
"""

from datetime import date, datetime, timezone
from io import BytesIO
from unittest.mock import patch
import uuid

from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_completion_models import (
    FRAArchiveRecord,
    FRAVillageProfile,
    ModelVersion,
    ProcessingJob,
)
from app.db.fra_models import FRAClaim, FRATitle, RightsHolder, SchemeRuleSet
from app.db.fra_operational_models import ImageryArtifact, ImagerySceneRecord, SchemeCatalogEntry
from app.db.models import Claim, Document, Parcel, User
from app.services.dss_engine import evaluate_rules
from app.services.dss_facts import CURRENT_FACT_VERSION, derive_facts, fact_values
from app.services.fra_archive import promote_archive_record, review_archive_record
from app.services.fra_assets import enqueue_asset_inference, process_asset_inference, review_asset
from app.services.fra_atlas import AtlasFilters, atlas_features
from app.services.fra_claims import add_geometry_version
from app.services.fra_document_intake import ArchiveUpload, ingest_archive_batch
from app.services.fra_intake import ensure_intake_for_legacy_claim, promote_intake, update_intake
from app.services.fra_job_handlers import get_job_handler
from app.services.fra_reports import render_village_report
from app.services.model_gateway import ManifestAssetDetector
from app.services.ocr_engine import OCRLineResult, OCRPageAnalysis
from app.services.scheme_convergence import list_scheme_convergence
from app.services.village_asset_profiles import refresh_village_asset_profiles


BOUNDARY = {
    "type": "MultiPolygon",
    "coordinates": [[[[78.40, 11.70], [78.43, 11.70], [78.43, 11.73], [78.40, 11.73], [78.40, 11.70]]]],
}


class MemoryStorage:
    def __init__(self):
        self.values = {}

    def put(self, content, suffix):
        key = f"private/integration-{len(self.values) + 1}{suffix}"
        self.values[key] = content
        return key

    def read(self, key):
        return self.values[key]

    def delete(self, key):
        self.values.pop(key, None)


class CleanScanner:
    def scan(self, _content):
        return None


class DeterministicOCR:
    """Test double for the external OCR runtime; PDF rendering remains real."""

    text = (
        "Claim No: TN/IFR/PIPE/1\nClaimant: Malar\nDistrict: Salem\n"
        "Block: Yercaud\nVillage: Kottur\nRight Type: IFR\nStatus: Granted\n"
        "Title No: TN-TITLE-PIPE-1\nDecision Date: 01/06/2025"
    )

    def extract_page(self, image):
        assert image.ndim in {2, 3}
        lines = [
            OCRLineResult(line, 0.94, [10.0, float(index * 20), 400.0, float(index * 20 + 16)])
            for index, line in enumerate(self.text.splitlines(), start=1)
        ]
        return OCRPageAnalysis(self.text, 94.0, lines)


def _pdf_bytes():
    output = BytesIO()
    Image.new("RGB", (640, 900), "white").save(output, format="PDF")
    return output.getvalue()


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _actor_village_claim(session, suffix):
    actor = User(external_id=f"pipeline-reviewer-{suffix}", display_name="Pipeline Reviewer", role="reviewer")
    village = FRAVillageProfile(
        state_code="TN", state_name="Tamil Nadu", district_code="TN-01",
        district_name="Salem", block_code="TN-01-01", block_name="Yercaud",
        village_code=f"TN-01-01-{suffix}", village_name="Kottur", boundary=BOUNDARY,
        tribal_groups_json=["Recorded community"], socioeconomic_json={},
        provenance_json={"source": "integration fixture"}, reference_version="test-v1",
        synthetic=True,
    )
    holder = RightsHolder(display_name="Malar", holder_type="individual")
    claim = FRAClaim(
        claim_number=f"TN-FRA-{suffix}", right_type="IFR", status="granted",
        rights_holder=holder, village=village, submitter=actor,
        provenance_json={"synthetic": True},
    )
    session.add_all([actor, village, claim])
    session.flush()
    geometry = add_geometry_version(
        session, claim, geometry=BOUNDARY, source="reviewed_boundary",
        provenance={"source": "integration fixture"}, boundary_quality="surveyed",
        actor_id=actor.id,
    )
    session.add(FRATitle(
        claim=claim, version=1, title_number=f"TN-TITLE-{suffix}", active=True,
        geometry_version_id=geometry.id, issued_by=actor.id, metadata_json={},
    ))
    session.flush()
    return actor, village, claim, geometry


def test_pipeline_1_scanned_pdf_to_reviewed_native_fra_record():
    engine = _engine()
    storage = MemoryStorage()
    try:
        with Session(engine) as session:
            reviewer = User(external_id="pdf-pipeline-reviewer", role="reviewer")
            session.add(reviewer)
            session.flush()
            model = ModelVersion(
                task="entity_extraction", adapter_type="local_python", name="TN FRA extractor",
                version="tn-fra-regex-integration-v1", status="active",
                configuration_json={"ready": True, "runner": "tamil_nadu_fra_regex_v1"},
                label_map_json={}, metrics_json={"status": "evaluated"}, registered_by=reviewer.id,
            )
            session.add(model)
            session.flush()
            result = ingest_archive_batch(
                session, files=[ArchiveUpload("fra-scan.pdf", "application/pdf", _pdf_bytes())],
                source_office="Salem District FRA Cell", district="Salem",
                actor_id=reviewer.id, idempotency_key="pdf-pipeline", storage=storage,
                scanner=CleanScanner(), synthetic=False,
            )
            record_id = result["files"][0]["record_id"]
            job_id = result["files"][0]["processing_job_id"]
            record = session.get(FRAArchiveRecord, uuid.UUID(record_id))
            job = session.get(ProcessingJob, uuid.UUID(job_id))
            with patch("app.services.fra_job_handlers._read_archive_document", return_value=storage.read(record.document.storage_key)), patch(
                "app.services.fra_job_handlers._ocr_engine", return_value=DeterministicOCR()
            ):
                get_job_handler("archive_extract")(session, job)
            run = record.latest_extraction
            assert run.ocr_model_version
            assert run.standardized_json["claim_number"] == "TN/IFR/PIPE/1"
            page_backed = {
                item.field_name: item.source_page for item in run.field_reviews
                if item.field_name in {"claim_number", "holder_name", "village"}
            }
            assert page_backed == {"claim_number": 1, "holder_name": 1, "village": 1}
            review_archive_record(
                session, record, reviewed_fields=run.standardized_json,
                reviewer_id=reviewer.id, expected_revision=record.revision,
            )
            claim = promote_archive_record(
                session, record, actor_id=reviewer.id, expected_revision=record.revision,
            )
            assert claim.claim_number == "TN/IFR/PIPE/1"
            assert claim.right_type == "IFR"
            assert claim.status == "granted"
            assert record.promoted_claim_id == claim.id
    finally:
        engine.dispose()


def test_pipeline_2_legacy_registry_record_through_intake_to_native_case():
    engine = _engine()
    try:
        with Session(engine) as session:
            reviewer = User(external_id="legacy-pipeline-reviewer", role="reviewer")
            parcel = Parcel(
                state="Tamil Nadu", district="Salem", taluk="Yercaud", village="Kottur",
                survey_number="12", subdivision_number="A", geometry=BOUNDARY,
                source="legacy register", source_version="v1", source_record_id="parcel-12-A",
            )
            session.add_all([reviewer, parcel])
            session.flush()
            document = Document(
                uploaded_by=reviewer.id, storage_key="private/legacy.pdf", original_filename="legacy.pdf",
                content_type="application/pdf", sha256="a" * 64, idempotency_key="legacy-pipeline-document",
            )
            session.add(document)
            session.flush()
            legacy = Claim(
                claimant_id=reviewer.id, parcel_id=parcel.id, document_id=document.id,
                match_method="exact", idempotency_key="legacy-pipeline-claim",
            )
            holder = RightsHolder(display_name="Malar", holder_type="individual")
            session.add_all([legacy, holder])
            session.flush()
            intake = ensure_intake_for_legacy_claim(session, legacy, actor_id=reviewer.id)
            update_intake(
                session, intake, target_state="ready_for_promotion", expected_revision=0,
                reasons=["Verified as an FRA claim"], actor_id=reviewer.id,
                triage={"right_type": "IFR", "source": "legacy register"},
            )
            native = promote_intake(
                session, intake, right_type="IFR", rights_holder_id=holder.id,
                gram_sabha_id=None, expected_revision=1, actor_id=reviewer.id,
            )
            assert native.legacy_claim_id == legacy.id
            assert native.parcel_id == parcel.id
            assert intake.state == "promoted"
            assert intake.promoted_claim_id == native.id
    finally:
        engine.dispose()


def test_pipeline_3_reviewed_claim_geometry_is_visible_in_fra_atlas():
    engine = _engine()
    try:
        with Session(engine) as session:
            actor, _village, claim, geometry = _actor_village_claim(session, "003")
            collection = atlas_features(
                session, AtlasFilters(district="Salem", right_type="IFR", layers=("claim", "title")),
                privileged=True, actor_id=actor.id,
            )
            claim_feature = next(item for item in collection["features"] if item["properties"]["kind"] == "claim")
            title_feature = next(item for item in collection["features"] if item["properties"]["kind"] == "title")
            assert claim_feature["id"] == str(claim.id)
            assert claim_feature["geometry"] == geometry.geometry
            assert title_feature["properties"]["claim_id"] == str(claim.id)
    finally:
        engine.dispose()


def test_pipeline_4_satellite_artifact_through_model_boundary_to_village_profile():
    engine = _engine()
    try:
        with Session(engine) as session:
            actor, village, claim, geometry = _actor_village_claim(session, "004")
            scene = ImagerySceneRecord(
                provider="earth-search", collection="sentinel-2-l2a", scene_id="S2-PIPELINE-004",
                acquired_at=datetime(2026, 1, 15, tzinfo=timezone.utc), footprint=BOUNDARY,
                cloud_cover=4, asset_references_json={}, license_reference="public-test-reference",
                status="selected", provenance_json={"source": "satellite integration fixture"}, synthetic=True,
            )
            session.add(scene)
            session.flush()
            artifact = ImageryArtifact(
                claim_id=claim.id, geometry_version_id=geometry.id, imagery_scene_id=scene.id,
                artifact_type="analysis_ready_multiband", target_year=2026,
                storage_key="private/analysis-ready-004.tif", content_sha256="b" * 64,
                processor_version="satellite-ingestion-v1", parameters_json={}, statistics_json={},
                quality_flags_json=[], provenance_json={"legal_role": "supporting_observation"},
                state="completed", verification_state="unverified", synthetic=True,
            )
            model = ModelVersion(
                task="asset_detection", adapter_type="manifest", name="User model contract",
                version="user-asset-model-contract-v1", status="active",
                configuration_json={"attachment_boundary": True}, label_map_json={"pond": "water_body"},
                metrics_json={}, registered_by=actor.id,
            )
            session.add_all([artifact, model])
            session.flush()
            job = enqueue_asset_inference(
                session, village_id=None, claim_id=claim.id, model_version_id=model.id,
                scene_id=scene.scene_id, actor_id=actor.id, idempotency_key="asset-pipeline-004",
                manifest={
                    "synthetic": True, "acquired_at": "2026-01-15",
                    "features": [{"asset_class": "pond", "value": {"present": True},
                                  "geometry": BOUNDARY, "confidence": 0.91}],
                },
            )
            asset = process_asset_inference(session, job, adapter=ManifestAssetDetector(model.version))[0]
            review_asset(
                session, asset, outcome="verified", reviewer_id=actor.id,
                reasons=[], expected_revision=0,
            )
            profile = refresh_village_asset_profiles(session, actor_id=actor.id)[0]
            assert asset.asset_class == "water_body"
            assert asset.source_reference == artifact.imagery_scene.scene_id
            assert profile.village_id == village.id
            assert profile.metrics_json["water_body_count"] == 1
            assert profile.sources_json[0]["model_version"] == model.version
    finally:
        engine.dispose()


def _evaluate_planning_pipeline(session):
    actor, village, claim, _geometry = _actor_village_claim(session, "005")
    model = ModelVersion(
        task="asset_detection", adapter_type="manifest", name="User model contract",
        version="user-asset-model-contract-v2", status="active", configuration_json={},
        label_map_json={}, metrics_json={}, registered_by=actor.id,
    )
    session.add(model)
    session.flush()
    job = enqueue_asset_inference(
        session, village_id=None, claim_id=claim.id, model_version_id=model.id,
        scene_id="S2-PIPELINE-005", actor_id=actor.id, idempotency_key="asset-pipeline-005",
        manifest={"synthetic": True, "acquired_at": date.today().isoformat(), "features": [{
            "asset_class": "agricultural_land", "value": {"cover_fraction": 0.65},
            "geometry": BOUNDARY, "confidence": 0.93,
        }]},
    )
    asset = process_asset_inference(session, job, adapter=ManifestAssetDetector(model.version))[0]
    review_asset(session, asset, outcome="verified", reviewer_id=actor.id, reasons=[], expected_revision=0)
    catalog = SchemeCatalogEntry(
        scheme_code="PM-KISAN", display_name="PM-KISAN planning review", version="test-2026",
        department="Agriculture", description="Integration-test planning contract",
        effective_from=date(2026, 1, 1), effective_to=date(2026, 12, 31),
        approving_authority="Integration test authority", source_reference="https://example.gov.in/pm-kisan",
        definition_json={
            "target_scope": "holder", "intervention_types": ["agriculture_support"],
            "applicable_right_types": ["IFR"],
            "convergence_prerequisites": [{"fact": "agricultural_observation", "label": "Reviewed agricultural observation"}],
            "evidence_facts": ["agricultural_observation"],
        },
        authoritative=True, active=True, created_by=actor.id,
    )
    session.add(catalog)
    session.flush()
    rule = SchemeRuleSet(
        catalog_entry_id=catalog.id, scheme_code="PM-KISAN", display_name="PM-KISAN planning review",
        version="rule-2026", effective_from=date(2026, 1, 1), effective_to=date(2026, 12, 31),
        required_facts_json=["has_active_title", "agricultural_observation"],
        condition_json={"all": [
            {"eq": {"fact": "has_active_title", "value": True}},
            {"eq": {"fact": "agricultural_observation", "value": True}},
        ]},
        required_evidence_json=["agricultural_observation"], required_assets_json=["agricultural_land"],
        exclusion_condition_json=None, priority_conditions_json=[], freshness_requirements_json={},
        recommendation_logic_json={"recommended": "Send this FRA holder for PM-KISAN departmental review."},
        recommendation_text="Send this FRA holder for PM-KISAN departmental review.",
        source_reference="https://example.gov.in/pm-kisan/rule", active=True, created_by=actor.id,
    )
    session.add(rule)
    session.flush()
    facts = derive_facts(session, claim, CURRENT_FACT_VERSION, actor.id, "planning-pipeline-facts")
    recommendation = evaluate_rules(
        session, claim_id=claim.id, facts=fact_values(facts), actor_id=actor.id,
        idempotency_key="planning-pipeline-evaluation", rule_set_ids={rule.id},
        fact_snapshot_id=facts.id, fact_sources=facts.sources_json,
    )[0]
    return actor, village, claim, asset, facts, rule, catalog, recommendation


def test_pipeline_5_claim_and_asset_contracts_produce_governed_recommendation():
    engine = _engine()
    try:
        with Session(engine) as session:
            _actor, _village, claim, asset, facts, rule, catalog, recommendation = _evaluate_planning_pipeline(session)
            assert facts.facts_json["agricultural_land_fraction"]["value"] == 0.65
            assert facts.sources_json["agricultural_land_fraction"]["source_entity_id"] == str(asset.id)
            assert recommendation.outcome == "recommended"
            assert recommendation.input_json["fact_snapshot_id"] == str(facts.id)
            assert recommendation.output_json["required_assets"] == ["agricultural_land"]
            assert recommendation.rule_version == rule.version
            assert recommendation.output_json["catalog_entry_id"] == str(catalog.id)
            assert recommendation.claim_id == claim.id
    finally:
        engine.dispose()


def test_pipeline_6_recommendation_is_visible_in_planner_and_village_report():
    engine = _engine()
    try:
        with Session(engine) as session:
            actor, village, claim, _asset, _facts, rule, catalog, recommendation = _evaluate_planning_pipeline(session)
            planner = list_scheme_convergence(session, claim_id=claim.id)
            item = planner["items"][0]
            assert item["recommendation_id"] == str(recommendation.id)
            assert item["convergence_status"] == "potentially_eligible"
            assert item["rule_version"] == rule.version
            assert item["catalog_version"] == catalog.version
            assert item["official_eligibility_decision"] is False
            report = render_village_report(session, village.id, actor_id=actor.id)
            assert "PM-KISAN planning review" in report
            assert "Send this FRA holder for PM-KISAN departmental review." in report
            assert "advisory" in report.casefold()
    finally:
        engine.dispose()
