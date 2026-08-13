import unittest

from sqlalchemy import create_engine, inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.db.base import Base
from app.db.models import ClaimConflict, Parcel


class DatabaseModelTests(unittest.TestCase):
    def test_metadata_creates_all_land_claim_tables(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")

        Base.metadata.create_all(engine)

        self.assertEqual(
            set(inspect(engine).get_table_names()),
            {
                "administrative_aliases",
                "audit_events",
                "claim_conflicts",
                "claims",
                "documents",
                "ocr_results",
                "parcels",
                "notifications",
                "users",
            },
        )

    def test_parcel_uses_postgis_multipolygon_in_postgresql(self):
        ddl = str(
            CreateTable(Parcel.__table__).compile(dialect=postgresql.dialect())
        ).upper()

        self.assertIn("GEOMETRY(MULTIPOLYGON,4326)", ddl.replace(" ", ""))

    def test_conflict_pair_and_type_are_unique(self):
        constraints = {
            tuple(column.name for column in constraint.columns)
            for constraint in ClaimConflict.__table__.constraints
            if hasattr(constraint, "columns")
        }

        self.assertIn(("claim_a_id", "claim_b_id", "conflict_type"), constraints)


if __name__ == "__main__":
    unittest.main()
