"""Exercise the actual producers and consumers, including retained old evidence."""

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import User
from app.db.fra_models import FRAClaim, FRAGeometryVersion, FRATitle, RightsHolder, SchemeRuleSet
from app.db.fra_completion_models import AssetFeature, ModelVersion
from app.db.fra_operational_models import DSSFactSnapshot, ImageryArtifact
from app.services.dss_engine import InvalidRuleError, evaluate_condition, evaluate_rules, validate_rule_definition
from app.services.dss_facts import derive_facts, fact_values
from app.services.fra_assets import enqueue_asset_inference, process_asset_inference, review_asset
from app.services.fra_geospatial_import import stage_spatial_import, publish_spatial_import
from app.services.model_gateway import ManifestAssetDetector


GEOMETRY = {"type": "MultiPolygon", "coordinates": [[[[79, 10], [79.1, 10], [79.1, 10.1], [79, 10.1], [79, 10]]]]}


@pytest.fixture
def records():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        actor = User(external_id="contracts-reviewer", role="reviewer")
        claim = FRAClaim(claim_number="TN-CONTRACT-1", right_type="IFR", status="granted",
                         rights_holder=RightsHolder(display_name="Holder", holder_type="individual"), submitter=actor)
        session.add_all([actor, claim]); session.flush()
        geometry = FRAGeometryVersion(claim=claim, version=1, geometry=GEOMETRY, source="survey", created_by=actor.id)
        model = ModelVersion(task="asset_detection", name="contracts", version="1", adapter_type="manifest",
                             status="active", label_map_json={}, metrics_json={}, configuration_json={}, registered_by=actor.id)
        session.add_all([geometry, model]); session.flush()
        session.add(FRATitle(claim=claim, version=1, title_number="TN-CONTRACT-TITLE", active=True,
                            geometry_version_id=geometry.id, issued_by=actor.id, metadata_json={}))
        session.commit()
        yield session, actor, claim, model
    engine.dispose()


def infer(records, *, value=.6, asset_class="agricultural_land", key="inference"):
    session, actor, claim, model = records
    job = enqueue_asset_inference(session, village_id=None, claim_id=claim.id, model_version_id=model.id,
        scene_id=key, actor_id=actor.id, idempotency_key=key, manifest={"synthetic": True,
        "acquired_at": date.today().isoformat(), "features": [{"asset_class": asset_class,
        "value": value, "geometry": GEOMETRY, "confidence": .9}]})
    return process_asset_inference(session, job, adapter=ManifestAssetDetector("1"))[0]


def snapshot(records, key):
    session, actor, claim, _model = records
    return derive_facts(session, claim, "fra-dss-facts-v1", actor.id, key)


