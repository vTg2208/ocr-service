from logging.config import fileConfig

from alembic import context
import geoalchemy2  # noqa: F401  # Register PostGIS geometry reflection.
from sqlalchemy import engine_from_config, pool

from app.config import get_settings
from app.db.base import Base
from app.db import models  # noqa: F401
from app.db import fra_models  # noqa: F401
from app.db import fra_completion_models  # noqa: F401
from app.db import fra_operational_models  # noqa: F401

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
if config.get_main_option("sqlalchemy.url") == "sqlite+pysqlite:///./aranyasetu.db":
    config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata


def include_application_object(obj, name, type_, reflected, compare_to):
    """Keep PostGIS extension-owned tables out of application schema diffs."""

    if type_ == "table" and reflected and compare_to is None:
        return name in target_metadata.tables
    return True


def run_migrations_offline():
    context.configure(
        url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata,
        literal_binds=True, dialect_opts={"paramstyle": "named"}, compare_type=True,
        include_object=include_application_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section), prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_application_object,
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations_offline() if context.is_offline_mode() else run_migrations_online()
