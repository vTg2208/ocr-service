import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_models import (
    FRADecision,
    FRAClaim,
    FRAGeometryVersion,
    GramSabha,
    RightsHolder,
)
from app.db.models import User


GEOMETRY = {
    "type": "MultiPolygon",
    "coordinates": [[[[79.0, 10.0], [79.001, 10.0], [79.001, 10.001], [79.0, 10.001], [79.0, 10.0]]]],
}


class FRADatabaseModelTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_staff_actor_and_rights_holder_are_distinct(self):
        with Session(self.engine) as session:
            staff = User(external_id="staff-1", display_name="Registry staff", role="user")
            holder = RightsHolder(
                display_name="Ramu Naik", holder_type="individual", claimant_category="ST"
            )
            session.add_all([staff, holder])
            session.flush()
            claim = FRAClaim(
                claim_number="FRA-OD-001",
                right_type="IFR",
                status="draft",
                rights_holder_id=holder.id,
                submitted_by=staff.id,
            )
            session.add(claim)
            session.commit()

            self.assertNotEqual(claim.rights_holder_id, claim.submitted_by)
            self.assertEqual(claim.rights_holder.display_name, "Ramu Naik")
            self.assertEqual(claim.submitter.external_id, "staff-1")

    def test_claim_retains_versioned_geometry_and_append_only_decisions(self):
        with Session(self.engine) as session:
            staff = User(external_id="reviewer-1", display_name="Reviewer", role="reviewer")
            gram_sabha = GramSabha(name="Example Gram Sabha", village="Example Village")
            holder = RightsHolder(
                display_name="Example Gram Sabha",
                holder_type="community",
                claimant_category="ST",
                gram_sabha=gram_sabha,
            )
            claim = FRAClaim(
                claim_number="FRA-CFR-001",
                right_type="CFR",
                status="gram_sabha_verified",
                rights_holder=holder,
                gram_sabha=gram_sabha,
                submitter=staff,
            )
            session.add(claim)
            session.flush()
            session.add_all(
                [
                    FRAGeometryVersion(
                        claim_id=claim.id,
                        version=1,
                        geometry=GEOMETRY,
                        source="claimant_sketch",
                        provenance_json={"record": "Form C"},
                        boundary_quality="unverified",
                        created_by=staff.id,
                    ),
                    FRADecision(
                        claim_id=claim.id,
                        authority_level="gram_sabha",
                        from_status="submitted",
                        to_status="gram_sabha_verified",
                        outcome="verified",
                        reasons_json=["Resolution GS-17"],
                        actor_id=staff.id,
                    ),
                ]
            )
            session.commit()

            self.assertEqual(len(claim.geometry_versions), 1)
            self.assertEqual(claim.geometry_versions[0].version, 1)
            self.assertEqual(len(claim.decisions), 1)
            self.assertEqual(claim.decisions[0].reasons_json, ["Resolution GS-17"])


if __name__ == "__main__":
    unittest.main()