def test_reviewed_model_measurement_flows_into_versioned_sample_rules(records):
    session, actor, claim, _model = records
    asset = infer(records)
    assert snapshot(records, "unreviewed").facts_json["agricultural_land_fraction"]["value"] == "unknown"
    review_asset(session, asset, outcome="verified", reviewer_id=actor.id, reasons=[], expected_revision=0)
    water = infer(records, value=False, asset_class="open_well", key="water")
    review_asset(session, water, outcome="verified", reviewer_id=actor.id, reasons=[], expected_revision=0)
    facts = snapshot(records, "reviewed")
    assert facts.facts_json["agricultural_land_fraction"]["value"] == .6
    assert facts.facts_json["agricultural_observation"]["value"] is True
    assert facts.facts_json["water_source_present"]["value"] is False
    assert facts.sources_json["agricultural_land_fraction"]["source_entity_id"] == str(asset.id)
    for definition in json.loads((Path(__file__).parents[1] / "data/demo_dss_rules.json").read_text()):
        session.add(SchemeRuleSet(scheme_code=definition["scheme_code"], display_name=definition["display_name"],
            version=definition["version"], required_facts_json=definition["required_facts"],
            condition_json=definition["condition"], required_evidence_json=definition["required_evidence"],
            required_assets_json=definition["required_assets"],
            exclusion_condition_json=definition["exclusion_condition"],
            priority_conditions_json=definition["priority_conditions"],
            freshness_requirements_json=definition["freshness_requirements"],
            recommendation_logic_json=definition["recommendation_logic"],
            recommendation_text=definition["recommendation_text"],
            source_reference=definition["source_reference"], created_by=actor.id))
    session.flush()
    results = evaluate_rules(session, claim_id=claim.id, facts=fact_values(facts), actor_id=actor.id,
        idempotency_key="derived", fact_snapshot_id=facts.id, fact_sources=facts.sources_json)
    assert {r.rule_set.scheme_code: r.outcome for r in results} == {
        "JJM": "insufficient_data", "PM-KISAN": "recommended",
        "PMAY-G": "insufficient_data", "MGNREGA": "insufficient_data",
        "DAJGUA": "insufficient_data",
    }
    assert {r.rule_version for r in results} == {"tn-sample-4"}
    corrected = review_asset(session, asset, outcome="corrected", reviewer_id=actor.id,
        reasons=["Presence only confirmed"], corrected_value={"present": True}, expected_revision=1)
    revised = snapshot(records, "corrected")
    assert revised.facts_json["agricultural_land_fraction"]["value"] == "unknown"
    assert revised.facts_json["agricultural_observation"]["value"] is True
    assert revised.sources_json["agricultural_land_fraction"]["source_entity_id"] == str(corrected.id)
    assert asset.observed_value_json == {"value": .6}
    assert asset.inference_run.output_json["features"][0]["value"] == {"value": .6}
    assert facts.facts_json["agricultural_land_fraction"]["value"] == .6


@pytest.mark.parametrize("value", [{"cover_fraction": 0}, {"coverage_fraction": 1}, {"value": .4}])
def test_fraction_aliases_and_historical_classes(records, value):
    session, actor, claim, _model = records
    session.add(AssetFeature(claim_id=claim.id, asset_class="agricultural_land", observed_value_json={**value, "asset_subtype": "cropland"},
        acquired_at=date.today(), source_type="field", verification_state="verified", provenance_json={}))
    facts = snapshot(records, "aliases").facts_json
    assert facts["agricultural_land_fraction"]["value"] == next(iter(value.values()))


@pytest.mark.parametrize("value", [True, -0.1, 1.1, float("nan"), float("inf"), "0.4"])
def test_invalid_explicit_fraction_is_rejected_at_model_and_review_boundaries(records, value):
    with pytest.raises(ValueError, match="fraction|finite"):
        infer(records, value={"cover_fraction": value}, key="invalid")
    session, actor, _claim, _model = records
    session.rollback()
    asset = infer(records, value={"present": True}, key="valid")
    with pytest.raises(ValueError, match="fraction|finite"):
        review_asset(session, asset, outcome="corrected", reviewer_id=actor.id, expected_revision=0,
                     corrected_value={"coverage_fraction": value}, reasons=["Invalid measurement"])
    assert asset.revision == 0


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_manifest_is_rejected_before_persisting_a_job(records, value):
    from sqlalchemy import func
    from app.db.fra_completion_models import ProcessingJob, InferenceRun
    session, actor, _claim, _model = records
    with pytest.raises(ValueError, match="finite"):
        infer(records, value={"cover_fraction": value}, key="nonfinite")
    # Validation must leave the transaction usable, without a queued invalid job.
    assert session.scalar(select(func.count()).select_from(ProcessingJob)) == 0
    assert session.scalar(select(func.count()).select_from(InferenceRun)) == 0
    assert session.get(User, actor.id) is actor


@pytest.mark.parametrize("actual", [True, False, float("nan"), float("inf"), -float("inf"), "0.6", {}])
@pytest.mark.parametrize("operator", ["gte", "lte"])
def test_numeric_comparisons_require_finite_numbers(actual, operator):
    result = evaluate_condition({operator: {"fact": "cover", "value": .25}}, {"cover": actual})
    assert result.value is None
    assert result.missing_inputs == {"cover"}
    assert "incompatible" in " ".join(result.reasons).lower()


