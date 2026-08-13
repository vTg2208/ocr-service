import unittest

from pydantic import ValidationError

from app.config import Settings


class ProductionConfigurationTests(unittest.TestCase):
    def test_production_rejects_insecure_registry_defaults(self):
        cases = [
            {"auth_secret": "change-me-in-production", "database_url": "postgresql+psycopg://db", "malware_scan_required": True},
            {"auth_secret": "replace-with-at-least-32-random-bytes", "database_url": "postgresql+psycopg://db", "malware_scan_required": True},
            {"auth_secret": "x" * 32, "database_url": "sqlite+pysqlite:///local.db", "malware_scan_required": True},
            {"auth_secret": "x" * 32, "database_url": "postgresql+psycopg://db", "malware_scan_required": False},
        ]
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                Settings(_env_file=None, environment="production", **values)

    def test_development_keeps_hermetic_sqlite_defaults(self):
        settings = Settings(_env_file=None)
        self.assertEqual(settings.environment, "development")
        self.assertTrue(settings.database_url.startswith("sqlite"))


if __name__ == "__main__":
    unittest.main()
