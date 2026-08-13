import json
import unittest
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import AdministrativeAlias
from app.services.alias_importer import import_aliases


class AliasImporterTests(unittest.TestCase):
    def test_imports_local_language_aliases_idempotently(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        payload = json.loads(Path("data/administrative_aliases.json").read_text(encoding="utf-8"))
        with Session(engine) as session:
            first = import_aliases(payload, session); session.commit()
            second = import_aliases(payload, session); session.commit()
            count = session.scalar(select(func.count()).select_from(AdministrativeAlias))
        self.assertGreaterEqual(first["inserted"], 4)
        self.assertEqual(second["skipped"], count)
        self.assertTrue(any(item["language"] == "ta" for item in payload))


if __name__ == "__main__":
    unittest.main()
