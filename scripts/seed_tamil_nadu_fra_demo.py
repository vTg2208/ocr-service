"""Seed a coherent, visibly synthetic Tamil Nadu FRA demonstration story."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path

from sqlalchemy import func, select

from app.db.fra_completion_models import AssetFeature, FRAArchiveRecord, FRAVillageProfile
from app.db.fra_models import DSSRecommendation, FRAClaim, FRATitle, GramSabha, RightsHolder, SchemeRuleSet
from app.db.models import Document, User
from app.db.session import get_session_factory
from app.services.dss_engine import evaluate_rules
from app.services.dss_referrals import create_referral
from app.services.fra_archive import create_archive_record, create_import_batch, process_archive_extraction, promote_archive_record, review_archive_record
from app.services.fra_atlas import import_village_profiles
from app.services.fra_claims import add_geometry_version, create_claim
from app.services.fra_workflow import issue_title, transition_claim
from app.services.model_gateway import ManifestFRAEntityExtractor


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_PATH = ROOT / "data" / "synthetic_tamil_nadu_fra_archive.json"
ATLAS_PATH = ROOT / "data" / "synthetic_tamil_nadu_fra_atlas.geojson"
RULES_PATH = ROOT / "data" / "demo_dss_rules.json"


@dataclass(frozen=True)
class SeedReport:
    created: int
    villages: int
    archive_records: int
    claims: int
    assets: int
    recommendations: int


COUNTED_MODELS = (FRAVillageProfile, FRAArchiveRecord, FRAClaim, FRATitle, AssetFeature, SchemeRuleSet, DSSRecommendation)


def _count(session) -> int:
    return sum(session.scalar(select(func.count()).select_from(model)) or 0 for model in COUNTED_MODELS)


def _load(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


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
    extractor = ManifestFRAEntityExtractor("tn-demo-manifest-v1")
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
    existing = session.scalar(select(FRAClaim).where(FRAClaim.claim_number == "TN-DEMO-CFR-NATIVE-001"))
    if existing is not None:
        return existing
    gram_sabha = session.scalar(select(GramSabha).where(GramSabha.external_reference == "tn-demo-solai-gs"))
    if gram_sabha is None:
        gram_sabha = GramSabha(name="Solai Demo Gram Sabha", village="Solai Demo", block="Kotagiri", district="The Nilgiris", state="Tamil Nadu", external_reference="tn-demo-solai-gs", metadata_json={"synthetic": True, "source": "tn-demo-v1"})
        session.add(gram_sabha); session.flush()
    holder = session.scalar(select(RightsHolder).where(RightsHolder.external_reference == "tn-demo-solai-collective"))
    if holder is None:
        holder = RightsHolder(display_name="Solai Demo Forest Collective", holder_type="community", claimant_category="synthetic_demo", external_reference="tn-demo-solai-collective", gram_sabha=gram_sabha, metadata_json={"synthetic": True})
        session.add(holder); session.flush()
    return create_claim(session, claim_number="TN-DEMO-CFR-NATIVE-001", right_type="CFR", rights_holder_id=holder.id, gram_sabha_id=gram_sabha.id, submitted_by=actor_id, claimed_area_sqm=410000, provenance={"synthetic": True, "source": "tn-demo-native"})


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
        transition_claim(session, claim, target_status=state, authority_level="synthetic_demo", outcome="demo_progression", reasons=[], actor_id=actor_id, request_id="tn-demo-seed")


def _seed_claim_details(session, records, actor_id) -> list[FRAClaim]:
    claims = [record.promoted_claim for record in records if record.promoted_claim is not None]
    claims.append(_native_cfr_claim(session, actor_id))
    villages = {item.village_name: item for item in session.scalars(select(FRAVillageProfile))}
    for claim in claims:
        village_name = claim.gram_sabha.village if claim.gram_sabha else claim.rights_holder.gram_sabha.village
        village = villages[village_name]
        if not claim.geometry_versions:
            add_geometry_version(session, claim, geometry=village.boundary, source="synthetic_village_reference", provenance={"synthetic": True, "village_code": village.village_code}, boundary_quality="synthetic_demo", actor_id=actor_id)
        _advance_claim(session, claim, "granted" if claim.right_type == "IFR" else "submitted", actor_id)
        if claim.status == "granted" and not claim.titles:
            issue_title(session, claim, title_number="TN-DEMO-TITLE-IFR-001", geometry_version_id=claim.geometry_versions[-1].id, issued_by=actor_id, metadata={"synthetic": True, "not_authoritative": True}, request_id="tn-demo-seed")
    return claims


def _seed_assets(session, villages, actor_id) -> None:
    by_name = {village.village_name: village for village in villages}
    observations = [
        (by_name["Kottur Demo"], "water_body", "tn-demo-scene-2005", date(2005, 1, 15), [79.11, 10.71], "verified"),
        (by_name["Kottur Demo"], "agricultural_cover", "tn-demo-scene-2025", date(2025, 1, 15), [79.108, 10.708], "unverified"),
        (by_name["Aranya Malai Demo"], "forest_cover", "tn-demo-scene-2025-yercaud", date(2025, 2, 12), [78.65, 11.49], "unverified"),
    ]
    for village, asset_class, reference, acquired_at, point, verification in observations:
        if session.scalar(select(AssetFeature).where(AssetFeature.source_reference == reference, AssetFeature.asset_class == asset_class)):
            continue
        session.add(AssetFeature(village=village, asset_class=asset_class, point_geometry_json={"type": "Point", "coordinates": point}, observed_value_json={"present": True}, acquired_at=acquired_at, confidence=0.78, source_type="synthetic_manifest", source_reference=reference, provenance_json={"synthetic": True, "pixel_inference": False, "legal_role": "supporting_observation"}, verification_state=verification, verification_reasons_json=["Synthetic reviewer demonstration"] if verification == "verified" else [], verified_by=actor_id if verification == "verified" else None, verified_at=datetime.now(timezone.utc) if verification == "verified" else None, synthetic=True))
    session.flush()


def _seed_planning(session, claims, actor_id) -> None:
    for item in _load(RULES_PATH):
        rule = session.scalar(select(SchemeRuleSet).where(SchemeRuleSet.scheme_code == item["scheme_code"], SchemeRuleSet.version == item["version"]))
        if rule is None:
            session.add(SchemeRuleSet(scheme_code=item["scheme_code"], display_name=item["display_name"], version=item["version"], required_facts_json=item["required_facts"], condition_json=item["condition"], recommendation_text=item["recommendation_text"], source_reference=item["source_reference"], active=True, created_by=actor_id))
    session.flush()
    for claim in claims:
        evaluate_rules(session, claim_id=claim.id, facts={"has_title": bool(claim.titles), "water_body_present": claim.right_type == "CR", "homestead_present": False, "agricultural_cover": 0.31}, actor_id=actor_id, idempotency_key=f"tn-demo-evaluation-{claim.id}")
    recommendation = session.scalar(select(DSSRecommendation).where(DSSRecommendation.outcome == "recommended").order_by(DSSRecommendation.created_at))
    if recommendation is not None:
        create_referral(session, recommendation_id=recommendation.id, department="Tamil Nadu Synthetic Rural Development Desk", priority="normal", actor_id=actor_id, idempotency_key="tn-demo-referral-v1", notes="Synthetic advisory referral; no benefit approval.")


def seed_demo(session, *, actor_id) -> SeedReport:
    actor = session.get(User, actor_id)
    if actor is None or actor.role != "admin":
        raise PermissionError("The Tamil Nadu demo seed requires an administrator.")
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
        user = User(external_id="tn-demo-admin", display_name="Tamil Nadu Demo Administrator", role="admin")
        session.add(user); session.flush()
    elif user.role != "admin":
        raise PermissionError("tn-demo-admin exists without the administrator role.")
    return user


def main() -> None:
    with get_session_factory()() as session:
        admin = _admin(session)
        report = seed_demo(session, actor_id=admin.id)
        session.commit()
        print(json.dumps(report.__dict__, indent=2))


if __name__ == "__main__":
    main()
