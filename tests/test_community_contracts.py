"""Route wiring and readiness checks, independent from example fixture counts."""
import re
import unittest
from pathlib import Path
from sqlalchemy import create_engine
from app.main import app
from app.services.portal_schema import assert_portal_schema

class CommunityContractTests(unittest.TestCase):
    def test_frontend_community_urls_are_registered_api_paths(self):
        schema=app.openapi()['paths']
        route_patterns=[re.compile('^'+re.sub(r'\{[^}]+\}',r'[^/]+',path)+'$') for path in schema]
        paths=set()
        for filename in ['app/static/user/portal.js','app/static/staff-services/app.js','app/static/staff-services/maps.js']:
            source=Path(filename).read_text()
            for literal in re.findall(r"(?:'|\"|`)(/api/(?:user|staff/portal|community)/[^'\"`\s]+)(?:'|\"|`)",source):
                path=re.sub(r'\$\{[^}]+\}','test-id',literal).split('?')[0]
                if path.endswith('/'):continue # The dynamic publication id is concatenated later.
                paths.add(path)
                self.assertTrue(any(pattern.match(path) for pattern in route_patterns),(filename,path))
        self.assertGreater(len(paths),15)
    def test_readiness_rejects_a_database_without_portal_tables(self):
        engine=create_engine('sqlite://')
        with self.assertRaisesRegex(RuntimeError,'account_credentials'):assert_portal_schema(engine)
        engine.dispose()
    def test_revision_commands_and_migration_files_are_included(self):
        self.assertIn('python -m scripts.migrate',Path('Dockerfile').read_text())
        self.assertTrue(Path('migrations/versions/20261112_0014_user_plots.py').exists())
        self.assertTrue(Path('migrations/versions/20261112_0015_community_portal.py').exists())

if __name__=='__main__':unittest.main()
