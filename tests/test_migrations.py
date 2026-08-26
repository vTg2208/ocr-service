import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


class MigrationTests(unittest.TestCase):
    def test_upgrade_head_creates_registry_schema(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "migration.db"
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database.as_posix()}")
            command.upgrade(config, "head")
            engine = create_engine(f"sqlite+pysqlite:///{database.as_posix()}")
            inspector = inspect(engine)
            tables = set(inspector.get_table_names())
            claim_constraints = {
                item.get("name") for item in inspector.get_unique_constraints("claims")
            }
            engine.dispose()
        self.assertTrue({"parcels", "documents", "ocr_results", "claims", "claim_conflicts", "audit_events"} <= tables)
        self.assertTrue({
            "rights_holders", "gram_sabhas", "fra_claims", "fra_decisions",
            "fra_geometry_versions", "satellite_observations", "fra_evidence_items",
            "fra_titles", "scheme_rule_sets", "dss_recommendations",
        } <= tables)
        self.assertIn("uq_claim_parcel_exclusive", claim_constraints)


if __name__ == "__main__":
    unittest.main()
