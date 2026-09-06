"""Opt-in PostgreSQL backend for the existing FRA regression tests.

Loaded only by scripts/test_postgres.py. Application services and SQL stay real;
only each test module's engine factory is redirected to the migrated database.
"""

import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.db.base import Base
from app.db import models, fra_models, fra_completion_models, fra_operational_models  # noqa: F401


@pytest.fixture(scope="session")
def postgres_engine():
    url = make_url(os.environ["FRA_TEST_POSTGRES_URL"])
    if (url.drivername != "postgresql+psycopg"
            or url.host != "127.0.0.1"
            or not (url.database or "").startswith("fra_test_")):
        raise ValueError("PostgreSQL tests require an isolated local fra_test_ database.")
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%"))
    command.upgrade(config, "head")
    command.check(config)
    engine = create_engine(url, connect_args={"options": "-c statement_timeout=15000 -c lock_timeout=10000"})
    try:
        with engine.connect() as connection:
            assert set(Base.metadata.tables) <= set(inspect(connection).get_table_names())
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260906_0013"
            print("\nPostgreSQL", connection.scalar(text("SHOW server_version")),
                  "| PostGIS", connection.scalar(text("SELECT postgis_lib_version()")))
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def isolated_postgres_test(request, monkeypatch, postgres_engine):
    # All names come from application metadata, never from request data. Retain
    # Alembic and PostGIS's own tables; clear synthetic application data only.
    names = ", ".join(postgres_engine.dialect.identifier_preparer.quote(name)
                      for name in Base.metadata.tables)
    with postgres_engine.begin() as connection:
        connection.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))
    if hasattr(request.module, "create_engine"):
        # SQLite-specific pool/check_same_thread arguments belong to the old
        # fixture; PostgreSQL uses its actual connection pool and transactions.
        monkeypatch.setattr(request.module, "create_engine", lambda *args, **kwargs: postgres_engine)
