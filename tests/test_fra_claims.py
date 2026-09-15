import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_completion_models import FRAVillageProfile
from app.db.fra_models import FRAClaim, GramSabha, RightsHolder
from app.db.models import Claim, Document, Parcel, User
from app.services.fra_claims import (
    FRAClaimValidationError,
    FRAClaimConflictError,
    add_geometry_version,
    create_claim,
    promote_legacy_claim,
)


GEOMETRY = {
    "type": "MultiPolygon",
    "coordinates": [[[[79.0, 10.0], [79.001, 10.0], [79.001, 10.001], [79.0, 10.001], [79.0, 10.0]]]],
}


class FRACadastralLinkServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            staff = User(external_id="staff", display_name="Registry staff", role="user")
            individual = RightsHolder(display_name="Ramu Naik", holder_type="individual")
            community = RightsHolder(display_name="Example community", holder_type="community")
            session.add_all([staff, individual, community])
            session.commit()
            self.staff_id = staff.id
            self.individual_id = individual.id
            self.community_id = community.id

    def tearDown(self):
        self.engine.dispose()

    def test_ifr_creation_keeps_staff_and_rights_holder_separate(self):
        with Session(self.engine) as session:
            claim = create_claim(
                session,
                claim_number="IFR-001",
                right_type="IFR",
                rights_holder_id=self.individual_id,
                submitted_by=self.staff_id,
            )
            session.commit()

            self.assertNotEqual(claim.rights_holder_id, claim.submitted_by)
            self.assertEqual(claim.status, "draft")

    def test_community_claim_requires_gram_sabha(self):
        with Session(self.engine) as session:
            with self.assertRaisesRegex(FRAClaimValidationError, "Gram Sabha"):
                create_claim(
                    session,
                    claim_number="CFR-001",
                    right_type="CFR",
                    rights_holder_id=self.community_id,
                    submitted_by=self.staff_id,
                )

    def test_ifr_requires_individual_or_household_holder(self):
        with Session(self.engine) as session:
            with self.assertRaisesRegex(FRAClaimValidationError, "individual or household"):
                create_claim(
                    session,
                    claim_number="IFR-COMMUNITY",
                    right_type="IFR",
                    rights_holder_id=self.community_id,
                    submitted_by=self.staff_id,
                )

    def test_direct_legacy_promotion_requires_a_reviewed_intake(self):
        with Session(self.engine) as session:
            parcel = Parcel(
                state="Tamil Nadu",
                district="Thanjavur",
                taluk="Kumbakonam",
                village="Example Village",
                survey_number="701",
                subdivision_number="4B",
                official_area_sqm=1200,
                geometry=GEOMETRY,
                source="synthetic",
            )
            document = Document(
                uploaded_by=self.staff_id,
                storage_key="private/legacy.png",
                original_filename="legacy.png",
                content_type="image/png",
                sha256="a" * 64,
                ocr_status="completed",
                idempotency_key="upload-legacy",
            )
            legacy = Claim(
                claimant_id=self.staff_id,
                parcel=parcel,
                document=document,
                confirmed_fields_json={"survey_number": "701", "village": "Example Village"},
                status="matched",
                match_confidence=1,
                match_method="exact_composite_key",
                idempotency_key="claim-legacy",
            )
            session.add(legacy)
            session.commit()

            with self.assertRaisesRegex(FRAClaimValidationError, "reviewed FRA intake"):
                promote_legacy_claim(
                    session,
                    legacy_claim_id=legacy.id,
                    rights_holder_id=self.individual_id,
                    right_type="IFR",
                    actor_id=self.staff_id,
                )

    def test_geometry_versions_increment_without_overwriting_history(self):
        with Session(self.engine) as session:
            claim = create_claim(
                session,
                claim_number="IFR-GEOMETRY",
                right_type="IFR",
                rights_holder_id=self.individual_id,
                submitted_by=self.staff_id,
            )
            first = add_geometry_version(
                session,
                claim,
                geometry=GEOMETRY,
                source="claimant_sketch",
                provenance={"form": "A"},
                boundary_quality="unverified",
                actor_id=self.staff_id,
            )
            second_geometry = {
                "type": "MultiPolygon",
                "coordinates": [[[[79.0, 10.0], [79.002, 10.0], [79.002, 10.002], [79.0, 10.002], [79.0, 10.0]]]],
            }
            second = add_geometry_version(
                session,
                claim,
                geometry=second_geometry,
                source="field_verification",
                provenance={"visit": "V-1"},
                boundary_quality="verified",
                actor_id=self.staff_id,
            )
            session.commit()

            self.assertEqual((first.version, second.version), (1, 2))
            self.assertEqual(len(claim.geometry_versions), 2)
            self.assertEqual(claim.geometry_versions[0].geometry, GEOMETRY)

    def test_geometry_records_area_overlap_and_administrative_containment(self):
        with Session(self.engine) as session:
            village = FRAVillageProfile(
                state_code="TN", state_name="Tamil Nadu", district_code="D",
                district_name="Test", block_code="B", block_name="Test",
                village_code="V", village_name="Test", boundary=GEOMETRY,
                tribal_groups_json=[], socioeconomic_json={}, provenance_json={"source": "test"},
                reference_version="v1", synthetic=True,
            )
            session.add(village); session.flush()
            claim = create_claim(
                session, claim_number="IFR-SPATIAL", right_type="IFR",
                rights_holder_id=self.individual_id, submitted_by=self.staff_id,
                village_id=village.id,
            )
            version = add_geometry_version(
                session, claim, geometry=GEOMETRY, source="field survey",
                provenance={"source": "test"}, boundary_quality="surveyed",
                actor_id=self.staff_id,
            )
            session.flush()

            validation = version.provenance_json["spatial_validation"]
            self.assertGreater(validation["area_sqm"], 0)
            self.assertEqual(validation["administrative_containment"], "within_linked_village")
            self.assertEqual(validation["overlap_outcome"], "allowed")

    def test_stale_geometry_allocation_conflicts_before_insert(self):
        from sqlalchemy import select
        from app.db.fra_models import FRAGeometryVersion
        with Session(self.engine) as setup:
            claim = create_claim(setup, claim_number="CONCURRENT", right_type="IFR",
                                 rights_holder_id=self.individual_id, submitted_by=self.staff_id)
            claim_id = claim.id
            setup.commit()
        with Session(self.engine) as first, Session(self.engine) as second:
            current, stale = first.get(FRAClaim, claim_id), second.get(FRAClaim, claim_id)
            list(stale.geometry_versions)
            add_geometry_version(first, current, geometry=GEOMETRY, source="first", provenance={},
                                 boundary_quality="unknown", actor_id=self.staff_id)
            first.commit()
            with self.assertRaisesRegex(FRAClaimConflictError, "changed"):
                add_geometry_version(second, stale, geometry=GEOMETRY, source="stale", provenance={},
                                     boundary_quality="unknown", actor_id=self.staff_id)
            second.rollback()
            second.refresh(stale)
            add_geometry_version(second, stale, geometry=GEOMETRY, source="second", provenance={},
                                 boundary_quality="unknown", actor_id=self.staff_id)
            second.commit()
        with Session(self.engine) as check:
            rows = check.scalars(select(FRAGeometryVersion).order_by(FRAGeometryVersion.version)).all()
            self.assertEqual([(row.version, row.source) for row in rows], [(1, "first"), (2, "second")])


if __name__ == "__main__":
    unittest.main()
