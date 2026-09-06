import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.db.fra_models import SchemeRuleSet


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
            fra_claim_columns = {column["name"] for column in inspector.get_columns("fra_claims")}
            fra_title_columns = {column["name"] for column in inspector.get_columns("fra_titles")}
            rule_columns = {
                column["name"] for column in inspector.get_columns("scheme_rule_sets")
            }
            processing_job_columns = {
                column["name"] for column in inspector.get_columns("processing_jobs")
            }
            processing_job_indexes = {
                index["name"] for index in inspector.get_indexes("processing_jobs")
            }
            spatial_indexes = {
                index["name"]
                for table in (
                    "fra_geometry_versions", "fra_village_profiles",
                    "spatial_reference_features", "imagery_scenes", "asset_features",
                )
                for index in inspector.get_indexes(table)
            }
            asset_checks = {item.get("name") for item in inspector.get_check_constraints("asset_features")}
            observation_checks = {item.get("name") for item in inspector.get_check_constraints("satellite_observations")}
            engine.dispose()
        self.assertTrue({"parcels", "documents", "ocr_results", "claims", "claim_conflicts", "audit_events"} <= tables)
        self.assertTrue({
            "rights_holders", "gram_sabhas", "fra_claims", "fra_decisions",
            "fra_geometry_versions", "satellite_observations", "fra_evidence_items",
            "fra_titles", "scheme_rule_sets", "dss_recommendations",
        } <= tables)
        self.assertTrue({
            "fra_import_batches", "fra_archive_records", "fra_extraction_runs",
            "processing_jobs", "model_versions", "inference_runs",
            "fra_village_profiles", "asset_features", "dss_referrals",
            "report_artifacts",
        } <= tables)
        self.assertTrue({
            "fra_intake_items", "spatial_import_batches", "spatial_reference_features",
            "imagery_scenes", "imagery_artifacts", "dss_fact_snapshots",
            "scheme_catalog_entries", "fra_village_asset_profiles",
        } <= tables)
        self.assertIn("fra_field_reviews", tables)
        self.assertTrue({"village_id", "supersedes_claim_id"} <= fra_claim_columns)
        self.assertIn("granted_area_sqm", fra_title_columns)
        self.assertTrue({
            "required_evidence_json", "required_assets_json",
            "exclusion_condition_json", "priority_conditions_json",
            "freshness_requirements_json", "recommendation_logic_json", "catalog_entry_id",
        } <= rule_columns)
        self.assertTrue({
            "failure_history_json", "lease_token", "lease_expires_at", "heartbeat_at",
        } <= processing_job_columns)
        self.assertTrue({
            "ix_processing_jobs_dispatch", "ix_processing_jobs_lease",
        } <= processing_job_indexes)
        self.assertIn("uq_claim_parcel_exclusive", claim_constraints)
        self.assertTrue({
            "ix_fra_geometry_versions_geometry_gist",
            "ix_fra_village_profiles_boundary_gist",
            "ix_spatial_reference_features_geometry_gist",
            "ix_imagery_scenes_footprint_gist",
            "ix_asset_features_polygon_geometry_gist",
            "ix_asset_features_point_geometry_gist",
        } <= spatial_indexes)
        self.assertIn("ck_asset_features_asset_class", asset_checks)
        self.assertIn("ck_satellite_observations_asset_class", observation_checks)

    def test_completion_migration_is_the_only_head(self):
        config = Config("alembic.ini")

        self.assertEqual(ScriptDirectory.from_config(config).get_heads(), ["20260906_0013"])

    def test_fresh_migration_has_no_orm_schema_drift(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "migration.db"
            config = Config("alembic.ini")
            config.set_main_option(
                "sqlalchemy.url", f"sqlite+pysqlite:///{database.as_posix()}"
            )
            command.upgrade(config, "head")

            command.check(config)

    def test_historical_schema_revisions_do_not_import_live_models(self):
        historical = (
            "20260813_0001_land_registry.py",
            "20260826_0003_fra_foundation.py",
            "20260826_0004_fra_completion.py",
            "20260902_0005_fra_operational.py",
        )
        for filename in historical:
            source = (Path("migrations/versions") / filename).read_text(encoding="utf-8")
            with self.subTest(filename=filename):
                self.assertNotIn("app.db", source)
                self.assertNotIn("Base.metadata", source)

    def test_exclusive_parcel_constraint_is_added_by_its_own_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "migration.db"
            config = Config("alembic.ini")
            url = f"sqlite+pysqlite:///{database.as_posix()}"
            config.set_main_option("sqlalchemy.url", url)
            command.upgrade(config, "20260813_0001")
            engine = create_engine(url)
            before = {
                item.get("name") for item in inspect(engine).get_unique_constraints("claims")
            }
            command.upgrade(config, "20260826_0002")
            after = {
                item.get("name") for item in inspect(engine).get_unique_constraints("claims")
            }
            engine.dispose()

        self.assertNotIn("uq_claim_parcel_exclusive", before)
        self.assertIn("uq_claim_parcel_exclusive", after)

    def test_migration_chain_can_downgrade_and_rebuild_from_base(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "migration.db"
            config = Config("alembic.ini")
            config.set_main_option(
                "sqlalchemy.url", f"sqlite+pysqlite:///{database.as_posix()}"
            )
            command.upgrade(config, "head")
            command.downgrade(config, "base")
            command.upgrade(config, "head")

            command.check(config)

    def test_job_lease_migration_recovers_prelease_running_work(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "migration.db"
            config = Config("alembic.ini")
            url = f"sqlite+pysqlite:///{database.as_posix()}"
            config.set_main_option("sqlalchemy.url", url)
            command.upgrade(config, "20260906_0012")
            engine = create_engine(url)
            actor_id = uuid.uuid4().hex
            job_id = uuid.uuid4().hex
            now = datetime.now(timezone.utc)
            with engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO users (id, external_id, role, created_at) "
                    "VALUES (:id, 'migration-worker-owner', 'user', :created_at)"
                ), {"id": actor_id, "created_at": now})
                connection.execute(text(
                    "INSERT INTO processing_jobs "
                    "(id, task_type, entity_type, entity_id, state, attempts, max_attempts, "
                    "idempotency_key, payload_json, result_json, requested_by, worker_id, "
                    "available_at, started_at, created_at, updated_at) VALUES "
                    "(:id, 'archive_extract', 'archive_record', :entity_id, 'running', 1, 3, "
                    "'migration-stranded', '{}', '{}', :requested_by, 'old-worker', "
                    ":available_at, :started_at, :created_at, :updated_at)"
                ), {
                    "id": job_id,
                    "entity_id": uuid.uuid4().hex,
                    "requested_by": actor_id,
                    "available_at": now,
                    "started_at": now,
                    "created_at": now,
                    "updated_at": now,
                })
            command.upgrade(config, "head")
            with engine.connect() as connection:
                recovered = connection.execute(text(
                    "SELECT state, worker_id, error_code FROM processing_jobs WHERE id = :id"
                ), {"id": job_id}).one()
            engine.dispose()

        self.assertEqual(tuple(recovered), ("queued", None, "worker_restart_recovery"))

    def test_asset_taxonomy_migration_normalizes_legacy_rows_and_preserves_subtype(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "migration.db"
            config = Config("alembic.ini")
            url = f"sqlite+pysqlite:///{database.as_posix()}"
            config.set_main_option("sqlalchemy.url", url)
            command.upgrade(config, "20260905_0007")
            engine = create_engine(url)
            identifier = uuid.uuid4().hex
            with engine.begin() as connection:
                # Historical migrations import current metadata, so disable the newly
                # declared check briefly to emulate an actual pre-0008 database row.
                connection.exec_driver_sql("PRAGMA ignore_check_constraints = ON")
                connection.execute(text(
                    "INSERT INTO asset_features "
                    "(id, asset_class, observed_value_json, source_type, provenance_json, "
                    "verification_state, verification_reasons_json, synthetic, revision, created_at) "
                    "VALUES (:id, 'pond', :value, 'field', '{}', 'verified', '[]', 0, 0, :created_at)"
                ), {"id": identifier, "value": '{"present": true}',
                    "created_at": datetime.now(timezone.utc)})
                connection.exec_driver_sql("PRAGMA ignore_check_constraints = OFF")
            command.upgrade(config, "head")
            with engine.connect() as connection:
                row = connection.execute(text(
                    "SELECT asset_class, observed_value_json FROM asset_features WHERE id = :id"
                ), {"id": identifier}).one()
            engine.dispose()

        self.assertEqual(row.asset_class, "water_body")
        self.assertIn('"asset_subtype": "pond"', row.observed_value_json)

    def test_dss_contract_migration_maps_known_names_and_deactivates_unknown_rules(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "migration.db"
            config = Config("alembic.ini")
            url = f"sqlite+pysqlite:///{database.as_posix()}"
            config.set_main_option("sqlalchemy.url", url)
            command.upgrade(config, "20260906_0009")
            engine = create_engine(url)
            admin_id = uuid.uuid4()
            mapped_id = uuid.uuid4()
            unknown_id = uuid.uuid4()
            now = datetime.now(timezone.utc)
            with engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO users (id, external_id, role, created_at) "
                    "VALUES (:id, 'migration-dss-admin', 'admin', :created_at)"
                ), {"id": admin_id.hex, "created_at": now})
                for values in (
                    {
                        "id": mapped_id.hex, "scheme_code": "MAPPED", "display_name": "Mapped",
                        "required": '["has_title", "agricultural_land"]',
                        "condition": '{"all": [{"eq": {"fact": "has_title", "value": true}}, '
                                     '{"gte": {"fact": "agricultural_land", "value": 0.25}}]}',
                    },
                    {
                        "id": unknown_id.hex, "scheme_code": "UNKNOWN", "display_name": "Unknown",
                        "required": '["unmapped_fact"]',
                        "condition": '{"present": {"fact": "unmapped_fact"}}',
                    },
                ):
                    connection.execute(text(
                        "INSERT INTO scheme_rule_sets "
                        "(id, scheme_code, display_name, version, required_facts_json, "
                        "condition_json, recommendation_text, source_reference, active, "
                        "created_by, created_at) VALUES "
                        "(:id, :scheme_code, :display_name, '1', :required, :condition, "
                        "'Review', 'test', 1, :created_by, :created_at)"
                    ), {**values, "created_by": admin_id.hex, "created_at": now})

            command.upgrade(config, "head")
            with Session(engine) as session:
                mapped = session.get(SchemeRuleSet, mapped_id)
                unknown = session.get(SchemeRuleSet, unknown_id)
                self.assertEqual(
                    mapped.required_facts_json,
                    ["has_active_title", "agricultural_land_fraction"],
                )
                self.assertEqual(
                    mapped.condition_json["all"][1]["gte"]["fact"],
                    "agricultural_land_fraction",
                )
                self.assertFalse(mapped.active)
                self.assertIsNone(mapped.catalog_entry_id)
                self.assertFalse(unknown.active)
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
