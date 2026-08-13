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
            tables = set(inspect(engine).get_table_names())
            engine.dispose()
        self.assertTrue({"parcels", "documents", "ocr_results", "claims", "claim_conflicts", "audit_events"} <= tables)


if __name__ == "__main__":
    unittest.main()
