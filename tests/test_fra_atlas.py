import json
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_completion_models import FRAVillageProfile
from app.db.fra_models import FRAClaim, FRAGeometryVersion, FRATitle, GramSabha, RightsHolder
from app.db.fra_operational_models import (
    ImageryArtifact,
    ImagerySceneRecord,
    SpatialImportBatch,
    SpatialReferenceFeature,
)
from app.db.models import User
from app.services.fra_atlas import (
    AtlasFilters,
    AtlasValidationError,
    atlas_features,
    atlas_summary,
    import_village_profiles,
)


def atlas_payload():
    return {
        "type": "FeatureCollection",
        "metadata": {
            "state_code": "TN",
            "state_name": "Tamil Nadu",
            "synthetic": True,
            "source": "Synthetic final-year project boundary pack",
            "version": "demo-v1",
        },
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[79.10, 10.70], [79.12, 10.70], [79.12, 10.72], [79.10, 10.72], [79.10, 10.70]]
                    ],
                },
                "properties": {
                    "district_code": "TN-13",
                    "district_name": "Thanjavur",
                    "block_code": "TN-13-01",
                    "block_name": "Kumbakonam",
                    "village_code": "TN-13-01-001",
                    "village_name": "Kottur",
                    "tribal_groups": ["Synthetic Irular community"],
                    "socioeconomic": {"water_access": "demo_unknown"},
                },
            },
            {
                "type": "Feature",
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": [
                        [[[79.13, 10.70], [79.15, 10.70], [79.15, 10.72], [79.13, 10.72], [79.13, 10.70]]]
                    ],
                },
                "properties": {
                    "district_code": "TN-13",
                    "district_name": "Thanjavur",
                    "block_code": "TN-13-01",
                    "block_name": "Kumbakonam",
                    "village_code": "TN-13-01-002",
                    "village_name": "Maruthur Demo",
                    "tribal_groups": [],
                    "socioeconomic": {},
                },
            },
        ],
    }


class FRAAtlasTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def _admin(self, session):
        admin = User(external_id=f"atlas-admin-{uuid.uuid4()}", display_name="Admin", role="admin")
        session.add(admin)
        session.flush()
        return admin

    def test_imported_tamil_nadu_villages_keep_synthetic_provenance(self):
        with Session(self.engine) as session:
            admin = self._admin(session)
            report = import_village_profiles(session, atlas_payload(), actor_id=admin.id)
            session.commit()
            village = session.scalar(
                select(FRAVillageProfile).order_by(FRAVillageProfile.village_code)
            )

            self.assertEqual(report.inserted, 2)
            self.assertEqual(report.updated, 0)
            self.assertEqual(village.state_code, "TN")
            self.assertTrue(village.provenance_json["synthetic"])
            self.assertTrue(village.synthetic)
            self.assertEqual(village.boundary["type"], "MultiPolygon")

    def test_village_import_is_idempotent(self):
        with Session(self.engine) as session:
            admin = self._admin(session)
            first = import_village_profiles(session, atlas_payload(), actor_id=admin.id)
            second = import_village_profiles(session, atlas_payload(), actor_id=admin.id)

            self.assertEqual((first.inserted, first.updated), (2, 0))
            self.assertEqual((second.inserted, second.updated), (0, 0))

    def test_import_rejects_unprovenanced_authoritative_or_unsupported_reference_data(self):
        with Session(self.engine) as session:
            admin = self._admin(session)
            payload = atlas_payload()
            payload["metadata"]["synthetic"] = False
            with self.assertRaisesRegex(AtlasValidationError, "source_authority"):
                import_village_profiles(session, payload, actor_id=admin.id)
            payload = atlas_payload()
            payload["metadata"]["state_code"] = "OD"
            with self.assertRaisesRegex(AtlasValidationError, "Tamil Nadu"):
                import_village_profiles(session, payload, actor_id=admin.id)

    def test_imports_real_soi_village_boundary_with_authoritative_provenance(self):
        with Session(self.engine) as session:
            admin = self._admin(session)
            payload = json.loads(
                (Path(__file__).parents[1] / "data" / "real" / "arpisampalaiyam_village.geojson")
                .read_text(encoding="utf-8")
            )
            report = import_village_profiles(session, payload, actor_id=admin.id)
            village = session.scalar(select(FRAVillageProfile))

            self.assertEqual(report.inserted, 1)
            self.assertFalse(village.synthetic)
            self.assertEqual(village.village_code, "632998")
            self.assertEqual(village.provenance_json["classification"], "published_authoritative_reference")
            self.assertEqual(village.provenance_json["source_authority"], "Survey of India (SOI)")
            self.assertIn("nwdp.nwic.gov.in", village.provenance_json["source_reference"])
            self.assertIn("copyright-policy", village.provenance_json["license_reference"])

    def test_atlas_filters_and_summary_use_same_scope_without_private_ids(self):
        with Session(self.engine) as session:
            admin = self._admin(session)
            import_village_profiles(session, atlas_payload(), actor_id=admin.id)
            village = session.scalar(
                select(FRAVillageProfile).where(FRAVillageProfile.village_name == "Kottur")
            )
            gram_sabha = GramSabha(
                name="Kottur Gram Sabha",
                village="Kottur",
                block="Kumbakonam",
                district="Thanjavur",
                state="Tamil Nadu",
            )
            holder = RightsHolder(
                display_name="Synthetic Ramu",
                holder_type="individual",
                claimant_category="ST",
                gram_sabha=gram_sabha,
            )
            claim = FRAClaim(
                claim_number="TN-ATLAS-IFR-1",
                right_type="IFR",
                status="granted",
                rights_holder=holder,
                gram_sabha=gram_sabha,
                submitted_by=admin.id,
                claimed_area_sqm=1200,
                provenance_json={"synthetic": True},
            )
            session.add(claim)
            session.flush()
            session.add(
                FRAGeometryVersion(
                    claim=claim,
                    version=1,
                    geometry=village.boundary,
                    source="synthetic_demo",
                    provenance_json={"synthetic": True},
                    boundary_quality="synthetic",
                    created_by=admin.id,
                )
            )
            session.flush()
            filters = AtlasFilters(
                district="Thanjavur", right_type="IFR", status="granted"
            )
            features = atlas_features(session, filters, privileged=False)
            summary = atlas_summary(session, filters)

            claim_features = [
                feature for feature in features["features"] if feature["properties"]["kind"] == "claim"
            ]
            self.assertEqual(summary.claim_count, len(claim_features))
            self.assertEqual(summary.claim_count, 1)
            self.assertNotIn("rights_holder_id", json.dumps(features))
            owner_features = atlas_features(
                session, filters, privileged=False, actor_id=admin.id
            )
            self.assertIn("claim_id", json.dumps(owner_features))
            self.assertNotIn("rights_holder_id", json.dumps(owner_features))
            privileged = atlas_features(session, filters, privileged=True)
            self.assertIn("rights_holder_id", json.dumps(privileged))

            category_scope = atlas_features(
                session,
                AtlasFilters(
                    claimant_category="ST", min_area_sqm=1000, max_area_sqm=1500,
                    layers=("claim",),
                ),
                privileged=False,
            )
            self.assertEqual(len(category_scope["features"]), 1)
            outside_area = atlas_features(
                session, AtlasFilters(min_area_sqm=2000, layers=("claim",)), privileged=False,
            )
            self.assertEqual(outside_area["features"], [])

    def test_atlas_includes_published_administrative_and_supporting_layers(self):
        with Session(self.engine) as session:
            admin = self._admin(session)
            import_village_profiles(session, atlas_payload(), actor_id=admin.id)
            boundary = atlas_payload()["features"][0]["geometry"]
            batch = SpatialImportBatch(
                dataset_kind="administrative_boundary", source_authority="Tamil Nadu reference",
                source_version="2025", state="published", record_count=2, valid_count=2,
                provenance_json={"source": "reviewed import"}, synthetic=True,
                created_by=admin.id, reviewed_by=admin.id, idempotency_key="atlas-reference",
            )
            session.add(batch); session.flush()
            session.add_all([
                SpatialReferenceFeature(
                    import_batch=batch, dataset_kind="administrative_boundary",
                    source_authority=batch.source_authority, source_version=batch.source_version,
                    source_record_id="district-13", geometry={"type": "MultiPolygon", "coordinates": [boundary["coordinates"]]},
                    properties_json={"admin_level": "district", "district": "Thanjavur", "name": "Thanjavur"},
                    provenance_json={"source": "reviewed import"}, published=True, synthetic=True,
                ),
                SpatialReferenceFeature(
                    import_batch=batch, dataset_kind="protected_area",
                    source_authority=batch.source_authority, source_version=batch.source_version,
                    source_record_id="pa-1", geometry={"type": "MultiPolygon", "coordinates": [boundary["coordinates"]]},
                    properties_json={"district": "Thanjavur", "name": "Protected reference"},
                    provenance_json={"source": "reviewed import"}, published=True, synthetic=True,
                ),
                SpatialReferenceFeature(
                    import_batch=batch, dataset_kind="groundwater_stress",
                    source_authority=batch.source_authority, source_version=batch.source_version,
                    source_record_id="gw-1", geometry={"type": "MultiPolygon", "coordinates": [boundary["coordinates"]]},
                    properties_json={"district": "Thanjavur", "name": "Groundwater stress reference"},
                    provenance_json={"source": "reviewed import"}, published=True, synthetic=True,
                ),
            ])
            session.flush()

            collection = atlas_features(
                session,
                AtlasFilters(
                    district="Thanjavur", layers=("district", "protected_area", "groundwater_stress"),
                ),
                privileged=False,
            )
            self.assertEqual(
                {(item["properties"]["kind"], item["properties"]["layer"]) for item in collection["features"]},
                {
                    ("administrative", "district"),
                    ("reference", "protected_area"),
                    ("reference", "groundwater_stress"),
                },
            )
            self.assertNotIn("private", json.dumps(collection).casefold())

    def test_atlas_consumes_current_and_historical_imagery_without_private_locations(self):
        with Session(self.engine) as session:
            admin = self._admin(session)
            import_village_profiles(session, atlas_payload(), actor_id=admin.id)
            village = session.scalar(
                select(FRAVillageProfile).where(
                    FRAVillageProfile.village_name == "Kottur"
                )
            )
            holder = RightsHolder(
                display_name="Imagery holder",
                holder_type="individual",
                claimant_category="ST",
            )
            claim = FRAClaim(
                claim_number="TN-IMAGERY-IFR-1",
                right_type="IFR",
                status="submitted",
                rights_holder=holder,
                village=village,
                submitted_by=admin.id,
                claimed_area_sqm=1200,
                provenance_json={"source": "imagery test"},
            )
            geometry = FRAGeometryVersion(
                claim=claim,
                version=1,
                geometry=village.boundary,
                source="reviewed_boundary",
                provenance_json={},
                boundary_quality="reviewed",
                created_by=admin.id,
            )
            current_scene = ImagerySceneRecord(
                provider="Earth Search",
                collection="sentinel-2-l2a",
                scene_id="S2-CURRENT",
                acquired_at=datetime(2025, 3, 3, tzinfo=timezone.utc),
                footprint=village.boundary,
                cloud_cover=8,
                asset_references_json={
                    "green": "https://private.example/current.tif"
                },
                license_reference="sentinel-license",
                status="ingested",
                provenance_json={},
            )
            historical_scene = ImagerySceneRecord(
                provider="Earth Search",
                collection="landsat-c2-l2",
                scene_id="LANDSAT-2005",
                acquired_at=datetime(2005, 2, 1, tzinfo=timezone.utc),
                footprint=village.boundary,
                cloud_cover=12,
                asset_references_json={
                    "red": "https://private.example/history.tif"
                },
                license_reference="landsat-license",
                status="ingested",
                provenance_json={},
            )
            session.add_all([claim, geometry, current_scene, historical_scene])
            session.flush()
            current = ImageryArtifact(
                claim=claim,
                geometry_version=geometry,
                imagery_scene=current_scene,
                artifact_type="analysis_ready_raster:2025-01-01:2025-03-31",
                target_year=2025,
                storage_key="private/current.tif",
                content_sha256="a" * 64,
                processor_version="cog-v1",
                parameters_json={},
                statistics_json={"valid_pixel_percent": 96.5},
                quality_flags_json=[],
                provenance_json={"legal_role": "supporting_observation"},
                state="completed",
            )
            historical = ImageryArtifact(
                claim=claim,
                geometry_version=geometry,
                imagery_scene=historical_scene,
                artifact_type="historical_land_observation:2005",
                target_year=2005,
                storage_key="private/history.json",
                content_sha256="b" * 64,
                processor_version="history-v1",
                parameters_json={},
                statistics_json={"forest_index": 0.6},
                quality_flags_json=["cloud_screened"],
                provenance_json={"legal_role": "supporting_observation"},
                state="completed",
            )
            session.add_all([current, historical])
            session.flush()

            public = atlas_features(
                session,
                AtlasFilters(
                    year=2005,
                    layers=("satellite_imagery", "historical_imagery"),
                ),
                privileged=False,
            )
            self.assertEqual(len(public["features"]), 1)
            feature = public["features"][0]
            self.assertEqual(feature["properties"]["layer"], "historical_imagery")
            self.assertEqual(
                feature["properties"]["coverage_type"], "claim_analysis_extent"
            )
            self.assertEqual(feature["geometry"], village.boundary)
            self.assertNotIn("artifact_id", feature["properties"])
            self.assertNotIn("claim_id", feature["properties"])
            self.assertNotIn("storage_key", json.dumps(public))
            self.assertNotIn("private.example", json.dumps(public))
            self.assertEqual(
                public["metadata"]["imagery_rendering"], "coverage_index"
            )
            self.assertEqual(
                public["metadata"]["layer_counts"], {"historical_imagery": 1}
            )

            owner = atlas_features(
                session,
                AtlasFilters(layers=("satellite_imagery",)),
                privileged=False,
                actor_id=admin.id,
            )
            self.assertEqual(
                owner["features"][0]["properties"]["claim_id"], str(claim.id)
            )
            self.assertEqual(
                owner["features"][0]["properties"]["artifact_id"], str(current.id)
            )

    def test_atlas_statistics_cover_unmapped_claims_areas_and_each_hierarchy_level(self):
        with Session(self.engine) as session:
            admin = self._admin(session)
            import_village_profiles(session, atlas_payload(), actor_id=admin.id)
            village = session.scalar(
                select(FRAVillageProfile).where(FRAVillageProfile.village_name == "Kottur")
            )
            claims = []
            for number, right_type, status, area in (
                ("TN-STATS-IFR", "IFR", "granted", 1000),
                ("TN-STATS-CR", "CR", "rejected", 2000),
                ("TN-STATS-CFR", "CFR", "remanded", 3000),
            ):
                holder = RightsHolder(
                    display_name=f"{right_type} holder",
                    holder_type="individual" if right_type == "IFR" else "community",
                )
                claim = FRAClaim(
                    claim_number=number, right_type=right_type, status=status,
                    rights_holder=holder, village=village, submitted_by=admin.id,
                    claimed_area_sqm=area, provenance_json={"source": "statistics test"},
                )
                session.add(claim)
                claims.append(claim)
            session.flush()
            session.add(FRATitle(
                claim=claims[0], version=1, title_number="TN-STATS-TITLE",
                granted_area_sqm=800, active=True, issued_by=admin.id,
            ))
            session.flush()

            summary = atlas_summary(session, AtlasFilters(layers=("village",)))

            self.assertEqual(summary.claim_count, 3)
            self.assertEqual(summary.by_right_type, {"CFR": 1, "CR": 1, "IFR": 1})
            self.assertEqual(summary.granted_claim_count, 1)
            self.assertEqual(summary.rejected_claim_count, 1)
            self.assertEqual(summary.pending_claim_count, 1)
            self.assertEqual(summary.claimed_area_sqm, 6000)
            self.assertEqual(summary.granted_area_sqm, 800)
            self.assertEqual(set(summary.progress), {"state", "district", "block", "village"})
            for level in summary.progress:
                self.assertEqual(len(summary.progress[level]), 1)
                self.assertEqual(summary.progress[level][0]["total_claims"], 3)
                self.assertEqual(summary.progress[level][0]["granted_claims"], 1)
            self.assertEqual(summary.progress["village"][0]["village"], "Kottur")


if __name__ == "__main__":
    unittest.main()
