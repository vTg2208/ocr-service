import json
import unittest
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import User
from app.services.scheme_catalog import CatalogValidationError, create_catalog_entry


class SchemeCatalogTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:"); Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            user = User(external_id="catalog-admin", role="admin"); session.add(user); session.commit(); self.user_id = user.id

    def tearDown(self): self.engine.dispose()

    def test_authoritative_active_entry_requires_approval_dates_and_source(self):
        base = {"scheme_code": "JJM", "display_name": "Jal Jeevan Mission", "version": "tn-2026", "department": "Rural Development", "description": "Water-service planning reference", "source_reference": "https://example.gov.in/jjm", "definition": {"reviewed_on": "2026-08-01"}, "authoritative": True, "active": True}
        with Session(self.engine) as session:
            with self.assertRaises(CatalogValidationError):
                create_catalog_entry(session, base, actor_id=self.user_id)
            valid = {**base, "approving_authority": "Tamil Nadu competent authority", "effective_from": date(2026, 8, 1)}
            entry = create_catalog_entry(session, valid, actor_id=self.user_id)
            self.assertTrue(entry.authoritative); self.assertTrue(entry.active)
            with self.assertRaises(CatalogValidationError):
                create_catalog_entry(session, {**valid, "version": "bad-dates", "effective_to": date(2026, 7, 1)}, actor_id=self.user_id)

    def test_bundled_tamil_nadu_catalog_is_non_authoritative_and_inactive(self):
        entries = json.loads(Path("data/tn_scheme_catalog.sample.json").read_text(encoding="utf-8"))
        self.assertEqual({row["scheme_code"] for row in entries}, {"PM-KISAN", "MGNREGA", "PMAY-G", "JJM", "DAJGUA"})
        self.assertTrue(all(not row["authoritative"] and not row["active"] for row in entries))
        self.assertTrue(all(row["source_reference"] for row in entries))


if __name__ == "__main__": unittest.main()
