import json
import unittest
import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_completion_models import FRAVillageProfile
from app.db.fra_models import FRAClaim, FRAGeometryVersion, GramSabha, RightsHolder
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

    def test_import_rejects_non_synthetic_or_unsupported_reference_data(self):
        with Session(self.engine) as session:
            admin = self._admin(session)
            payload = atlas_payload()
            payload["metadata"]["synthetic"] = False
            with self.assertRaisesRegex(AtlasValidationError, "synthetic"):
                import_village_profiles(session, payload, actor_id=admin.id)
            payload = atlas_payload()
            payload["metadata"]["state_code"] = "OD"
            with self.assertRaisesRegex(AtlasValidationError, "Tamil Nadu"):
                import_village_profiles(session, payload, actor_id=admin.id)

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
            privileged = atlas_features(session, filters, privileged=True)
            self.assertIn("rights_holder_id", json.dumps(privileged))


if __name__ == "__main__":
    unittest.main()
