"""Run the limited, provenance-explicit Tamil Nadu FRA pilot.

The pilot combines a real public village boundary and a real sanitized Sentinel-2
scene reference with one conspicuously synthetic FRA case fixture.  The fixture
exercises archive extraction, review, native mapping, spatial linking, Atlas, and
DSS persistence without representing a real claimant or legal boundary.  Asset
detection remains an attachment point for the user's trained model.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
import hashlib
import json
from pathlib import Path

from shapely.geometry import MultiPolygon, mapping, shape
from sqlalchemy import func, select

from app.db.fra_completion_models import (
    AssetFeature,
    FRAArchiveRecord,
    FRAVillageProfile,
    VillageAssetProfile,
)
from app.db.fra_models import (
    DSSRecommendation,
    FRAClaim,
    FRAGeometryVersion,
    SchemeRuleSet,
)
from app.db.fra_operational_models import (
    DSSFactSnapshot,
    ImagerySceneRecord,
    SchemeCatalogEntry,
)
from app.db.models import Document, User
from app.db.session import get_session_factory
from app.services.dss_engine import (
    evaluate_rules,
    validate_rule_configuration,
    validate_rule_fact_contract,
)
from app.services.dss_facts import CURRENT_FACT_VERSION, derive_facts, fact_values
from app.services.fra_archive import (
    create_archive_record,
    create_import_batch,
    process_archive_extraction,
    promote_archive_record,
    review_archive_record,
)
from app.services.fra_atlas import AtlasFilters, atlas_features, import_village_profiles
from app.services.fra_claims import add_geometry_version
from app.services.fra_reports import render_village_report
from app.services.model_gateway import ManifestFRAEntityExtractor
from app.services.scheme_catalog import create_catalog_entry
from app.services.village_asset_profiles import refresh_village_asset_profiles


ROOT = Path(__file__).resolve().parents[1]
VILLAGE_PATH = ROOT / "data" / "real" / "arpisampalaiyam_village.geojson"
SCENE_PATH = ROOT / "data" / "real" / "arpisampalaiyam_sentinel2_scene.json"

PILOT_REFERENCE = "TN-PILOT-CFR-SYNTHETIC-001"
PILOT_SCHEME_CODE = "TN-PILOT-JJM-READINESS"
PILOT_CATALOG_VERSION = "2026-09-pilot-1"
PILOT_RULE_VERSION = "2026-09-pilot-1"


@dataclass(frozen=True)
class PilotStage:
    step: int
    key: str
    title: str
    status: str
    evidence: str
    limitation: str | None = None


@dataclass(frozen=True)
class PilotReport:
    created: int
    district: str
    block: str
    village: str
    village_code: str
    village_id: str
    claim_id: str
    scene_id: str
    recommendation_id: str | None
    report_url: str
    report_sha256: str
    stages: tuple[PilotStage, ...]


COUNTED_MODELS = (
    Document,
    FRAArchiveRecord,
    FRAVillageProfile,
    FRAClaim,
    FRAGeometryVersion,
    ImagerySceneRecord,
    AssetFeature,
    VillageAssetProfile,
    DSSFactSnapshot,
    SchemeCatalogEntry,
    SchemeRuleSet,
    DSSRecommendation,
)


def _load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _count(session) -> int:
    return sum(
        session.scalar(select(func.count()).select_from(model)) or 0
        for model in COUNTED_MODELS
    )


def _synthetic_record() -> dict:
    return {
        "holder_name": "Synthetic Arpisampalaiyam Forest Rights Collective",
        "district": "Villupuram",
        "block": "Kandamangalam",
        "village": "Arpisampalaiyam",
        "right_type": "CFR",
        "claim_status": "submitted",
        "claim_number": PILOT_REFERENCE,
        "claim_year": 2024,
        "claimed_area": "4.00 hectare",
        "confidence": 1.0,
    }


def _document(session, actor_id) -> Document:
    key = "tn-fra-pilot:synthetic-document:v1"
    existing = session.scalar(
        select(Document).where(
            Document.uploaded_by == actor_id,
            Document.idempotency_key == key,
        )
    )
    if existing is not None:
        return existing
    raw_text = (
        "SYNTHETIC FRA PILOT FIXTURE. Community Forest Resource claim for "
        "Arpisampalaiyam, Kandamangalam, Villupuram. This is not a legal record."
    )
    digest = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
    document = Document(
        uploaded_by=actor_id,
        storage_key=f"private/synthetic-fra-pilot/{digest}.txt",
        original_filename="TN-PILOT-CFR-SYNTHETIC-001.synthetic.txt",
        content_type="text/plain",
        sha256=digest,
        ocr_status="completed",
        idempotency_key=key,
    )
    session.add(document)
    session.flush()
    return document


def _archive_record(session, actor_id) -> FRAArchiveRecord:
    provenance = {
        "source": "AranyaSetu synthetic FRA pilot fixture",
        "version": "tn-pilot-fixture-v1",
        "synthetic": True,
        "not_a_legal_record": True,
        "purpose": "exercise_the_FRA_pipeline_without_inventing_a_real_claimant",
    }
    batch = create_import_batch(
        session,
        source_label="Arpisampalaiyam synthetic FRA pilot fixture",
        state="Tamil Nadu",
        actor_id=actor_id,
        idempotency_key="tn-fra-pilot:archive:v1",
        synthetic=True,
        provenance=provenance,
    )
    record = create_archive_record(
        session,
        batch=batch,
        document_id=_document(session, actor_id).id,
        legacy_reference=PILOT_REFERENCE,
        actor_id=actor_id,
        provenance=provenance,
    )
    fields = _synthetic_record()
    if not record.extraction_runs:
        process_archive_extraction(
            session,
            record,
            extractor=ManifestFRAEntityExtractor("tn-pilot-synthetic-ner-v1"),
            manifest=fields,
            raw_text=(
                "SYNTHETIC FRA PILOT FIXTURE. Community Forest Resource claim; "
                "Arpisampalaiyam, Kandamangalam, Villupuram; submitted."
            ),
            ocr_model_version="synthetic-transcription-fixture-v1",
            actor_id=actor_id,
        )
    if record.review_state == "needs_review":
        review_archive_record(
            session,
            record,
            reviewed_fields=record.latest_extraction.standardized_json,
            reviewer_id=actor_id,
            expected_revision=record.revision,
        )
    if record.review_state == "reviewed":
        promote_archive_record(
            session,
            record,
            actor_id=actor_id,
            expected_revision=record.revision,
        )
    return record


def _claim_geometry(session, claim: FRAClaim, village: FRAVillageProfile, actor_id):
    if claim.geometry_versions:
        return max(claim.geometry_versions, key=lambda item: item.version)
    village_shape = shape(village.boundary)
    inset = village_shape.representative_point().buffer(0.0005).intersection(village_shape)
    if inset.geom_type == "Polygon":
        inset = MultiPolygon([inset])
    elif inset.geom_type != "MultiPolygon":
        polygons = [part for part in inset.geoms if part.geom_type == "Polygon"]
        inset = MultiPolygon(polygons)
    return add_geometry_version(
        session,
        claim,
        geometry=mapping(inset),
        source="synthetic_pilot_geometry",
        provenance={
            "synthetic": True,
            "not_a_legal_boundary": True,
            "derived_within_real_village_code": village.village_code,
            "reason": "No verified publishable claim-level FRA geometry was provided.",
        },
        boundary_quality="synthetic_pilot_only",
        actor_id=actor_id,
        request_id="tn-fra-pilot",
    )


def _scene_reference(session) -> ImagerySceneRecord:
    payload = _load(SCENE_PATH)
    item = payload["selected_scene"]
    existing = session.scalar(
        select(ImagerySceneRecord).where(
            ImagerySceneRecord.provider == item["provider"],
            ImagerySceneRecord.collection == item["collection"],
            ImagerySceneRecord.scene_id == item["scene_id"],
        )
    )
    if existing is not None:
        return existing
    scene = ImagerySceneRecord(
        provider=item["provider"],
        collection=item["collection"],
        scene_id=item["scene_id"],
        acquired_at=datetime.fromisoformat(item["acquired_at"]),
        footprint=item["footprint"],
        cloud_cover=item["cloud_cover"],
        asset_references_json={},
        license_reference=item["license_reference"],
        status="discovered",
        provenance_json={
            "source": "live_stac_validation",
            "schema_version": payload["schema_version"],
            "search": payload["search"],
            "asset_keys": item["asset_keys"],
            "private_asset_urls_persisted": False,
            "legal_role": "supporting_observation_context",
        },
        synthetic=False,
    )
    session.add(scene)
    session.flush()
    return scene


def _catalog(session, actor_id) -> SchemeCatalogEntry:
    existing = session.scalar(
        select(SchemeCatalogEntry).where(
            SchemeCatalogEntry.scheme_code == PILOT_SCHEME_CODE,
            SchemeCatalogEntry.version == PILOT_CATALOG_VERSION,
        )
    )
    if existing is not None:
        return existing
    return create_catalog_entry(
        session,
        {
            "scheme_code": PILOT_SCHEME_CODE,
            "display_name": "Jal Jeevan Mission pilot evidence-readiness screening",
            "version": PILOT_CATALOG_VERSION,
            "department": "Pilot scheme-convergence review desk",
            "description": (
                "Administrator-approved pilot metadata for testing evidence readiness; "
                "it is not an official eligibility or sanction rule."
            ),
            "effective_from": date(2026, 1, 1),
            "approving_authority": "AranyaSetu pilot administrator",
            "source_reference": "https://jaljeevanmission.gov.in/",
            "definition": {
                "reviewed_on": "2026-09-06",
                "pilot_only": True,
                "target_scope": "holder_and_village",
                "applicable_right_types": ["IFR", "CR", "CFR"],
                "intervention_types": ["drinking_water", "water_source_strengthening"],
                "convergence_prerequisites": [
                    {
                        "fact": "water_source_present",
                        "label": "Current verified water-source observation",
                    }
                ],
                "evidence_facts": ["water_source_present"],
                "screening_note": (
                    "This pilot checks whether evidence is available; it does not encode "
                    "official scheme eligibility."
                ),
            },
            "authoritative": True,
            "active": True,
        },
        actor_id=actor_id,
        request_id="tn-fra-pilot",
    )


def _rule(session, actor_id, catalog: SchemeCatalogEntry) -> SchemeRuleSet:
    existing = session.scalar(
        select(SchemeRuleSet).where(
            SchemeRuleSet.scheme_code == PILOT_SCHEME_CODE,
            SchemeRuleSet.version == PILOT_RULE_VERSION,
        )
    )
    if existing is not None:
        return existing
    required_facts = ["claim_status", "water_source_present"]
    condition = {"eq": {"fact": "water_source_present", "value": False}}
    required_evidence = ["water_source_present"]
    recommendation_logic = {
        "recommended": "Refer the water evidence for human scheme-convergence review.",
        "not_recommended": "Current reviewed evidence does not indicate this pilot referral.",
        "insufficient_data": (
            "Attach and run the trained asset model, review its water observations, "
            "then repeat scheme-convergence screening."
        ),
    }
    validate_rule_fact_contract(required_facts, condition)
    validate_rule_configuration(
        required_facts=required_facts,
        required_evidence=required_evidence,
        required_assets=[],
        exclusion_condition={"eq": {"fact": "claim_status", "value": "rejected"}},
        priority_conditions=[],
        freshness_requirements={},
        recommendation_logic=recommendation_logic,
    )
    rule = SchemeRuleSet(
        catalog_entry_id=catalog.id,
        scheme_code=PILOT_SCHEME_CODE,
        display_name="JJM pilot evidence-readiness rule",
        version=PILOT_RULE_VERSION,
        effective_from=date(2026, 1, 1),
        required_facts_json=required_facts,
        condition_json=condition,
        required_evidence_json=required_evidence,
        required_assets_json=[],
        exclusion_condition_json={"eq": {"fact": "claim_status", "value": "rejected"}},
        priority_conditions_json=[],
        freshness_requirements_json={},
        recommendation_logic_json=recommendation_logic,
        recommendation_text=recommendation_logic["recommended"],
        source_reference="pilot-screening://JJM/evidence-readiness/2026-09-1",
        active=True,
        created_by=actor_id,
    )
    session.add(rule)
    session.flush()
    return rule


def seed_pilot(session, *, actor_id) -> PilotReport:
    actor = session.get(User, actor_id)
    if actor is None or actor.role != "admin":
        raise PermissionError("The Tamil Nadu FRA pilot requires an administrator.")
    before = _count(session)

    import_village_profiles(session, _load(VILLAGE_PATH), actor_id=actor_id)
    village = session.scalar(
        select(FRAVillageProfile).where(FRAVillageProfile.village_code == "632998")
    )
    record = _archive_record(session, actor_id)
    claim = record.promoted_claim
    geometry = _claim_geometry(session, claim, village, actor_id)
    scene = _scene_reference(session)
    profile = next(
        item
        for item in refresh_village_asset_profiles(
            session, actor_id=actor_id, request_id="tn-fra-pilot"
        )
        if item.village_id == village.id
    )
    catalog = _catalog(session, actor_id)
    rule = _rule(session, actor_id, catalog)
    snapshot = derive_facts(
        session,
        claim,
        CURRENT_FACT_VERSION,
        actor_id,
        f"tn-pilot-{CURRENT_FACT_VERSION}-{claim.id}",
        request_id="tn-fra-pilot",
    )
    recommendations = evaluate_rules(
        session,
        claim_id=claim.id,
        facts=fact_values(snapshot),
        actor_id=actor_id,
        idempotency_key=f"tn-pilot-evaluation-{claim.id}",
        rule_set_ids={rule.id},
        fact_snapshot_id=snapshot.id,
        fact_sources=snapshot.sources_json,
        request_id="tn-fra-pilot",
    )
    recommendation = recommendations[0] if recommendations else None
    report_html = render_village_report(session, village.id, actor_id=actor_id)
    report_url = f"/api/fra/reports/villages/{village.id}"
    report_sha256 = hashlib.sha256(report_html.encode("utf-8")).hexdigest()

    atlas = atlas_features(
        session,
        AtlasFilters(
            state="Tamil Nadu",
            district="Villupuram",
            block="Kandamangalam",
            village="Arpisampalaiyam",
        ),
        privileged=True,
        actor_id=actor_id,
    )
    atlas_kinds = {item["properties"].get("kind") for item in atlas["features"]}
    has_model_assets = profile.verified_asset_count > 0
    mapping_status = "complete" if has_model_assets else "awaiting_user_model"
    profile_status = "complete" if has_model_assets else "complete_with_observation_gap"
    recommendation_status = recommendation.outcome if recommendation else "not_evaluated"

    fixture_limitation = "No publishable claim-level FRA document was provided; this pilot record is synthetic."
    model_limitation = "Attach the user's trained asset-detection model and run reviewed inference."
    stages = (
        PilotStage(1, "upload_import_legacy_fra_documents", "Upload/import legacy FRA documents", "complete_with_synthetic_fixture", f"Archive record {record.legacy_reference} retains its private source document.", fixture_limitation),
        PilotStage(2, "ocr_documents", "OCR documents", "complete_with_synthetic_fixture", f"The pilot document has OCR status {record.document.ocr_status}.", "Workflow fixture transcription; not a real FRA OCR accuracy result."),
        PilotStage(3, "extract_fra_entities", "Extract FRA entities", "complete_with_synthetic_fixture", f"Entity extraction {record.latest_extraction.entity_model_version} produced reviewable FRA fields.", "Deterministic fixture output; not a trained-model accuracy claim."),
        PilotStage(4, "review_extracted_information", "Review extracted information", "complete", f"{len(record.latest_extraction.field_reviews)} fields retain reviewer decisions and final values."),
        PilotStage(5, "create_normalize_fra_records", "Create/normalize FRA records", "complete_with_synthetic_fixture", f"The reviewed archive record was promoted to native {claim.right_type} case {claim.claim_number}.", "The claimant identity and case are synthetic."),
        PilotStage(6, "link_records_to_villages", "Link records to villages", "complete_with_synthetic_fixture", f"Case {claim.claim_number} links to real village {village.village_code} and geometry version {geometry.version}.", "The inset case polygon is synthetic and is not a legal boundary."),
        PilotStage(7, "display_on_fra_atlas", "Display records on the FRA Atlas", "complete" if {"village", "claim"} <= atlas_kinds else "incomplete", "The filtered Atlas returns the pilot village and spatial FRA case."),
        PilotStage(8, "display_claim_title_status", "Display claim/title status", "complete", f"Case state is {claim.status}; active title count is {sum(1 for title in claim.titles if title.active)}."),
        PilotStage(9, "load_satellite_imagery", "Load satellite imagery", "complete", f"Real Sentinel-2 scene {scene.scene_id} intersects the pilot village.", "The seed persists sanitized discovery metadata; real raster ingestion is a separate queued operation."),
        PilotStage(10, "run_ai_asset_detection", "Run AI asset detection", mapping_status, f"{profile.verified_asset_count} reviewed model observations are available." if has_model_assets else "The production model attachment interface is ready; no detections were fabricated.", None if has_model_assets else model_limitation),
        PilotStage(11, "display_mapped_assets", "Display agricultural/water/homestead assets", "complete" if has_model_assets else "awaiting_user_model", f"The Atlas can display {profile.verified_asset_count} reviewed canonical asset observations." if has_model_assets else "The asset view truthfully shows no reviewed detections.", None if has_model_assets else model_limitation),
        PilotStage(12, "generate_village_asset_profile", "Generate village asset profile", profile_status, f"Village profile {profile.id} records {profile.verified_asset_count} reviewed assets and explicit observation gaps."),
        PilotStage(13, "generate_dss_facts", "Generate DSS facts", "complete", f"Fact snapshot {snapshot.id} uses contract {snapshot.derivation_version}."),
        PilotStage(14, "run_scheme_rules", "Run scheme rules", "complete" if recommendation else "not_evaluated", f"Governed rule {rule.scheme_code} version {rule.version} evaluated the persisted fact snapshot."),
        PilotStage(15, "show_recommended_interventions", "Show recommended schemes/interventions", recommendation_status, recommendation.output_json["recommendation"] if recommendation else "No executable pilot rule was available.", "A human department must review every output; this does not determine eligibility or sanction."),
        PilotStage(16, "show_spatial_evidence_reasoning", "Show spatial/evidence reasoning", "complete" if recommendation else "not_evaluated", f"Recommendation {recommendation.id} retains the fact snapshot, rule version, reasons, and missing inputs." if recommendation else "No recommendation evidence was generated."),
        PilotStage(17, "generate_report", "Generate report", "complete", f"Village planning report rendered at {report_url} with SHA-256 {report_sha256}."),
    )
    session.flush()
    return PilotReport(
        created=_count(session) - before,
        district=village.district_name,
        block=village.block_name,
        village=village.village_name,
        village_code=village.village_code,
        village_id=str(village.id),
        claim_id=str(claim.id),
        scene_id=scene.scene_id,
        recommendation_id=str(recommendation.id) if recommendation else None,
        report_url=report_url,
        report_sha256=report_sha256,
        stages=stages,
    )


def _admin(session) -> User:
    user = session.scalar(select(User).where(User.external_id == "tn-fra-pilot-admin"))
    if user is None:
        user = User(
            external_id="tn-fra-pilot-admin",
            display_name="Tamil Nadu FRA Pilot Administrator",
            role="admin",
        )
        session.add(user)
        session.flush()
    elif user.role != "admin":
        raise PermissionError("tn-fra-pilot-admin exists without the administrator role.")
    return user


def main() -> None:
    with get_session_factory()() as session:
        report = seed_pilot(session, actor_id=_admin(session).id)
        session.commit()
        print(json.dumps(asdict(report), indent=2))


if __name__ == "__main__":
    main()