@pytest.mark.parametrize("threshold", [float("nan"), float("inf"), -float("inf")])
def test_rule_threshold_must_be_finite(threshold):
    with pytest.raises(InvalidRuleError):
        validate_rule_definition({"gte": {"fact": "cover", "value": threshold}})


def test_old_fact_replay_is_immutable_but_new_v1_derivations_are_rejected(records):
    session, actor, claim, _model = records
    old = DSSFactSnapshot(claim_id=claim.id, derivation_version="tn-facts-v1", idempotency_key="old",
        facts_json={"sentinel": {"value": "retained"}}, sources_json={"original": "source"}, created_by=actor.id)
    session.add(old); session.flush()
    assert derive_facts(session, claim, "tn-facts-v1", actor.id, "old") is old
    with pytest.raises(ValueError, match="fra-dss-facts-v1"):
        derive_facts(session, claim, "tn-facts-v1", actor.id, "new")
    snapshot(records, "v2")
    assert old.facts_json == {"sentinel": {"value": "retained"}}


def test_old_geometry_imagery_cannot_supply_current_water_absence(records):
    session, actor, claim, _model = records
    old_geometry = claim.geometry_versions[0]
    session.add(ImageryArtifact(claim_id=claim.id, geometry_version_id=old_geometry.id,
        artifact_type="current_land_observation", target_year=date.today().year, storage_key="private/water.json",
        content_sha256="a" * 64, processor_version="1", statistics_json={"observation_coverage": .95,
        "water_source_present": False}, state="completed", verification_state="verified"))
    session.flush()
    assert snapshot(records, "old-boundary").facts_json["water_source_present"]["value"] is False
    session.add(FRAGeometryVersion(claim=claim, version=2, geometry=GEOMETRY, source="new-survey", created_by=actor.id))
    session.flush()
    assert snapshot(records, "new-boundary").facts_json["water_source_present"]["value"] == "unknown"


@pytest.mark.parametrize("kind", ["water_stress", "groundwater", "groundwater_stress"])
def test_water_reference_import_publication_and_derivation(records, kind):
    session, actor, _claim, _model = records
    batch = stage_spatial_import(session, content=json.dumps({"type": "FeatureCollection", "features": [{
        "type": "Feature", "id": "water-1", "geometry": GEOMETRY, "properties": {"stress_score": .8}}]}).encode(),
        filename="water.geojson", dataset_kind=kind, source_authority="Reference authority", source_version="2026-09",
        declared_crs="EPSG:4326", actor_id=actor.id, idempotency_key="water-import", synthetic=True)
    fact_name = "water_stress_status" if kind == "water_stress" else "groundwater_status"
    assert snapshot(records, "unpublished").facts_json[fact_name]["value"] == "unknown"
    publish_spatial_import(session, batch, reviewer_id=actor.id)
    facts = snapshot(records, "published")
    assert facts.facts_json[fact_name]["value"] == "critical"
    assert facts.sources_json[fact_name]["source_version"] == "2026-09"


