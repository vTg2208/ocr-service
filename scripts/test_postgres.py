"""Run FRA regression and native lock tests in a disposable PostGIS container.

Usage: python scripts/test_postgres.py [additional pytest options]
Requires Docker and the project's test dependencies. No application volumes,
database settings or running containers are modified.
"""

import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]
TARGETS = [
    "test_fra_intake.py", "test_fra_archive.py", "test_fra_document_intake.py",
    "test_fra_tabular_intake.py", "test_fra_geospatial_import.py",
    "test_fra_geospatial_api.py", "test_fra_assets.py",
    "test_fra_claims.py", "test_fra_workflow.py", "test_historical_evidence.py",
    "test_satellite_ingestion.py",
    "test_dss_referrals.py", "test_dss_facts.py", "test_dss_engine.py",
    "test_scheme_catalog.py", "test_scheme_convergence.py",
    "test_scheme_rule_governance.py", "test_model_gateway.py",
    "test_processing_jobs.py", "test_fra_operations_api.py",
    "test_fra_concurrency.py", "test_fra_data_contracts.py",
    "test_fra_atlas.py",
    "test_fra_operational_journey.py", "test_fra_integration_pipelines.py",
    "test_water_dss_journey.py", "test_tamil_nadu_demo.py",
    "test_tamil_nadu_pilot.py",
    "test_fra_api.py", "test_fra_intake_api.py", "test_fra_archive_api.py",
    "test_fra_case_api.py", "test_fra_evidence_api.py", "test_fra_asset_api.py",
    "test_fra_planning_api.py", "test_fra_atlas_api.py", "test_fra_dashboard_api.py",
    "test_postgres_native.py",
]


def docker(*args, **kwargs):
    return subprocess.run(["docker", *args], check=True, text=True, **kwargs)


def main():
    name = "fra-postgis-test-" + uuid.uuid4().hex[:12]
    password = secrets.token_hex(24)
    environment = {**os.environ, "POSTGRES_PASSWORD": password}
    created = False
    try:
        docker("run", "--detach", "--name", name,
               "--label", "codex.fra-postgis-test=true",
               "--publish", "127.0.0.1::5432", "--env", "POSTGRES_PASSWORD",
               "--env", "POSTGRES_USER=fra_test", "--env", "POSTGRES_DB=fra_test_integration",
               "postgis/postgis:16-3.4", env=environment, stdout=subprocess.DEVNULL)
        created = True
        print(f"Started isolated PostGIS container {name}", flush=True)
        deadline = time.monotonic() + 60
        while True:
            ready = subprocess.run(["docker", "exec", name, "pg_isready", "-U", "fra_test",
                                    "-d", "fra_test_integration"], capture_output=True)
            if ready.returncode == 0:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("Test PostGIS database did not become ready within 60 seconds.")
            time.sleep(1)
        ports = json.loads(docker("inspect", "--format", "{{json .NetworkSettings.Ports}}",
                                  name, capture_output=True).stdout)
        port = ports["5432/tcp"][0]["HostPort"]
        environment = {**os.environ,
            "FRA_TEST_POSTGRES_URL": f"postgresql+psycopg://fra_test:{password}@127.0.0.1:{port}/fra_test_integration",
            "PYTHONPATH": os.pathsep.join([str(ROOT), str(ROOT / "tests"), os.environ.get("PYTHONPATH", "")]),
        }
        return subprocess.run([sys.executable, "-m", "pytest", "-p", "postgres_plugin",
                               "-q", "--tb=short", *[str(ROOT / "tests" / name) for name in TARGETS],
                               *sys.argv[1:]], cwd=ROOT, env=environment).returncode
    finally:
        if created:
            docker("rm", "--force", "--volumes", name, stdout=subprocess.DEVNULL)
            print(f"Removed test container and its temporary volume: {name}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
