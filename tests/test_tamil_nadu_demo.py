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
            self.assertGreaterEqual(session.scalar(select(func.count()).select_from(DSSRecommendation)), 1)
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
            recommendations = list(session.scalars(select(DSSRecommendation)))
            self.assertTrue(all(row.output_json.get("recommendation") for row in recommendations))

    def test_seed_refreshes_legacy_visible_values_without_duplicate_records(self):
        with Session(self.engine) as session:
            admin = User(external_id="sample-refresh-admin", display_name="Sample Administrator", role="admin")
            session.add(admin); session.commit()
            seed_demo(session, actor_id=admin.id); session.commit()
            counts_before = {
                model: session.scalar(select(func.count()).select_from(model))
                for model in (FRAVillageProfile, FRAArchiveRecord, FRAClaim, SchemeRuleSet)
            }
            village = session.scalar(select(FRAVillageProfile).where(FRAVillageProfile.village_code == "TN-13-01-001"))
            record = session.scalar(select(FRAArchiveRecord).where(FRAArchiveRecord.right_type == "IFR"))
            rule = session.scalar(select(SchemeRuleSet).where(SchemeRuleSet.scheme_code == "TN-FRA-WATER-SUPPORT"))
            village.village_name = "Kottur Demo"
            record.legacy_reference = "TN-DEMO-IFR-001"
            record.claim_number = "TN-DEMO-IFR-001"
            record.holder_display_name = "Kaveri Demo Household"
            record.village = "Kottur Demo"
            record.document.idempotency_key = "tn-demo-archive:TN-DEMO-IFR-001"
            record.document.original_filename = "TN-DEMO-IFR-001.synthetic.txt"
            record.provenance_json = {
                "synthetic": True,
                "source": "Synthetic final-year project archive pack; not authoritative",
                "version": "tn-demo-v1",
            }
            rule.scheme_code = "DEMO-WATER-SUPPORT"
            rule.display_name = "Demo Water Support Review"
            rule.version = "demo-1"
            rule.source_reference = "demo://water-support/v1"
            session.commit()

            seed_demo(session, actor_id=admin.id); session.commit()

            self.assertEqual(
                counts_before,
                {
                    model: session.scalar(select(func.count()).select_from(model))
                    for model in counts_before
                },
            )
            self.assertEqual(village.village_name, "Kottur")
            self.assertEqual(record.legacy_reference, "TN-FRA-IFR-001")
            self.assertEqual(record.claim_number, "TN-FRA-IFR-001")
            self.assertEqual(record.holder_display_name, "Kaveri Household")
            self.assertEqual(record.village, "Kottur")
            self.assertEqual(
                record.document.idempotency_key,
                "tn-demo-archive:TN-FRA-IFR-001",
            )
            self.assertEqual(rule.scheme_code, "TN-FRA-WATER-SUPPORT")
            self.assertEqual(rule.display_name, "Water Security Support Review")
            self.assertEqual(rule.version, "tn-sample-1")
            self.assertEqual(rule.source_reference, "synthetic://water-support/v1")

    def test_seed_refresh_is_scoped_and_removes_legacy_values_from_linked_records(self):
        with Session(self.engine) as session:
            admin = User(external_id="sample-scope-admin", display_name="Sample Administrator", role="admin")
            session.add(admin); session.commit()
            seed_demo(session, actor_id=admin.id); session.commit()

            native = session.scalar(
                select(FRAClaim).where(FRAClaim.claim_number == "TN-FRA-CFR-NATIVE-001")
            )
            promoted = session.scalar(
                select(FRAClaim).where(FRAClaim.claim_number == "TN-FRA-IFR-001")
            )
            record = session.scalar(
                select(FRAArchiveRecord).where(FRAArchiveRecord.right_type == "IFR")
            )
            native.claim_number = "TN-DEMO-CFR-NATIVE-001"
            native.rights_holder.claimant_category = "synthetic_demo"
            native.provenance_json = {"synthetic": True, "source": "tn-demo-native"}
            promoted.decisions[0].authority_level = "synthetic_demo"
            promoted.decisions[0].outcome = "demo_progression"
            promoted.decisions[0].request_id = "tn-demo-seed"
            promoted.geometry_versions[0].boundary_quality = "synthetic_demo"
            record.latest_extraction.provenance_json = {
                "adapter": "manifest",
                "synthetic": True,
                "document_reference": "TN-DEMO-IFR-001",
            }

            rule = session.scalar(
                select(SchemeRuleSet).where(
                    SchemeRuleSet.scheme_code == "TN-FRA-WATER-SUPPORT"
                )
            )
            unrelated_claim = FRAClaim(
                claim_number="TN-UNRELATED-001",
                right_type="IFR",
                status="draft",
                rights_holder_id=promoted.rights_holder_id,
                submitted_by=admin.id,
                provenance_json={"source": "unrelated"},
            )
            session.add(unrelated_claim); session.flush()
            unrelated = DSSRecommendation(
                claim_id=unrelated_claim.id,
                rule_set_id=rule.id,
                rule_version="historical-1",
                actor_id=admin.id,
                idempotency_key="unrelated-history",
                outcome="not_recommended",
                input_json={"facts": {"sentinel": True}},
                output_json={"sentinel": "preserve exactly"},
            )
            session.add(unrelated); session.commit()
            original_output = dict(unrelated.output_json)

            seed_demo(session, actor_id=admin.id); session.commit()

            exposed_values = [
                native.claim_number,
                native.rights_holder.claimant_category,
                native.provenance_json.get("source"),
                promoted.decisions[0].authority_level,
                promoted.decisions[0].outcome,
                promoted.decisions[0].request_id,
                promoted.geometry_versions[0].boundary_quality,
                record.latest_extraction.entity_model_version,
                record.latest_extraction.provenance_json.get("document_reference"),
            ]
            self.assertNotIn("demo", " ".join(exposed_values).casefold())
            self.assertEqual(unrelated.rule_version, "historical-1")
            self.assertEqual(unrelated.output_json, original_output)

    def test_seed_handles_coexisting_legacy_and_sample_identifiers(self):
        with Session(self.engine) as session:
            session.execute(text("PRAGMA foreign_keys=ON"))
            admin = User(external_id="sample-coexist-admin", display_name="Sample Administrator", role="admin")
            session.add(admin); session.commit()
            seed_demo(session, actor_id=admin.id); session.commit()
            baseline_counts = {
                model: session.scalar(select(func.count()).select_from(model))
                for model in (
                    FRAVillageProfile,
                    FRAArchiveRecord,
                    FRAClaim,
                    FRATitle,
                    AssetFeature,
                    SchemeRuleSet,
                )
            }

            current_claim = session.scalar(
                select(FRAClaim).where(FRAClaim.claim_number == "TN-FRA-CFR-NATIVE-001")
            )
            legacy_claim = FRAClaim(
                claim_number="TN-DEMO-CFR-NATIVE-001",
                right_type="CFR",
                status="submitted",
                rights_holder_id=current_claim.rights_holder_id,
                gram_sabha_id=current_claim.gram_sabha_id,
                submitted_by=admin.id,
                provenance_json={"synthetic": True, "source": "tn-demo-native"},
            )
            session.add(legacy_claim); session.flush()
            session.add(
                FRATitle(
                    claim_id=legacy_claim.id,
                    version=1,
                    title_number="TN-DEMO-TITLE-IFR-001",
                    metadata_json={"synthetic": True},
                    issued_by=admin.id,
                )
            )
            session.add(
                SchemeRuleSet(
                    scheme_code="DEMO-WATER-SUPPORT",
                    display_name="Demo Water Support Review",
                    version="demo-1",
                    required_facts_json=["has_title"],
                    condition_json={"present": {"fact": "has_title"}},
                    recommendation_text="Demo recommendation",
                    source_reference="demo://water-support/v1",
                    active=True,
                    created_by=admin.id,
                )
            )
            historical_rule = SchemeRuleSet(
                scheme_code="TN-FRA-WATER-SUPPORT",
                display_name="Historical water rule",
                version="tn-sample-2",
                required_facts_json=["has_title"],
                condition_json={"present": {"fact": "has_title"}},
                recommendation_text="Preserve this version",
                source_reference="policy://historical-water",
                active=False,
                created_by=admin.id,
            )
            session.add(historical_rule)
            water = session.scalar(
                select(AssetFeature).where(
                    AssetFeature.source_reference == "tn-sample-scene-2005"
                )
            )
            session.add(
                AssetFeature(
                    village_id=water.village_id,
                    claim_id=legacy_claim.id,
                    asset_class=water.asset_class,
                    point_geometry_json=water.point_geometry_json,
                    observed_value_json={"present": True},
                    source_type="synthetic_manifest",
                    source_reference="tn-demo-scene-2005",
                    provenance_json={"synthetic": True},
                    verification_state="unverified",
                    synthetic=True,
                )
            )
            batch = create_import_batch(
                session,
                source_label="Legacy synthetic archive",
                state="TN",
                actor_id=admin.id,
                idempotency_key="legacy-coexist-batch",
                synthetic=True,
                provenance={"synthetic": True, "source": "legacy sample"},
            )
            document = _document(session, admin.id, "TN-DEMO-IFR-001")
            create_archive_record(
                session,
                batch=batch,
                document_id=document.id,
                legacy_reference="TN-DEMO-IFR-001",
                actor_id=admin.id,
                synthetic=True,
            )
            session.commit()

            seed_demo(session, actor_id=admin.id); session.commit()

            identifiers = [
                *session.scalars(select(FRAArchiveRecord.legacy_reference)),
                *session.scalars(select(FRAClaim.claim_number)),
                *session.scalars(select(FRATitle.title_number)),
                *session.scalars(select(SchemeRuleSet.scheme_code)),
                *session.scalars(select(SchemeRuleSet.version)),
            ]
            self.assertNotIn("demo", " ".join(identifiers).casefold())
            expected_counts = dict(baseline_counts)
            expected_counts[SchemeRuleSet] += 1
            self.assertEqual(
                expected_counts,
                {
                    model: session.scalar(select(func.count()).select_from(model))
                    for model in expected_counts
                },
            )
            self.assertEqual(historical_rule.version, "tn-sample-2")
            self.assertEqual(historical_rule.recommendation_text, "Preserve this version")
            surviving_water = session.scalar(
                select(AssetFeature).where(
                    AssetFeature.source_reference == "tn-sample-scene-2005"
                )
            )
            self.assertEqual(surviving_water.claim_id, current_claim.id)

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
