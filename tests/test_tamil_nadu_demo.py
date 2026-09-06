import unittest

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_completion_models import AssetFeature, DSSReferral, FRAArchiveRecord, FRAVillageProfile
from app.db.fra_models import (
    DSSRecommendation,
    FRAClaim,
    FRATitle,
    RightsHolder,
    SchemeRuleSet,
)
from app.db.models import User
from app.db.fra_operational_models import SchemeCatalogEntry
from app.services.fra_archive import create_archive_record, create_import_batch
from scripts.seed_tamil_nadu_fra_demo import _document, seed_demo


class TamilNaduSampleDataTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_seed_is_idempotent_coherent_complete_and_visibly_synthetic(self):
        with Session(self.engine) as session:
            admin = User(external_id="sample-admin", display_name="Sample Administrator", role="admin")
            session.add(admin); session.commit()
            first = seed_demo(session, actor_id=admin.id)
            session.commit()
            second = seed_demo(session, actor_id=admin.id)
            session.commit()

            self.assertGreater(first.created, 0)
            self.assertEqual(second.created, 0)
            self.assertEqual(session.scalar(select(func.count()).select_from(FRAVillageProfile)), 3)
            records = list(session.scalars(select(FRAArchiveRecord)))
            self.assertEqual({row.right_type for row in records}, {"IFR", "CR", "CFR"})
            self.assertTrue(all(row.synthetic and row.state_code == "TN" for row in records))
            self.assertGreaterEqual(session.scalar(select(func.count()).select_from(FRAClaim)), 3)
            self.assertGreaterEqual(session.scalar(select(func.count()).select_from(FRATitle)), 1)
            self.assertGreaterEqual(session.scalar(select(func.count()).select_from(AssetFeature)), 2)
            water = session.scalar(select(AssetFeature).where(AssetFeature.source_reference == "tn-sample-scene-2005"))
            agriculture = session.scalar(select(AssetFeature).where(AssetFeature.source_reference == "tn-sample-scene-2025"))
            self.assertEqual(water.village.village_name, "Kottur")
            self.assertEqual(agriculture.village.village_name, "Kottur")
            self.assertEqual(session.scalar(select(func.count()).select_from(DSSRecommendation)), 0)
            self.assertEqual(session.scalar(select(func.count()).select_from(SchemeCatalogEntry)), 5)
            self.assertTrue(all(not row.authoritative and not row.active for row in session.scalars(select(SchemeCatalogEntry))))
            visible_archive_values = [
                value
                for row in records
                for value in (
                    row.legacy_reference, row.claim_number, row.holder_display_name,
                    row.district, row.block, row.village, row.right_type,
                    row.claim_status, row.claim_year,
                )
            ]
            self.assertTrue(all(value not in (None, "") for value in visible_archive_values))
            self.assertNotIn("demo", " ".join(map(str, visible_archive_values)).casefold())
            villages = list(session.scalars(select(FRAVillageProfile)))
            self.assertTrue(all(row.tribal_groups_json and row.socioeconomic_json for row in villages))
            self.assertNotIn("demo", " ".join(row.village_name for row in villages).casefold())
            self.assertTrue(all(
                session.scalar(
                    select(func.count()).select_from(AssetFeature).where(AssetFeature.village_id == row.id)
                )
                for row in villages
            ))
            rules = list(session.scalars(select(SchemeRuleSet)))
            self.assertTrue(all(not row.active and row.catalog_entry_id for row in rules))
            self.assertTrue(all(
                row.scheme_code and row.display_name and row.version
                and row.recommendation_text and row.source_reference
                for row in rules
            ))
            self.assertNotIn(
                "demo",
                " ".join(
                    value
                    for row in rules
                    for value in (row.scheme_code, row.display_name, row.version, row.source_reference)
                ).casefold(),
            )
            self.assertEqual(list(session.scalars(select(DSSRecommendation))), [])

    def test_seed_retains_reviewed_measurements_and_versioned_history(self):
        from copy import deepcopy
        from app.db.fra_operational_models import DSSFactSnapshot
        with Session(self.engine) as session:
            admin = User(external_id="history-admin", role="admin")
            session.add(admin); session.commit()
            seed_demo(session, actor_id=admin.id); session.commit()
            asset = session.scalar(select(AssetFeature).where(AssetFeature.asset_class == "water_body"))
            asset.observed_value_json = {"present": False, "field_note": "Verified absence"}
            asset.verification_reasons_json = ["Reviewer field visit"]
            record = session.scalar(select(FRAArchiveRecord).where(FRAArchiveRecord.right_type == "IFR"))
            record.latest_extraction.raw_text = "Original historical transcription"
            record.latest_extraction.provenance_json = {"sentinel": "original source"}
            old_rule = SchemeRuleSet(scheme_code="TN-FRA-WATER-SUPPORT", display_name="Old water rule",
                version="tn-sample-1", required_facts_json=["has_title"], condition_json={"present": {"fact": "has_title"}},
                recommendation_text="Old advice", source_reference="synthetic://water-support/v1", created_by=admin.id)
            session.add(old_rule); session.flush()
            old_rec = DSSRecommendation(claim_id=record.promoted_claim_id, rule_set=old_rule, rule_version="tn-sample-1",
                actor_id=admin.id, idempotency_key="old-rec", outcome="recommended",
                input_json={"facts": {"has_title": True}}, output_json={"sentinel": "original recommendation"})
            old_snapshot = DSSFactSnapshot(claim_id=record.promoted_claim_id, derivation_version="tn-facts-v1",
                idempotency_key="old-snapshot", facts_json={"old": {"value": True}}, sources_json={"sentinel": "old"}, created_by=admin.id)
            session.add_all([old_rec, old_snapshot]); session.commit()
            before = deepcopy((asset.observed_value_json, asset.verification_reasons_json,
                record.latest_extraction.raw_text, record.latest_extraction.provenance_json,
                old_rule.condition_json, old_rec.input_json, old_rec.output_json, old_snapshot.facts_json, old_snapshot.sources_json))
            report = seed_demo(session, actor_id=admin.id); session.commit()
            self.assertEqual(report.created, 0)
            self.assertEqual(before, (asset.observed_value_json, asset.verification_reasons_json,
                record.latest_extraction.raw_text, record.latest_extraction.provenance_json,
                old_rule.condition_json, old_rec.input_json, old_rec.output_json, old_snapshot.facts_json, old_snapshot.sources_json))
            current = list(session.scalars(select(DSSRecommendation).where(DSSRecommendation.rule_version == "tn-sample-4")))
            self.assertEqual(current, [])
            candidate_rules = list(session.scalars(select(SchemeRuleSet).where(
                SchemeRuleSet.version == "tn-sample-4"
            )))
            self.assertTrue(all(not row.active and row.catalog_entry_id for row in candidate_rules))

    def test_seed_preserves_coexisting_legacy_rules_and_assets(self):
        with Session(self.engine) as session:
            if self.engine.dialect.name == "sqlite":
                session.execute(text("PRAGMA foreign_keys=ON"))
            admin = User(external_id="coexist-admin", role="admin")
            session.add(admin); session.commit()
            seed_demo(session, actor_id=admin.id); session.commit()
            water = session.scalar(select(AssetFeature).where(AssetFeature.asset_class == "water_body"))
            legacy_asset = AssetFeature(village_id=water.village_id, asset_class="water_body",
                observed_value_json={"present": False}, source_type="synthetic_manifest", source_reference="tn-demo-scene-2005",
                provenance_json={"synthetic": True}, verification_state="verified", synthetic=True)
            legacy_rule = SchemeRuleSet(scheme_code="DEMO-WATER-SUPPORT", display_name="Original rule", version="demo-1",
                required_facts_json=["has_title"], condition_json={"present": {"fact": "has_title"}},
                recommendation_text="Original advice", source_reference="demo://water/v1", created_by=admin.id)
            session.add_all([legacy_asset, legacy_rule]); session.commit()
            counts = {model: session.scalar(select(func.count()).select_from(model)) for model in (AssetFeature, SchemeRuleSet)}
            seed_demo(session, actor_id=admin.id); session.commit()
            self.assertEqual(counts, {model: session.scalar(select(func.count()).select_from(model)) for model in counts})
            self.assertEqual(legacy_asset.observed_value_json, {"present": False})
            self.assertEqual(legacy_asset.source_reference, "tn-demo-scene-2005")
            self.assertEqual(legacy_rule.version, "demo-1")
            self.assertEqual(legacy_rule.recommendation_text, "Original advice")

    def test_seed_rerun_preserves_subsequent_human_lifecycle_decisions(self):
        from app.services.fra_workflow import transition_claim
        with Session(self.engine) as session:
            admin = User(external_id="lifecycle-admin", role="admin")
            session.add(admin); session.commit()
            seed_demo(session, actor_id=admin.id); session.commit()
            claim = session.scalar(select(FRAClaim).where(FRAClaim.right_type == "CR"))
            transition_claim(session, claim, target_status="gram_sabha_verified", authority_level="Gram Sabha",
                             outcome="reviewed", reasons=["Human source review"], actor_id=admin.id, request_id="human-review")
            session.commit()
            decisions = [(row.id, row.from_status, row.to_status, row.reasons_json) for row in claim.decisions]
            seed_demo(session, actor_id=admin.id); session.commit()
            self.assertEqual(claim.status, "gram_sabha_verified")
            self.assertEqual(decisions, [(row.id, row.from_status, row.to_status, row.reasons_json) for row in claim.decisions])

    def test_seed_does_not_evaluate_or_refer_unrelated_rules(self):
        with Session(self.engine) as session:
            admin = User(external_id="sample-referral-admin", display_name="Sample Administrator", role="admin")
            session.add(admin); session.flush()
            unrelated_holder = RightsHolder(
                display_name="Unrelated holder",
                holder_type="individual",
            )
            unrelated_claim = FRAClaim(
                claim_number="TN-UNRELATED-PRESEED",
                right_type="IFR",
                status="granted",
                rights_holder=unrelated_holder,
                submitted_by=admin.id,
                provenance_json={"source": "unrelated"},
            )
            unrelated_rule = SchemeRuleSet(
                scheme_code="TN-UNRELATED-ACTIVE",
                display_name="Unrelated active rule",
                version="1",
                required_facts_json=["has_title"],
                condition_json={"present": {"fact": "has_title"}},
                recommendation_text="Unrelated recommendation",
                source_reference="policy://unrelated",
                active=True,
                created_by=admin.id,
            )
            session.add_all([unrelated_claim, unrelated_rule]); session.flush()
            unrelated_recommendation = DSSRecommendation(
                claim_id=unrelated_claim.id,
                rule_set_id=unrelated_rule.id,
                rule_version="1",
                actor_id=admin.id,
                idempotency_key="unrelated-preseed",
                outcome="recommended",
                input_json={"facts": {"has_title": True}},
                output_json={"recommendation": "Unrelated recommendation"},
            )
            session.add(unrelated_recommendation); session.commit()

            seed_demo(session, actor_id=admin.id); session.commit()

            self.assertEqual(
                session.scalar(
                    select(func.count())
                    .select_from(DSSRecommendation)
                    .where(DSSRecommendation.rule_set_id == unrelated_rule.id)
                ),
                1,
            )
            self.assertIsNone(
                session.scalar(
                    select(DSSReferral).where(
                        DSSReferral.recommendation_id == unrelated_recommendation.id
                    )
                )
            )


if __name__ == "__main__":
    unittest.main()
