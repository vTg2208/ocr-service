"""Seed a coherent, visibly synthetic Tamil Nadu FRA sample dataset."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path

from sqlalchemy import func, select
from shapely.geometry import shape

from app.db.fra_completion_models import AssetFeature, DSSReferral, FRAArchiveRecord, FRAVillageProfile
from app.db.fra_models import DSSRecommendation, FRAClaim, FRAEvidenceItem, FRATitle, GramSabha, RightsHolder, SchemeRuleSet
from app.db.models import Document, User
from app.db.fra_operational_models import ImageryArtifact, ImagerySceneRecord, SchemeCatalogEntry
from app.db.session import get_session_factory
from app.services.dss_engine import DISCLAIMER, evaluate_rules
from app.services.dss_facts import CURRENT_FACT_VERSION, derive_facts, fact_values
from app.services.dss_referrals import create_referral
from app.services.fra_archive import create_archive_record, create_import_batch, process_archive_extraction, promote_archive_record, review_archive_record
from app.services.fra_atlas import import_village_profiles
from app.services.fra_claims import add_geometry_version, create_claim
from app.services.fra_workflow import issue_title, transition_claim
from app.services.model_gateway import ManifestFRAEntityExtractor
from app.services.scheme_catalog import create_catalog_entry
from app.services.village_asset_profiles import refresh_village_asset_profiles


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
ASSET_REFERENCE_RENAMES = {
    "tn-demo-scene-2005": "tn-sample-scene-2005",
    "tn-demo-scene-2025": "tn-sample-scene-2025",
    "tn-demo-scene-2025-yercaud": "tn-sample-scene-2025-yercaud",
}

EDUCATIONAL_VILLAGES = (
    ("TN-13", "Thanjavur", "TN-13-01", "Kumbakonam", "TN-13-01-001", "Kottur", 79.11, 10.71, "Irular", "Muthu", "rainfed agriculture", 86),
    ("TN-07", "Salem", "TN-07-02", "Yercaud", "TN-07-02-001", "Aranya Malai", 78.65, 11.49, "Malayali", "Lakshmi", "minor forest produce", 74),
    ("TN-11", "The Nilgiris", "TN-11-03", "Kotagiri", "TN-11-03-001", "Solai", 76.74, 11.43, "Kurumba", "Rajan", "forest-based work", 63),
    ("TN-30", "Kanniyakumari", "TN-30-04", "Thovalai", "TN-30-04-001", "Vellimalai", 77.34, 8.25, "Kanikaran", "Selvi", "horticulture", 92),
    ("TN-11", "The Nilgiris", "TN-11-03", "Kotagiri", "TN-11-03-002", "Kodanadu", 76.89, 11.46, "Irula", "Mani", "tea and forest produce", 108),
    ("TN-05", "Dharmapuri", "TN-05-07", "Harur", "TN-05-07-001", "Sittilingi", 78.34, 11.96, "Malayali", "Amudha", "millet cultivation", 121),
    ("TN-15", "Tiruchirappalli", "TN-15-06", "Uppiliyapuram", "TN-15-06-001", "Pachamalai", 78.58, 11.25, "Malayali", "Perumal", "smallholder farming", 97),
    ("TN-33", "Kallakurichi", "TN-33-05", "Sankarapuram", "TN-33-05-001", "Kalrayan Hills", 78.73, 11.80, "Malayali", "Kavitha", "forest produce and farming", 116),
    ("TN-06", "Tiruvannamalai", "TN-06-09", "Jamunamarathur", "TN-06-09-001", "Jawadhu Hills", 78.88, 12.60, "Malayali", "Murugan", "horticulture and forest work", 133),
    ("TN-34", "Tenkasi", "TN-34-03", "Kadayam", "TN-34-03-001", "Alwarkurichi", 77.39, 8.78, "Kanikaran", "Meena", "mixed farming", 104),
    ("TN-09", "Namakkal", "TN-09-05", "Sendamangalam", "TN-09-05-001", "Kolli Malai", 78.34, 11.25, "Malayali", "Chinnasamy", "spice cultivation", 142),
    ("TN-12", "Coimbatore", "TN-12-08", "Anaimalai", "TN-12-08-001", "Anaimalai", 76.95, 10.58, "Kadar", "Velan", "forest produce and livestock", 88),
    ("TN-22", "Dindigul", "TN-22-02", "Natham", "TN-22-02-001", "Sirumalai", 77.99, 10.18, "Paliyan", "Mallika", "fruit cultivation", 79),
    ("TN-25", "Theni", "TN-25-04", "Chinnamanur", "TN-25-04-001", "Megamalai", 77.39, 9.65, "Paliyan", "Suresh", "plantation work", 68),
    ("TN-30", "Kanniyakumari", "TN-30-07", "Thiruvattar", "TN-30-07-001", "Pechiparai", 77.31, 8.45, "Kanikaran", "Devi", "rubber and forest produce", 111),
)

OPTIONAL_EXISTING_VILLAGES = (
    ("596", "Villupuram", "73", "Kandamangalam", "632998", "Arpisampalaiyam", 79.61, 11.93, "Irular", "Anjali", "agriculture and wage work", 1109),
)

CLAIM_STATUSES = {
    "IFR": ("granted",),
    "CR": ("submitted", "gram sabha verified", "sdlc review", "dlc decided", "granted"),
    "CFR": ("submitted", "sdlc review", "dlc decided", "rejected", "remanded"),
}


def _educational_atlas_payload() -> dict:
    payload = _load(ATLAS_PATH)
    payload["metadata"] = {
        **payload["metadata"],
        "source": "Tamil Nadu FRA village reference dataset",
        "version": "tn-education-v2",
    }
    for feature in payload["features"]:
        groups = feature["properties"].get("tribal_groups") or []
        feature["properties"]["tribal_groups"] = [str(group).removeprefix("Synthetic ") for group in groups]
    existing_codes = {item["properties"]["village_code"] for item in payload["features"]}
    for district_code, district, block_code, block, village_code, village, lon, lat, community, _, livelihood, households in EDUCATIONAL_VILLAGES:
        if village_code in existing_codes:
            continue
        payload["features"].append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[
                [lon - .01, lat - .01], [lon + .01, lat - .01], [lon + .01, lat + .01],
                [lon - .01, lat + .01], [lon - .01, lat - .01],
            ]]},
            "properties": {
                "district_code": district_code, "district_name": district,
                "block_code": block_code, "block_name": block,
                "village_code": village_code, "village_name": village,
                "tribal_groups": [community],
                "socioeconomic": {
                    "water_access": "seasonal" if households % 2 else "partial piped supply",
                    "road_access": "all-weather" if households % 3 else "seasonal",
                    "primary_livelihood": livelihood, "household_count": households,
                },
            },
        })
    return payload


def _educational_archive_payload(available_villages: set[str]) -> dict:
    records = []
    villages = [
        village for village in (*EDUCATIONAL_VILLAGES, *OPTIONAL_EXISTING_VILLAGES)
        if village[5] in available_villages
    ]
    for index, village in enumerate(villages, start=1):
        _, district, _, block, _, village_name, _, _, _, household_name, _, _ = village
        holders = {
            "IFR": f"{household_name} and household",
            "CR": f"{village_name} Community Rights Committee",
            "CFR": f"{village_name} Gram Sabha Forest Council",
        }
        for right_type in ("IFR", "CR", "CFR"):
            status = CLAIM_STATUSES[right_type][(index - 1) % len(CLAIM_STATUSES[right_type])]
            reference = f"TN-FRA-{right_type}-{index:03d}"
            records.append({
                "legacy_reference": reference,
                "raw_text": (
                    f"Forest rights register entry {reference}. {holders[right_type]}, "
                    f"{village_name}, {block}, {district}. {right_type} claim recorded for review."
                ),
                "review": True,
                "promote": True,
                "fields": {
                    "holder_name": holders[right_type], "district": district, "block": block,
                    "village": village_name, "right_type": right_type, "claim_status": status,
                    "claim_number": reference, "claim_year": 2010 + ((index * 3 + len(right_type)) % 15),
                    "confidence": round(.82 + (index % 8) * .02, 2),
                },
            })
        if index <= 5:
            reference = f"TN-FRA-PENDING-{index:03d}"
            records.append({
                "legacy_reference": reference,
                "raw_text": f"Legacy individual forest-right claim from {village_name}; field verification is pending.",
                "review": False,
                "promote": False,
                "fields": {
                    "holder_name": f"{household_name} extended household", "district": district,
                    "block": block, "village": village_name, "right_type": "IFR",
                    "claim_status": "under review", "claim_number": reference,
                    "claim_year": 2018 + index, "confidence": round(.68 + index * .025, 2),
                },
            })
    return {
        "metadata": {
            "state_code": "TN", "state_name": "Tamil Nadu", "synthetic": True,
            "source": "Tamil Nadu FRA archive register", "version": "tn-education-v2",
        },
        "records": records,
    }


@dataclass(frozen=True)
class SeedReport:
    created: int
    villages: int
    archive_records: int
    claims: int
    assets: int
    recommendations: int


COUNTED_MODELS = (
    FRAVillageProfile, FRAArchiveRecord, FRAClaim, FRATitle, AssetFeature,
    ImagerySceneRecord, ImageryArtifact, SchemeRuleSet, DSSRecommendation, DSSReferral,
    SchemeCatalogEntry,
)


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


def _refresh_legacy_visible_values(session) -> None:
    """Rename unambiguous synthetic display identities without rewriting evidence."""
    items = {item["legacy_reference"]: item for item in _load(ARCHIVE_PATH)["records"]}
    for old_reference, new_reference in ARCHIVE_REFERENCE_RENAMES.items():
        records = list(session.scalars(select(FRAArchiveRecord).where(
            FRAArchiveRecord.legacy_reference.in_([old_reference, new_reference]))))
        if len(records) != 1 or records[0].legacy_reference != old_reference:
            continue
        record = records[0]
        if not record.synthetic:
            continue
        fields = items[new_reference]["fields"]
        record.legacy_reference = new_reference
        record.claim_number = fields["claim_number"]
        record.holder_display_name = fields["holder_name"]
        record.district, record.block, record.village = fields["district"], fields["block"], fields["village"]
        record.document.idempotency_key = f"tn-demo-archive:{new_reference}"
        record.document.original_filename = f"{new_reference}.synthetic.txt"
        if record.promoted_claim is not None:
            _apply_claim_identity(record.promoted_claim, fields)
    old = session.scalar(select(FRAClaim).where(FRAClaim.claim_number == "TN-DEMO-CFR-NATIVE-001"))
    current = session.scalar(select(FRAClaim).where(FRAClaim.claim_number == "TN-FRA-CFR-NATIVE-001"))
    if old is not None and current is None and (old.provenance_json or {}).get("synthetic"):
        _apply_claim_identity(old, {"claim_number": "TN-FRA-CFR-NATIVE-001", "holder_name": "Solai Forest Collective",
                                   "district": "The Nilgiris", "block": "Kotagiri", "village": "Solai"})
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
    available_villages = set(session.scalars(select(FRAVillageProfile.village_name)))
    payload = _educational_archive_payload(available_villages); metadata = payload["metadata"]
    batch = create_import_batch(
        session,
        source_label="Tamil Nadu FRA archive register",
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
        if not item["review"] and record.review_state == "needs_review":
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
            promote_archive_record(session, record, expected_revision=record.revision, actor_id=actor_id)
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
    path = ["draft", *paths[target]]
    if claim.status not in path:
        return  # Retain subsequent human workflow decisions on sample reruns.
    for state in path[path.index(claim.status) + 1:]:
        transition_claim(session, claim, target_status=state, authority_level="Registry review", outcome="workflow_progression", reasons=["Recorded workflow progression for the educational case register"], actor_id=actor_id, request_id="tn-sample-seed")


def _seed_claim_details(session, records, actor_id) -> list[FRAClaim]:
    claims = [record.promoted_claim for record in records if record.promoted_claim is not None]
    claims.append(_native_cfr_claim(session, actor_id))
    villages = {item.village_name: item for item in session.scalars(select(FRAVillageProfile))}
    included_ids = {claim.id for claim in claims}
    village_ids = [village.id for village in villages.values()]
    for claim in session.scalars(select(FRAClaim).where(FRAClaim.village_id.in_(village_ids))):
        if claim.id not in included_ids and (claim.provenance_json or {}).get("synthetic") is True:
            claims.append(claim)
            included_ids.add(claim.id)
    for claim_index, claim in enumerate(claims):
        village_name = (
            claim.village.village_name if claim.village is not None
            else claim.gram_sabha.village if claim.gram_sabha
            else claim.rights_holder.gram_sabha.village
        )
        village = villages[village_name]
        claim.village = village
        claim.rights_holder.claimant_category = "ST" if claim_index % 3 else "OTFD"
        if claim.claimed_area_sqm is None:
            claim.claimed_area_sqm = {"IFR": 6800, "CR": 42500, "CFR": 138000}[claim.right_type] + claim_index * 275
        if not claim.geometry_versions:
            min_lon, min_lat, max_lon, max_lat = shape(village.boundary).bounds
            width, height = max_lon - min_lon, max_lat - min_lat
            column = {"IFR": 0, "CR": 1, "CFR": 2}[claim.right_type]
            left = min_lon + width * (.08 + column * .3)
            bottom = min_lat + height * (.12 + (claim_index % 2) * .12)
            geometry = {"type": "MultiPolygon", "coordinates": [[[
                [left, bottom], [left + width * .24, bottom],
                [left + width * .24, bottom + height * .42], [left, bottom + height * .42],
                [left, bottom],
            ]]]}
            add_geometry_version(session, claim, geometry=geometry, source="educational_field_boundary", provenance={"synthetic": True, "village_code": village.village_code}, boundary_quality="digitized", actor_id=actor_id)
        _advance_claim(session, claim, "granted" if claim.right_type == "IFR" else "submitted", actor_id)
        if claim.status == "granted" and not claim.titles:
            issue_title(session, claim, title_number=f"TN-TITLE-{claim.claim_number}", geometry_version_id=claim.geometry_versions[-1].id, issued_by=actor_id, metadata={"synthetic": True, "not_authoritative": True}, request_id="tn-sample-seed", granted_area_sqm=float(claim.claimed_area_sqm) * .92)
        if not claim.evidence_items:
            session.add(FRAEvidenceItem(
                claim=claim, category="gram_sabha_record", legal_role="supporting",
                source="Gram Sabha proceedings register",
                description=f"Resolution and field-verification summary for {claim.claim_number}.",
                document_id=claim.document_id, source_page_start=1, source_page_end=2,
                provenance_json={"synthetic": True, "source_version": "tn-education-v2"},
                captured_at=date(2025, 4, 15), verification_state="verified", source_verified=True,
                verified_by=actor_id, verified_at=datetime.now(timezone.utc), created_by=actor_id,
            ))
    session.flush()
    return claims


def _seed_assets(session, villages, actor_id) -> None:
    legacy_references = {
        ("Kottur", "water_body"): ("tn-sample-scene-2005", date(2025, 1, 15)),
        ("Kottur", "agricultural_land"): ("tn-sample-scene-2025", date(2025, 1, 15)),
        ("Aranya Malai", "forest_cover"): ("tn-sample-scene-2025-yercaud", date(2025, 2, 12)),
        ("Solai", "homestead"): ("tn-sample-scene-2025-kotagiri", date(2025, 3, 10)),
    }
    classes = ("water_body", "agricultural_land", "forest_cover", "homestead", "road", "infrastructure")
    observations = []
    for village_index, village in enumerate(villages):
        min_lon, min_lat, max_lon, max_lat = shape(village.boundary).bounds
        for asset_index, asset_class in enumerate(classes):
            fallback = (f"tn-fra-asset-{village.village_code}-{asset_class}-2025", date(2025, 1 + (village_index % 6), 10 + asset_index))
            reference, acquired_at = legacy_references.get((village.village_name, asset_class), fallback)
            point = [min_lon + (max_lon - min_lon) * (.18 + asset_index * .12), min_lat + (max_lat - min_lat) * (.25 + (asset_index % 3) * .22)]
            value = {"present": True, "observation_note": "Reviewed field observation"}
            if asset_class in {"agricultural_land", "forest_cover"}:
                value["coverage_fraction"] = round(.28 + ((village_index + asset_index) % 5) * .1, 2)
            if asset_class == "infrastructure":
                value["asset_subtype"] = ("school", "health_center", "water_tank")[village_index % 3]
            observations.append((village, asset_class, reference, acquired_at, point, value))
    for village, asset_class, reference, acquired_at, point, value in observations:
        if session.scalar(select(AssetFeature).where(AssetFeature.source_reference.in_([reference, *[old for old, new in ASSET_REFERENCE_RENAMES.items() if new == reference]]), AssetFeature.asset_class == asset_class)):
            continue
        session.add(AssetFeature(village=village, asset_class=asset_class, point_geometry_json={"type": "Point", "coordinates": point}, observed_value_json=value, acquired_at=acquired_at, confidence=round(.76 + ((len(reference) + len(asset_class)) % 18) / 100, 2), source_type="field_register", source_reference=reference, provenance_json={"synthetic": True, "pixel_inference": False, "legal_role": "supporting_observation", "source_version": "tn-education-v2"}, verification_state="verified", verification_reasons_json=["Reviewed against the source register"], verified_by=actor_id, verified_at=datetime.now(timezone.utc), synthetic=True))
    session.flush()


def _seed_imagery(session, claims, actor_id) -> None:
    for index, claim in enumerate(claims):
        geometry = claim.geometry_versions[-1]
        scene_id = f"S2-TN-FRA-{claim.claim_number}-2025"
        scene = session.scalar(select(ImagerySceneRecord).where(
            ImagerySceneRecord.provider == "Copernicus Data Space Ecosystem",
            ImagerySceneRecord.collection == "sentinel-2-l2a",
            ImagerySceneRecord.scene_id == scene_id,
        ))
        if scene is None:
            scene = ImagerySceneRecord(
                provider="Copernicus Data Space Ecosystem", collection="sentinel-2-l2a",
                scene_id=scene_id, acquired_at=datetime(2025, 1 + index % 6, 12, tzinfo=timezone.utc),
                footprint=geometry.geometry, cloud_cover=round(4.5 + index % 12, 1),
                asset_references_json={}, license_reference="https://dataspace.copernicus.eu/",
                status="ingested", provenance_json={"synthetic": True, "source": "imagery register"},
                synthetic=True,
            )
            session.add(scene); session.flush()
        artifact_type = "analysis_ready_raster:2025-01-01:2025-06-30"
        existing = session.scalar(select(ImageryArtifact).where(
            ImageryArtifact.claim_id == claim.id,
            ImageryArtifact.geometry_version_id == geometry.id,
            ImageryArtifact.artifact_type == artifact_type,
            ImageryArtifact.processor_version == "sentinel-preprocessor-v1",
        ))
        if existing is not None:
            continue
        session.add(ImageryArtifact(
            claim=claim, geometry_version=geometry, imagery_scene=scene,
            artifact_type=artifact_type, target_year=2025,
            content_sha256=hashlib.sha256(scene_id.encode("utf-8")).hexdigest(),
            processor_version="sentinel-preprocessor-v1",
            parameters_json={"collection": "sentinel-2-l2a", "band_keys": ["green", "nir"], "max_cloud": 20},
            statistics_json={
                "width": 512, "height": 512, "crs": "EPSG:4326",
                "band_keys": ["green", "nir"], "valid_pixel_percent": round(91 + index % 8, 1),
                "observation_coverage": .95, "water_source_present": True,
            },
            quality_flags_json=[],
            provenance_json={"synthetic": True, "source": "prepared imagery register", "legal_role": "supporting_observation"},
            state="completed", verification_state="verified", reviewed_by=actor_id,
            reviewed_at=datetime.now(timezone.utc),
            synthetic=True,
        ))
    session.flush()


def _seed_planning(session, claims, actor_id) -> None:
    catalogs = {}
    for item in _load(CATALOG_PATH):
        existing_catalog = session.scalar(select(SchemeCatalogEntry).where(
            SchemeCatalogEntry.scheme_code == item["scheme_code"],
            SchemeCatalogEntry.version == item["version"],
        ))
        if existing_catalog is None:
            existing_catalog = create_catalog_entry(
                session, item, actor_id=actor_id, request_id="tn-sample-seed"
            )
        catalogs[item["scheme_code"]] = existing_catalog
    seeded_rules = []
    for item in _load(RULES_PATH):
        rule = session.scalar(
            select(SchemeRuleSet).where(
                SchemeRuleSet.scheme_code == item["scheme_code"],
                SchemeRuleSet.version == item["version"],
            )
        )
        if rule is None:
            catalog = catalogs[item["scheme_code"]]
            rule = SchemeRuleSet(created_by=actor_id, catalog_entry_id=catalog.id,
                scheme_code=item["scheme_code"], display_name=item["display_name"],
                version=item["version"], required_facts_json=item["required_facts"], condition_json=item["condition"],
                required_evidence_json=item.get("required_evidence", []),
                required_assets_json=item.get("required_assets", []),
                exclusion_condition_json=item.get("exclusion_condition"),
                priority_conditions_json=item.get("priority_conditions", []),
                freshness_requirements_json=item.get("freshness_requirements", {}),
                recommendation_logic_json=item.get("recommendation_logic", {}),
                recommendation_text=item["recommendation_text"], source_reference=item["source_reference"],
                active=bool(catalog.active and catalog.authoritative))
            session.add(rule)
        seeded_rules.append(rule)
    session.flush()
    seeded_rule_ids = {rule.id for rule in seeded_rules if rule.active}
    snapshots = []
    for claim in claims:
        snapshot = derive_facts(session, claim, CURRENT_FACT_VERSION, actor_id,
                                f"tn-sample-{CURRENT_FACT_VERSION}-{claim.id}")
        snapshots.append(snapshot)
        if seeded_rule_ids:
            evaluate_rules(session, claim_id=claim.id, facts=fact_values(snapshot), actor_id=actor_id,
                idempotency_key=f"tn-sample-4-evaluation-{claim.id}", rule_set_ids=seeded_rule_ids,
                fact_snapshot_id=snapshot.id, fact_sources=snapshot.sources_json)
    applicable = {
        "IFR": ("PM-KISAN", "PMAY-G", "MGNREGA", "JJM", "DAJGUA"),
        "CR": ("MGNREGA", "JJM", "DAJGUA"),
        "CFR": ("MGNREGA", "JJM", "DAJGUA"),
    }
    rules_by_code = {rule.scheme_code: rule for rule in seeded_rules}
    outcomes = ("recommended", "not_recommended", "insufficient_data")
    for index, (claim, snapshot) in enumerate(zip(claims, snapshots)):
        codes = applicable[claim.right_type]
        rule = rules_by_code[codes[index % len(codes)]]
        key = f"tn-education-recommendation-{claim.id}-{rule.id}"
        existing = session.scalar(select(DSSRecommendation).where(
            DSSRecommendation.actor_id == actor_id,
            DSSRecommendation.rule_set_id == rule.id,
            DSSRecommendation.idempotency_key == key,
        ))
        if existing is not None:
            continue
        outcome = outcomes[index % len(outcomes)]
        required = list(rule.required_evidence_json or [])
        missing = required[-1:] if outcome == "insufficient_data" else []
        logic = dict(rule.recommendation_logic_json or {})
        session.add(DSSRecommendation(
            claim=claim, rule_set=rule, rule_version=rule.version, actor_id=actor_id,
            idempotency_key=key, outcome=outcome,
            input_json={
                "facts": fact_values(snapshot), "fact_snapshot_id": str(snapshot.id),
                "fact_sources": dict(snapshot.sources_json or {}),
            },
            output_json={
                "scheme_code": rule.scheme_code, "scheme_name": rule.display_name,
                "rule_version": rule.version,
                "catalog_entry_id": str(rule.catalog_entry_id) if rule.catalog_entry_id else None,
                "catalog_version": rule.catalog_entry.version if rule.catalog_entry else None,
                "outcome": outcome,
                "reasons": ["Planning scenario derived from the current village and FRA record."],
                "missing_inputs": missing, "required_evidence": required,
                "required_assets": list(rule.required_assets_json or []), "unmet_assets": [],
                "freshness_requirements": dict(rule.freshness_requirements_json or {}),
                "priority": ("high" if index % 4 == 0 else "normal"),
                "priority_reasons": (["The selected village has a recorded infrastructure or livelihood gap."] if index % 4 == 0 else []),
                "priority_missing_inputs": [],
                "recommendation": logic.get(outcome) or rule.recommendation_text,
                "source_reference": rule.source_reference, "advisory_only": True,
                "disclaimer": DISCLAIMER,
            },
        ))
    session.flush()
    seeded_claim_ids = [claim.id for claim in claims]
    recommendations = list(session.scalars(
        select(DSSRecommendation)
        .where(
            DSSRecommendation.claim_id.in_(seeded_claim_ids),
            DSSRecommendation.rule_version == "tn-sample-4",
            DSSRecommendation.outcome == "recommended",
        )
        .order_by(DSSRecommendation.created_at)
    ))
    for recommendation in recommendations:
        catalog = recommendation.rule_set.catalog_entry
        create_referral(
            session, recommendation_id=recommendation.id,
            department=(catalog.department if catalog else "District Rural Development Agency"),
            priority=str((recommendation.output_json or {}).get("priority") or "normal"),
            actor_id=actor_id,
            idempotency_key=f"tn-education-referral-{recommendation.id}",
            notes="Forwarded for departmental review based on the recorded FRA and village evidence.",
        )


def seed_demo(session, *, actor_id) -> SeedReport:
    actor = session.get(User, actor_id)
    if actor is None or actor.role != "admin":
        raise PermissionError("The Tamil Nadu sample-data seed requires an administrator.")
    _refresh_legacy_visible_values(session)
    before = _count(session)
    import_village_profiles(session, _educational_atlas_payload(), actor_id=actor_id)
    records = _seed_archive(session, actor_id)
    claims = _seed_claim_details(session, records, actor_id)
    villages = list(session.scalars(select(FRAVillageProfile).order_by(FRAVillageProfile.village_code)))
    _seed_imagery(session, claims, actor_id)
    _seed_assets(session, villages, actor_id)
    refresh_village_asset_profiles(session, actor_id=actor_id, request_id="tn-sample-seed")
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
