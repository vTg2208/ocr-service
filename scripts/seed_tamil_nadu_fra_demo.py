"""Seed a coherent, visibly synthetic Tamil Nadu FRA sample dataset."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path

from sqlalchemy import func, select

from app.db.fra_completion_models import AssetFeature, DSSReferral, FRAArchiveRecord, FRAVillageProfile
from app.db.fra_models import DSSRecommendation, FRAClaim, FRATitle, GramSabha, RightsHolder, SchemeRuleSet
from app.db.models import Document, User
from app.db.fra_operational_models import SchemeCatalogEntry
from app.db.session import get_session_factory
from app.services.dss_engine import evaluate_rules, recommendation_for_outcome
from app.services.dss_referrals import create_referral
from app.services.fra_archive import create_archive_record, create_import_batch, process_archive_extraction, promote_archive_record, review_archive_record
from app.services.fra_atlas import import_village_profiles
from app.services.fra_claims import add_geometry_version, create_claim
from app.services.fra_workflow import issue_title, transition_claim
from app.services.model_gateway import ManifestFRAEntityExtractor
from app.services.scheme_catalog import create_catalog_entry


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_PATH = ROOT / "data" / "synthetic_tamil_nadu_fra_archive.json"
ATLAS_PATH = ROOT / "data" / "synthetic_tamil_nadu_fra_atlas.geojson"
RULES_PATH = ROOT / "data" / "demo_dss_rules.json"
CATALOG_PATH = ROOT / "data" / "tn_scheme_catalog.sample.json"

ARCHIVE_REFERENCE_RENAMES = {
    "TN-DEMO-IFR-001": "TN-FRA-IFR-001",
    "TN-DEMO-CR-001": "TN-FRA-CR-001",
    "TN-DEMO-CFR-001": "TN-FRA-CFR-001",
}
RULE_CODE_RENAMES = {
    "DEMO-WATER-SUPPORT": "TN-FRA-WATER-SUPPORT",
    "DEMO-HOUSING-SUPPORT": "TN-FRA-HOUSING-SUPPORT",
    "DEMO-LIVELIHOOD-SUPPORT": "TN-FRA-LIVELIHOOD-SUPPORT",
}
ASSET_REFERENCE_RENAMES = {
    "tn-demo-scene-2005": "tn-sample-scene-2005",
    "tn-demo-scene-2025": "tn-sample-scene-2025",
    "tn-demo-scene-2025-yercaud": "tn-sample-scene-2025-yercaud",
}


@dataclass(frozen=True)
class SeedReport:
    created: int
    villages: int
    archive_records: int
    claims: int
    assets: int
    recommendations: int


COUNTED_MODELS = (FRAVillageProfile, FRAArchiveRecord, FRAClaim, FRATitle, AssetFeature, SchemeRuleSet, DSSRecommendation, SchemeCatalogEntry)


def _count(session) -> int:
    return sum(session.scalar(select(func.count()).select_from(model)) or 0 for model in COUNTED_MODELS)


def _load(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _apply_claim_identity(claim: FRAClaim, fields: dict) -> None:
    claim.claim_number = fields["claim_number"]
    claim.provenance_json = {"synthetic": True, "source": "tn-sample-v1"}
    claim.rights_holder.display_name = fields["holder_name"]
    claim.rights_holder.claimant_category = "synthetic_sample"
    claim.rights_holder.metadata_json = {"synthetic": True, "source": "tn-sample-v1"}
    gram_sabha = claim.gram_sabha or claim.rights_holder.gram_sabha
    if gram_sabha is not None:
        gram_sabha.name = f"{fields['village']} Gram Sabha"
        gram_sabha.village = fields["village"]
        gram_sabha.block = fields["block"]
        gram_sabha.district = fields["district"]
        gram_sabha.metadata_json = {"synthetic": True, "source": "tn-sample-v1"}
    for decision in claim.decisions:
        decision.authority_level = "synthetic_sample"
        decision.outcome = "sample_progression"
        decision.reasons_json = ["Synthetic workflow progression for interface testing"]
        decision.request_id = "tn-sample-seed"
    for geometry in claim.geometry_versions:
        geometry.boundary_quality = "synthetic_sample"
        geometry.provenance_json = {
            **(geometry.provenance_json or {}),
            "synthetic": True,
            "source": "tn-sample-v1",
        }


def _merge_claim(session, duplicate: FRAClaim, canonical: FRAClaim) -> None:
    if duplicate.id == canonical.id:
        return
    for record in session.scalars(
        select(FRAArchiveRecord).where(FRAArchiveRecord.promoted_claim_id == duplicate.id)
    ):
        record.promoted_claim = canonical
    canonical_geometries = {item.version: item for item in canonical.geometry_versions}
    for geometry in list(duplicate.geometry_versions):
        target = canonical_geometries.get(geometry.version)
        if target is None:
            geometry.claim = canonical
            canonical_geometries[geometry.version] = geometry
            continue
        for title in session.scalars(
            select(FRATitle).where(FRATitle.geometry_version_id == geometry.id)
        ):
            title.geometry_version_id = target.id
        for observation in duplicate.satellite_observations:
            if observation.geometry_version_id == geometry.id:
                observation.geometry_version_id = target.id
        session.delete(geometry)
    session.flush()
    canonical_titles = {item.version: item for item in canonical.titles}
    for title in list(duplicate.titles):
        if title.version in canonical_titles:
            session.delete(title)
        else:
            title.claim = canonical
            canonical_titles[title.version] = title
    session.flush()
    for decision in list(duplicate.decisions):
        decision.claim = canonical
    for evidence in list(duplicate.evidence_items):
        evidence.claim = canonical
    for observation in list(duplicate.satellite_observations):
        observation.claim = canonical
    for asset in session.scalars(
        select(AssetFeature).where(AssetFeature.claim_id == duplicate.id)
    ):
        asset.claim = canonical
    for recommendation in list(duplicate.dss_recommendations):
        recommendation.claim = canonical
    session.flush()
    session.delete(duplicate)
    session.flush()


def _merge_rule(session, duplicate: SchemeRuleSet, canonical: SchemeRuleSet) -> None:
    if duplicate.id == canonical.id:
        return
    for recommendation in list(duplicate.recommendations):
        existing = session.scalar(
            select(DSSRecommendation).where(
                DSSRecommendation.actor_id == recommendation.actor_id,
                DSSRecommendation.rule_set_id == canonical.id,
                DSSRecommendation.idempotency_key == recommendation.idempotency_key,
            )
        )
        if existing is None:
            recommendation.rule_set = canonical
            continue
        existing_referral = session.scalar(
            select(DSSReferral).where(DSSReferral.recommendation_id == existing.id)
        )
        for referral in session.scalars(
            select(DSSReferral).where(DSSReferral.recommendation_id == recommendation.id)
        ):
            if existing_referral is None:
                referral.recommendation_id = existing.id
                existing_referral = referral
            else:
                session.delete(referral)
        session.delete(recommendation)
    session.flush()
    session.delete(duplicate)
    session.flush()


def _refresh_legacy_visible_values(session) -> None:
    payload = _load(ARCHIVE_PATH)
    items = {item["legacy_reference"]: item for item in payload["records"]}
    metadata = payload["metadata"]
    for old_reference, new_reference in ARCHIVE_REFERENCE_RENAMES.items():
        records = list(
            session.scalars(
                select(FRAArchiveRecord).where(
                    FRAArchiveRecord.legacy_reference.in_([old_reference, new_reference])
                )
            )
        )
        if not records:
            continue
        canonical = next(
            (row for row in records if row.legacy_reference == new_reference),
            records[0],
        )
        item = items[new_reference]
        fields = dict(item["fields"])
        for duplicate in [row for row in records if row.id != canonical.id]:
            duplicate.document.original_filename = f"{new_reference}.legacy.synthetic.txt"
            duplicate.batch.source_label = "Tamil Nadu synthetic FRA archive"
            duplicate.batch.provenance_json = dict(metadata)
            had_extractions = bool(duplicate.extraction_runs)
            for extraction in list(duplicate.extraction_runs):
                extraction.archive_record = canonical
            if duplicate.promoted_claim is not None:
                duplicate_fields = dict(fields)
                duplicate_fields["claim_number"] = duplicate.promoted_claim.claim_number
                _apply_claim_identity(duplicate.promoted_claim, duplicate_fields)
                if canonical.promoted_claim is None:
                    canonical.promoted_claim_id = duplicate.promoted_claim.id
                elif canonical.promoted_claim_id != duplicate.promoted_claim_id:
                    _merge_claim(
                        session,
                        duplicate.promoted_claim,
                        canonical.promoted_claim,
                    )
            duplicate.batch.record_count = max(0, duplicate.batch.record_count - 1)
            if had_extractions:
                duplicate.batch.processed_count = max(0, duplicate.batch.processed_count - 1)
            session.flush()
            session.delete(duplicate)
            session.flush()
        canonical.legacy_reference = new_reference
        canonical.claim_number = fields["claim_number"]
        canonical.holder_display_name = fields["holder_name"]
        canonical.district = fields["district"]
        canonical.block = fields["block"]
        canonical.village = fields["village"]
        canonical.right_type = fields["right_type"]
        canonical.claim_status = fields["claim_status"]
        canonical.claim_year = fields["claim_year"]
        canonical.provenance_json = dict(metadata)
        canonical.batch.source_label = "Tamil Nadu synthetic FRA archive"
        canonical.batch.provenance_json = dict(metadata)
        if canonical.reviewed_fields_json:
            canonical.reviewed_fields_json = fields
        for extraction in canonical.extraction_runs:
            extraction.raw_text = item["raw_text"]
            extraction.standardized_json = fields
            extraction.entity_model_version = "tn-sample-manifest-v1"
            extraction.provenance_json = {
                "adapter": "manifest",
                "synthetic": True,
                "document_reference": new_reference,
            }
        canonical.document.idempotency_key = f"tn-demo-archive:{new_reference}"
        canonical.document.original_filename = f"{new_reference}.synthetic.txt"
        if canonical.promoted_claim is not None:
            _apply_claim_identity(canonical.promoted_claim, fields)

    native_claims = list(
        session.scalars(
            select(FRAClaim).where(
                FRAClaim.claim_number.in_(["TN-DEMO-CFR-NATIVE-001", "TN-FRA-CFR-NATIVE-001"])
            )
        )
    )
    if native_claims:
        native_claim = next(
            (
                claim
                for claim in native_claims
                if claim.claim_number == "TN-FRA-CFR-NATIVE-001"
            ),
            native_claims[0],
        )
        for duplicate in [claim for claim in native_claims if claim.id != native_claim.id]:
            duplicate.rights_holder.display_name = "Solai Forest Collective"
            duplicate.rights_holder.claimant_category = "synthetic_sample"
            duplicate.rights_holder.metadata_json = {
                "synthetic": True,
                "source": "tn-sample-v1",
            }
            _merge_claim(session, duplicate, native_claim)
        _apply_claim_identity(
            native_claim,
            {
                "claim_number": "TN-FRA-CFR-NATIVE-001",
                "holder_name": "Solai Forest Collective",
                "district": "The Nilgiris",
                "block": "Kotagiri",
                "village": "Solai",
            },
        )
    titles = list(
        session.scalars(
            select(FRATitle).where(
                FRATitle.title_number.in_(["TN-DEMO-TITLE-IFR-001", "TN-FRA-TITLE-IFR-001"])
            )
        )
    )
    if titles:
        canonical_title = next(
            (
                title
                for title in titles
                if title.title_number == "TN-FRA-TITLE-IFR-001"
            ),
            titles[0],
        )
        for duplicate in [title for title in titles if title.id != canonical_title.id]:
            session.delete(duplicate)
        canonical_title.title_number = "TN-FRA-TITLE-IFR-001"
        session.flush()
    for old_reference, new_reference in ASSET_REFERENCE_RENAMES.items():
        assets = list(
            session.scalars(
                select(AssetFeature).where(
                    AssetFeature.source_reference.in_([old_reference, new_reference])
                )
            )
        )
        for asset_class in {asset.asset_class for asset in assets}:
            matching = [asset for asset in assets if asset.asset_class == asset_class]
            canonical_asset = next(
                (
                    asset
                    for asset in matching
                    if asset.source_reference == new_reference
                ),
                matching[0],
            )
            for duplicate in [asset for asset in matching if asset.id != canonical_asset.id]:
                if canonical_asset.claim_id is None and duplicate.claim_id is not None:
                    canonical_asset.claim = duplicate.claim
                if canonical_asset.village_id is None and duplicate.village_id is not None:
                    canonical_asset.village = duplicate.village
                if canonical_asset.polygon_geometry is None and duplicate.polygon_geometry is not None:
                    canonical_asset.polygon_geometry = duplicate.polygon_geometry
                if canonical_asset.point_geometry_json is None and duplicate.point_geometry_json is not None:
                    canonical_asset.point_geometry_json = duplicate.point_geometry_json
                if canonical_asset.acquired_at is None and duplicate.acquired_at is not None:
                    canonical_asset.acquired_at = duplicate.acquired_at
                if canonical_asset.confidence is None and duplicate.confidence is not None:
                    canonical_asset.confidence = duplicate.confidence
                if canonical_asset.inference_run_id is None and duplicate.inference_run_id is not None:
                    canonical_asset.inference_run = duplicate.inference_run
                if (
                    canonical_asset.verification_state != "verified"
                    and duplicate.verification_state == "verified"
                ):
                    canonical_asset.verification_state = duplicate.verification_state
                    canonical_asset.verified_by = duplicate.verified_by
                    canonical_asset.verified_at = duplicate.verified_at
                for dependent in session.scalars(
                    select(AssetFeature).where(AssetFeature.supersedes_id == duplicate.id)
                ):
                    dependent.supersedes_id = (
                        None if dependent.id == canonical_asset.id else canonical_asset.id
                    )
                session.delete(duplicate)
            canonical_asset.source_reference = new_reference
            canonical_asset.observed_value_json = {
                **(canonical_asset.observed_value_json or {}),
                "present": True,
                "coverage_note": "Sample observation available",
            }
            if canonical_asset.verification_state == "verified":
                canonical_asset.verification_reasons_json = ["Synthetic sample reviewed against the source manifest"]
            else:
                canonical_asset.verification_reasons_json = ["Awaiting human verification"]
            session.flush()
    for item in _load(RULES_PATH):
        old_code = next(old for old, new in RULE_CODE_RENAMES.items() if new == item["scheme_code"])
        legacy_rules = list(
            session.scalars(
                select(SchemeRuleSet).where(
                    SchemeRuleSet.scheme_code == old_code,
                    SchemeRuleSet.version == "demo-1",
                )
            )
        )
        current_rule = session.scalar(
            select(SchemeRuleSet).where(
                SchemeRuleSet.scheme_code == item["scheme_code"],
                SchemeRuleSet.version == item["version"],
            )
        )
        if legacy_rules and current_rule is None:
            current_rule = legacy_rules.pop(0)
            current_rule.scheme_code = item["scheme_code"]
            current_rule.version = item["version"]
        for duplicate in legacy_rules:
            _merge_rule(session, duplicate, current_rule)
        if current_rule is not None:
            current_rule.display_name = item["display_name"]
            current_rule.required_facts_json = item["required_facts"]
            current_rule.condition_json = item["condition"]
            current_rule.recommendation_text = item["recommendation_text"]
            current_rule.source_reference = item["source_reference"]
            current_rule.active = True
    session.flush()


def _document(session, actor_id, reference: str) -> Document:
    key = f"tn-demo-archive:{reference}"
    existing = session.scalar(select(Document).where(Document.uploaded_by == actor_id, Document.idempotency_key == key))
    if existing is not None:
        return existing
    digest = hashlib.sha256(reference.encode("utf-8")).hexdigest()
    document = Document(
        uploaded_by=actor_id,
        storage_key=f"private/synthetic-fra/{digest}.txt",
        original_filename=f"{reference}.synthetic.txt",
        content_type="text/plain",
        sha256=digest,
        ocr_status="completed",
        idempotency_key=key,
    )
    session.add(document); session.flush()
    return document


def _seed_archive(session, actor_id) -> list[FRAArchiveRecord]:
    payload = _load(ARCHIVE_PATH); metadata = payload["metadata"]
    batch = create_import_batch(
        session,
        source_label="Tamil Nadu synthetic FRA archive",
        state="Tamil Nadu",
        actor_id=actor_id,
        idempotency_key="tn-demo-archive-v1",
        synthetic=True,
        provenance=metadata,
    )
    extractor = ManifestFRAEntityExtractor("tn-sample-manifest-v1")
    records = []
    for item in payload["records"]:
        record = create_archive_record(
            session,
            batch=batch,
            document_id=_document(session, actor_id, item["legacy_reference"]).id,
            legacy_reference=item["legacy_reference"],
            actor_id=actor_id,
            provenance=metadata,
        )
        if not record.extraction_runs:
            process_archive_extraction(
                session,
                record,
                extractor=extractor,
                manifest=item["fields"],
                raw_text=item["raw_text"],
                ocr_model_version="synthetic-transcription-v1",
                actor_id=actor_id,
            )
        if not item["review"]:
            fields = record.latest_extraction.standardized_json
            record.claim_number = fields.get("claim_number")
            record.holder_display_name = fields.get("holder_name")
            record.district = fields.get("district")
            record.block = fields.get("block")
            record.village = fields.get("village")
            record.right_type = fields.get("right_type")
            record.claim_status = fields.get("claim_status")
            record.claim_year = fields.get("claim_year")
        if item["review"] and record.review_state == "needs_review":
            review_archive_record(
                session,
                record,
                reviewed_fields=record.latest_extraction.standardized_json,
                reviewer_id=actor_id,
                expected_revision=record.revision,
            )
        if item["promote"] and record.review_state in {"reviewed", "promoted"}:
            promote_archive_record(session, record, actor_id=actor_id)
        records.append(record)
    return records


def _native_cfr_claim(session, actor_id) -> FRAClaim:
    existing = session.scalar(select(FRAClaim).where(FRAClaim.claim_number == "TN-FRA-CFR-NATIVE-001"))
    if existing is not None:
        return existing
    gram_sabha = session.scalar(select(GramSabha).where(GramSabha.external_reference == "tn-demo-solai-gs"))
    if gram_sabha is None:
        gram_sabha = GramSabha(name="Solai Gram Sabha", village="Solai", block="Kotagiri", district="The Nilgiris", state="Tamil Nadu", external_reference="tn-demo-solai-gs", metadata_json={"synthetic": True, "source": "tn-sample-v1"})
        session.add(gram_sabha); session.flush()
    holder = session.scalar(select(RightsHolder).where(RightsHolder.external_reference == "tn-demo-solai-collective"))
    if holder is None:
        holder = RightsHolder(display_name="Solai Forest Collective", holder_type="community", claimant_category="synthetic_sample", external_reference="tn-demo-solai-collective", gram_sabha=gram_sabha, metadata_json={"synthetic": True})
        session.add(holder); session.flush()
    return create_claim(session, claim_number="TN-FRA-CFR-NATIVE-001", right_type="CFR", rights_holder_id=holder.id, gram_sabha_id=gram_sabha.id, submitted_by=actor_id, claimed_area_sqm=410000, provenance={"synthetic": True, "source": "tn-sample-native"})


def _advance_claim(session, claim: FRAClaim, target: str, actor_id) -> None:
    paths = {
        "submitted": ["submitted"],
        "granted": ["submitted", "gram_sabha_verified", "sdlc_review", "dlc_decided", "granted"],
    }
    if claim.status == target:
        return
    for state in paths[target]:
        if claim.status == state:
            continue
        if state not in {"submitted", "gram_sabha_verified", "sdlc_review", "dlc_decided", "granted"}:
            continue
        transition_claim(session, claim, target_status=state, authority_level="synthetic_sample", outcome="sample_progression", reasons=["Synthetic workflow progression for interface testing"], actor_id=actor_id, request_id="tn-sample-seed")


def _seed_claim_details(session, records, actor_id) -> list[FRAClaim]:
    claims = [record.promoted_claim for record in records if record.promoted_claim is not None]
    claims.append(_native_cfr_claim(session, actor_id))
    villages = {item.village_name: item for item in session.scalars(select(FRAVillageProfile))}
    for claim in claims:
        village_name = claim.gram_sabha.village if claim.gram_sabha else claim.rights_holder.gram_sabha.village
        village = villages[village_name]
        if not claim.geometry_versions:
            add_geometry_version(session, claim, geometry=village.boundary, source="synthetic_village_reference", provenance={"synthetic": True, "village_code": village.village_code}, boundary_quality="synthetic_sample", actor_id=actor_id)
        _advance_claim(session, claim, "granted" if claim.right_type == "IFR" else "submitted", actor_id)
        if claim.status == "granted" and not claim.titles:
            issue_title(session, claim, title_number="TN-FRA-TITLE-IFR-001", geometry_version_id=claim.geometry_versions[-1].id, issued_by=actor_id, metadata={"synthetic": True, "not_authoritative": True}, request_id="tn-sample-seed")
    return claims


def _seed_assets(session, villages, actor_id) -> None:
    by_name = {village.village_name: village for village in villages}
    observations = [
        (by_name["Kottur"], "water_body", "tn-sample-scene-2005", date(2005, 1, 15), [79.11, 10.71], "verified"),
        (by_name["Kottur"], "agricultural_cover", "tn-sample-scene-2025", date(2025, 1, 15), [79.108, 10.708], "unverified"),
        (by_name["Aranya Malai"], "forest_cover", "tn-sample-scene-2025-yercaud", date(2025, 2, 12), [78.65, 11.49], "unverified"),
        (by_name["Solai"], "homestead", "tn-sample-scene-2025-kotagiri", date(2025, 3, 10), [76.74, 11.43], "unverified"),
    ]
    for village, asset_class, reference, acquired_at, point, verification in observations:
        if session.scalar(select(AssetFeature).where(AssetFeature.source_reference == reference, AssetFeature.asset_class == asset_class)):
            continue
        session.add(AssetFeature(village=village, asset_class=asset_class, point_geometry_json={"type": "Point", "coordinates": point}, observed_value_json={"present": True, "coverage_note": "Sample observation available"}, acquired_at=acquired_at, confidence=0.78, source_type="synthetic_manifest", source_reference=reference, provenance_json={"synthetic": True, "pixel_inference": False, "legal_role": "supporting_observation"}, verification_state=verification, verification_reasons_json=["Synthetic sample reviewed against the source manifest"] if verification == "verified" else ["Awaiting human verification"], verified_by=actor_id if verification == "verified" else None, verified_at=datetime.now(timezone.utc) if verification == "verified" else None, synthetic=True))
    session.flush()


def _seed_planning(session, claims, actor_id) -> None:
    for item in _load(CATALOG_PATH):
        existing_catalog = session.scalar(select(SchemeCatalogEntry).where(
            SchemeCatalogEntry.scheme_code == item["scheme_code"],
            SchemeCatalogEntry.version == item["version"],
        ))
        if existing_catalog is None:
            create_catalog_entry(session, item, actor_id=actor_id, request_id="tn-sample-seed")
    seeded_rules = []
    for item in _load(RULES_PATH):
        rule = session.scalar(
            select(SchemeRuleSet).where(
                SchemeRuleSet.scheme_code == item["scheme_code"],
                SchemeRuleSet.version == item["version"],
            )
        )
        if rule is None:
            rule = SchemeRuleSet(created_by=actor_id)
            session.add(rule)
        rule.scheme_code = item["scheme_code"]
        rule.display_name = item["display_name"]
        rule.version = item["version"]
        rule.required_facts_json = item["required_facts"]
        rule.condition_json = item["condition"]
        rule.recommendation_text = item["recommendation_text"]
        rule.source_reference = item["source_reference"]
        rule.active = True
        seeded_rules.append(rule)
    session.flush()
    seeded_rule_ids = {rule.id for rule in seeded_rules}
    for claim in claims:
        evaluate_rules(session, claim_id=claim.id, facts={"has_title": bool(claim.titles), "water_body_present": claim.right_type == "CR", "homestead_present": False, "agricultural_cover": 0.31}, actor_id=actor_id, idempotency_key=f"tn-demo-evaluation-{claim.id}", rule_set_ids=seeded_rule_ids)
    seeded_claim_ids = [claim.id for claim in claims]
    for recommendation in session.scalars(
        select(DSSRecommendation).where(
            DSSRecommendation.claim_id.in_(seeded_claim_ids),
            DSSRecommendation.rule_set_id.in_(seeded_rule_ids),
        )
    ):
        rule = recommendation.rule_set
        output = dict(recommendation.output_json or {})
        output.update(
            {
                "scheme_code": rule.scheme_code,
                "scheme_name": rule.display_name,
                "rule_version": rule.version,
                "recommendation": recommendation_for_outcome(
                    recommendation.outcome, rule.recommendation_text
                ),
                "source_reference": rule.source_reference,
                "advisory_only": True,
            }
        )
        recommendation.rule_version = rule.version
        recommendation.output_json = output
    recommendation = session.scalar(
        select(DSSRecommendation)
        .where(
            DSSRecommendation.claim_id.in_(seeded_claim_ids),
            DSSRecommendation.rule_set_id.in_(seeded_rule_ids),
            DSSRecommendation.outcome == "recommended",
        )
        .order_by(DSSRecommendation.created_at)
    )
    if recommendation is not None:
        create_referral(session, recommendation_id=recommendation.id, department="Tamil Nadu Synthetic Rural Development Desk", priority="normal", actor_id=actor_id, idempotency_key="tn-demo-referral-v1", notes="Synthetic advisory referral; no benefit approval.")


def seed_demo(session, *, actor_id) -> SeedReport:
    actor = session.get(User, actor_id)
    if actor is None or actor.role != "admin":
        raise PermissionError("The Tamil Nadu sample-data seed requires an administrator.")
    _refresh_legacy_visible_values(session)
    before = _count(session)
    import_village_profiles(session, _load(ATLAS_PATH), actor_id=actor_id)
    records = _seed_archive(session, actor_id)
    claims = _seed_claim_details(session, records, actor_id)
    villages = list(session.scalars(select(FRAVillageProfile).order_by(FRAVillageProfile.village_code)))
    _seed_assets(session, villages, actor_id)
    _seed_planning(session, claims, actor_id)
    session.flush()
    after = _count(session)
    return SeedReport(created=after - before, villages=len(villages), archive_records=len(records), claims=len(claims), assets=session.scalar(select(func.count()).select_from(AssetFeature)) or 0, recommendations=session.scalar(select(func.count()).select_from(DSSRecommendation)) or 0)


def _admin(session) -> User:
    user = session.scalar(select(User).where(User.external_id == "tn-demo-admin"))
    if user is None:
        user = User(external_id="tn-demo-admin", display_name="Tamil Nadu Sample Administrator", role="admin")
        session.add(user); session.flush()
    elif user.role != "admin":
        raise PermissionError("tn-demo-admin exists without the administrator role.")
    else:
        user.display_name = "Tamil Nadu Sample Administrator"
    return user


def main() -> None:
    with get_session_factory()() as session:
        admin = _admin(session)
        report = seed_demo(session, actor_id=admin.id)
        session.commit()
        print(json.dumps(report.__dict__, indent=2))


if __name__ == "__main__":
    main()
