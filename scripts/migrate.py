"""Migrate and verify the exact DATABASE_URL used by the application."""
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from app.config import get_settings
from app.services.portal_schema import assert_portal_schema

def main():
    config=Config('alembic.ini')
    config.set_main_option('sqlalchemy.url',get_settings().database_url.replace('%','%%'))
    command.upgrade(config,'head')
    engine=create_engine(get_settings().database_url)
    try:assert_portal_schema(engine)
    finally:engine.dispose()
    print('Application database migrations and portal schema verified.')

if __name__=='__main__':main()