def test_claim_location_fallback_matches_cases_atlas_assets_and_dashboards(records):
    from app.db.models import Parcel
    from app.db.fra_models import GramSabha
    from app.services.fra_cases import list_cases
    from app.services.fra_atlas import atlas_features, AtlasFilters
    from app.services.fra_assets import list_assets
    from app.services.fra_dashboards import planner_dashboard, verifier_dashboard
    from app.services.dss_referrals import list_recommendations
    session, actor, original, _model = records
    sabha = GramSabha(name="Sabha", district="Salem", block="Yercaud", village="Test", state="TN")
    session.add(sabha); session.flush()
    original.gram_sabha = sabha
    claims = [original]
    for index in range(3):
        holder = RightsHolder(display_name=f"Holder {index}", holder_type="individual",
                              gram_sabha=sabha if index == 0 else None)
        parcel = Parcel(state="Tamil Nadu", district="Salem" if index < 2 else "Other", taluk="Yercaud",
                        village="Test", survey_number=str(index), geometry=GEOMETRY, source="survey")
        claim = FRAClaim(claim_number=f"LOC-{index}", right_type="IFR", status="submitted",
                         rights_holder=holder, parcel=parcel, submitter=actor)
        session.add(claim); session.flush()
        session.add(FRAGeometryVersion(claim=claim, version=1, geometry=GEOMETRY, source="survey", created_by=actor.id))
        claims.append(claim)
    rule = SchemeRuleSet(scheme_code="TEST", display_name="Test", version="1", required_facts_json=[],
        condition_json={"present": {"fact": "title"}}, recommendation_text="Test", source_reference="test", created_by=actor.id)
    session.add(rule); session.flush()
    for claim in claims:
        session.add(AssetFeature(claim_id=claim.id, asset_class="infrastructure", observed_value_json={"present": True, "asset_subtype": "well"},
            polygon_geometry=GEOMETRY, source_type="field", verification_state="verified", provenance_json={}))
        evaluate_rules(session, claim_id=claim.id, facts={"title": True}, actor_id=actor.id, idempotency_key=str(claim.id))
    session.flush()
    scope = {"district": " salem ", "block": " YERCAUD ", "village": " test "}
    expected = {claim.id for claim in claims[:3]}
    assert {c.id for c in list_cases(session, user_id=actor.id, privileged=True, **scope)} == expected
    features = atlas_features(session, AtlasFilters(**scope), privileged=True)["features"]
    assert {f["id"] for f in features if f["properties"]["kind"] == "claim"} == {str(i) for i in expected}
    assert {a.claim_id for a in list_assets(session, **scope)} == expected
    assert {r.claim_id for r in list_recommendations(session, **scope)} == expected
    assert planner_dashboard(session, **scope)["claims_by_status"] == {"granted": 1, "submitted": 2}
    assert verifier_dashboard(session, **scope)["totals"]["spatial_findings_awaiting_disposition"] == 3


def test_current_summary_ignores_history_and_deduplicates_missing_inputs(records):
    from app.db.fra_models import DSSRecommendation
    from app.services.fra_dashboards import planner_dashboard
    from app.services.dss_referrals import list_recommendations
    session, actor, claim, _model = records
    now = datetime.now(timezone.utc)
    for version in (1, 2):
        rule = SchemeRuleSet(scheme_code="TEST", display_name="Test", version=str(version), required_facts_json=[],
            condition_json={"present": {"fact": "water"}}, recommendation_text="Test", source_reference="test", created_by=actor.id)
        session.add(rule); session.flush()
        session.add(DSSRecommendation(claim=claim, rule_set=rule, rule_version=rule.version, actor_id=actor.id,
            idempotency_key=f"rec-{version}", outcome="recommended" if version == 1 else "insufficient_data",
            input_json={}, output_json={"missing_inputs": ["water"]}, created_at=now + timedelta(seconds=version)))
        session.add(DSSFactSnapshot(claim_id=claim.id, derivation_version=f"tn-facts-v{version}", idempotency_key=f"facts-{version}",
            facts_json={"water_source_present": {"value": False if version == 1 else "unknown"},
                "source_quality_flags": {"value": {"unknown_facts": ["water"]}}}, sources_json={}, created_by=actor.id,
            created_at=now + timedelta(seconds=version)))
    session.flush()
    summary = planner_dashboard(session)
    assert summary["recommendations"] == [{"scheme_code": "TEST", "outcome": "insufficient_data", "count": 1}]
    assert summary["missing_inputs"] == [{"fact": "water", "count": 1}]
    assert summary["deficit_counts"] == {}
    assert len(list_recommendations(session)) == 1
    assert list_recommendations(session, outcome="recommended") == []
    assert len(list_recommendations(session, include_history=True)) == 2
    latest_evaluation = evaluate_rules(session, claim_id=claim.id, facts={"water": True}, actor_id=actor.id,
                                      idempotency_key="current-rule")
    assert [item.rule_version for item in latest_evaluation] == ["2"]
    old_rule = session.scalar(select(SchemeRuleSet).where(SchemeRuleSet.version == "1"))
    historical_evaluation = evaluate_rules(session, claim_id=claim.id, facts={"water": True}, actor_id=actor.id,
        idempotency_key="explicit-rule", rule_set_ids=[old_rule.id])
    assert [item.rule_version for item in historical_evaluation] == ["1"]


