import unittest

from shapely.geometry import shape
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_completion_models import (
    AssetFeature,
    FRAArchiveRecord,
    FRAVillageProfile,
    VillageAssetProfile,
)
from app.db.fra_models import DSSRecommendation, FRAClaim, SchemeRuleSet
from app.db.fra_operational_models import ImagerySceneRecord, SchemeCatalogEntry
from app.db.models import User
from app.services.fra_atlas import AtlasFilters, atlas_features
from scripts.seed_tamil_nadu_fra_pilot import seed_pilot


class TamilNaduPilotTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_pilot_runs_full_fra_flow_with_real_context_and_honest_model_gap(self):
        with Session(self.engine) as session:
            admin = User(external_id="pilot-admin", role="admin")
            session.add(admin)
            session.commit()

            first = seed_pilot(session, actor_id=admin.id)
            session.commit()
            second = seed_pilot(session, actor_id=admin.id)
            session.commit()

            self.assertGreater(first.created, 0)
            self.assertEqual(second.created, 0)
            self.assertEqual(first.district, "Villupuram")
            self.assertEqual(first.village, "Arpisampalaiyam")

            expected_demo = [
                (1, "upload_import_legacy_fra_documents", "complete_with_synthetic_fixture"),
                (2, "ocr_documents", "complete_with_synthetic_fixture"),
                (3, "extract_fra_entities", "complete_with_synthetic_fixture"),
                (4, "review_extracted_information", "complete"),
                (5, "create_normalize_fra_records", "complete_with_synthetic_fixture"),
                (6, "link_records_to_villages", "complete_with_synthetic_fixture"),
                (7, "display_on_fra_atlas", "complete"),
                (8, "display_claim_title_status", "complete"),
                (9, "load_satellite_imagery", "complete"),
                (10, "run_ai_asset_detection", "awaiting_user_model"),
                (11, "display_mapped_assets", "awaiting_user_model"),
                (12, "generate_village_asset_profile", "complete_with_observation_gap"),
                (13, "generate_dss_facts", "complete"),
                (14, "run_scheme_rules", "complete"),
                (15, "show_recommended_interventions", "insufficient_data"),
                (16, "show_spatial_evidence_reasoning", "complete"),
                (17, "generate_report", "complete"),
            ]
            self.assertEqual(
                [(stage.step, stage.key, stage.status) for stage in second.stages],
                expected_demo,
            )
            self.assertEqual(second.report_url, f"/api/fra/reports/villages/{second.village_id}")
            self.assertEqual(len(second.report_sha256), 64)

            village = session.scalar(
                select(FRAVillageProfile).where(
                    FRAVillageProfile.village_code == "632998"
                )
            )
            self.assertIsNotNone(village)
            self.assertFalse(village.synthetic)
            self.assertEqual(
                village.provenance_json["classification"],
                "published_authoritative_reference",
            )

            record = session.scalar(
                select(FRAArchiveRecord).where(
                    FRAArchiveRecord.legacy_reference == "TN-PILOT-CFR-SYNTHETIC-001"
                )
            )
            self.assertTrue(record.synthetic)
            self.assertEqual(record.review_state, "promoted")
            self.assertEqual(
                record.latest_extraction.provenance_json["adapter"],
                "manifest",
            )

            claim = record.promoted_claim
            self.assertEqual(claim.right_type, "CFR")
            self.assertEqual(claim.village_id, village.id)
            self.assertTrue(claim.provenance_json["synthetic"])
            geometry = claim.geometry_versions[-1]
            self.assertTrue(geometry.provenance_json["synthetic"])
            self.assertTrue(shape(village.boundary).covers(shape(geometry.geometry)))

            atlas = atlas_features(
                session,
                AtlasFilters(
                    state="Tamil Nadu",
                    district="Villupuram",
                    village="Arpisampalaiyam",
                ),
                privileged=True,
                actor_id=admin.id,
            )
            feature_types = {item["properties"]["kind"] for item in atlas["features"]}
            self.assertIn("village", feature_types)
            self.assertIn("claim", feature_types)

            scene = session.scalar(select(ImagerySceneRecord))
            self.assertFalse(scene.synthetic)
            self.assertEqual(scene.scene_id, "S2A_44PLU_20260605_0_L2A")
            self.assertEqual(scene.asset_references_json, {})
            self.assertTrue(shape(scene.footprint).intersects(shape(village.boundary)))

            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(AssetFeature).where(
                        (AssetFeature.claim_id == claim.id)
                        | (AssetFeature.village_id == village.id)
                    )
                ),
                0,
            )
            asset_profile = session.scalar(
                select(VillageAssetProfile).where(
                    VillageAssetProfile.village_id == village.id
                )
            )
            self.assertEqual(asset_profile.verified_asset_count, 0)
            self.assertIn(
                "no_verified_satellite_assets",
                asset_profile.metrics_json["asset_deficiency_indicators"],
            )

            recommendation = session.scalar(
                select(DSSRecommendation).where(DSSRecommendation.claim_id == claim.id)
            )
            self.assertEqual(recommendation.outcome, "insufficient_data")
            self.assertIn("water_source_present", recommendation.output_json["missing_inputs"])
            self.assertTrue(recommendation.output_json["advisory_only"])

            rule = session.get(SchemeRuleSet, recommendation.rule_set_id)
            catalog = session.get(SchemeCatalogEntry, rule.catalog_entry_id)
            self.assertEqual(rule.scheme_code, "TN-PILOT-JJM-READINESS")
            self.assertTrue(rule.active)
            self.assertTrue(catalog.active)
            self.assertTrue(catalog.authoritative)
            self.assertTrue(catalog.definition_json["pilot_only"])

    def test_pilot_requires_an_administrator(self):
        with Session(self.engine) as session:
            user = User(external_id="pilot-user", role="user")
            session.add(user)
            session.commit()
            with self.assertRaises(PermissionError):
                seed_pilot(session, actor_id=user.id)


if __name__ == "__main__":
    unittest.main()
