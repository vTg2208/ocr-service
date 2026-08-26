import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_completion_models import AssetFeature, FRAArchiveRecord, FRAVillageProfile
from app.db.fra_models import DSSRecommendation, FRAClaim, FRATitle
from app.db.models import User
from scripts.seed_tamil_nadu_fra_demo import seed_demo


class TamilNaduDemoTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_seed_is_idempotent_coherent_and_visibly_synthetic(self):
        with Session(self.engine) as session:
            admin = User(external_id="demo-admin", display_name="Demo Admin", role="admin")
            session.add(admin); session.commit()
            first = seed_demo(session, actor_id=admin.id)
            session.commit()
            second = seed_demo(session, actor_id=admin.id)
            session.commit()

            self.assertGreater(first.created, 0)
            self.assertEqual(second.created, 0)
            self.assertEqual(session.scalar(select(func.count()).select_from(FRAVillageProfile)), 3)
            records = list(session.scalars(select(FRAArchiveRecord)))
            self.assertEqual({row.right_type for row in records}, {"IFR", "CR", "CFR"})
            self.assertTrue(all(row.synthetic and row.state_code == "TN" for row in records))
            self.assertGreaterEqual(session.scalar(select(func.count()).select_from(FRAClaim)), 3)
            self.assertGreaterEqual(session.scalar(select(func.count()).select_from(FRATitle)), 1)
            self.assertGreaterEqual(session.scalar(select(func.count()).select_from(AssetFeature)), 2)
            water = session.scalar(select(AssetFeature).where(AssetFeature.source_reference == "tn-demo-scene-2005"))
            agriculture = session.scalar(select(AssetFeature).where(AssetFeature.source_reference == "tn-demo-scene-2025"))
            self.assertEqual(water.village.village_name, "Kottur Demo")
            self.assertEqual(agriculture.village.village_name, "Kottur Demo")
            self.assertGreaterEqual(session.scalar(select(func.count()).select_from(DSSRecommendation)), 1)


if __name__ == "__main__":
    unittest.main()