def test_unversioned_and_old_boundary_dispositions_do_not_clear_current_queue(records):
    from app.db.fra_models import FRAEvidenceItem
    from app.services.fra_dashboards import verifier_dashboard
    session, actor, claim, _model = records
    evidence = FRAEvidenceItem(claim=claim, category="map", legal_role="submitted",
        source="spatial_evaluation_disposition", description="Old review", provenance_json={},
        source_verified=False, created_by=actor.id)
    session.add(evidence); session.flush()
    count = lambda: verifier_dashboard(session)["totals"]["spatial_findings_awaiting_disposition"]
    assert count() == 1
    evidence.source_verified = True
    assert count() == 1
    evidence.provenance_json = {"geometry_version_id": str(claim.geometry_versions[0].id)}
    assert count() == 0
    session.add(FRAGeometryVersion(claim=claim, version=2, geometry=GEOMETRY, source="survey", created_by=actor.id))
    session.flush()
    assert count() == 1


def test_queues_count_full_populations_and_village_only_assets_jobs(records):
    from app.db.fra_completion_models import FRAVillageProfile, FRAArchiveRecord, FRAImportBatch, ProcessingJob
    from app.db.models import Document
    from app.services.fra_dashboards import verifier_dashboard, planner_dashboard
    session, actor, _claim, _model = records
    village = FRAVillageProfile(state_code="TN", state_name="Tamil Nadu", district_code="D", district_name="Village District",
        block_code="B", block_name="Block", village_code="V", village_name="Village", boundary=GEOMETRY,
        reference_version="1", socioeconomic_json={}, tribal_groups_json=[], provenance_json={}, synthetic=True)
    batch = FRAImportBatch(state_code="TN", source_label="Test",
        idempotency_key="queue-batch", created_by=actor.id)
    document = Document(uploaded_by=actor.id, storage_key="queue-doc", original_filename="sample.pdf",
        content_type="application/pdf", sha256="b" * 64, idempotency_key="queue-doc")
    session.add_all([village, batch, document]); session.flush()
    for index in range(105):
        record = FRAArchiveRecord(batch_id=batch.id, document_id=document.id, legacy_reference=f"REF-{index:03}",
            state_code="TN", district="Village District", block="Block", village="Village", review_state="pending")
        session.add(record); session.flush()
        session.add_all([
            AssetFeature(village_id=village.id, asset_class="water_body", observed_value_json={"present": True, "asset_subtype": "pond"},
                source_type="field", verification_state="unverified", provenance_json={}),
            ProcessingJob(task_type="archive_extraction", entity_type="archive_record", entity_id=record.id,
                state="failed", idempotency_key=f"queue-{index}", requested_by=actor.id),
        ])
    session.add_all([
        AssetFeature(village_id=village.id, asset_class="infrastructure", observed_value_json={"present": True, "asset_subtype": "well"},
            source_type="field", verification_state="verified", provenance_json={}),
        ProcessingJob(task_type="asset_inference", entity_type="village", entity_id=village.id, state="queued",
            created_at=datetime.now(timezone.utc)-timedelta(hours=2), idempotency_key="village-job", requested_by=actor.id),
    ])
    session.flush()
    result = verifier_dashboard(session, district=" village district ")
    assert result["totals"]["archive_records_needing_review"] == 105
    assert result["totals"]["unverified_observations"] == 105
    assert result["totals"]["failed_or_overdue_jobs"] == 106
    assert all(len(queue) <= 100 for queue in result["queues"].values())
    assert result == verifier_dashboard(session, district=" village district ")
    assert planner_dashboard(session, district="Village District")["verified_assets"] == {"infrastructure": 1}
    assert verifier_dashboard(session, district="Other")["totals"]["failed_or_overdue_jobs"] == 0
