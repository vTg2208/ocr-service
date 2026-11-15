"""Fail readiness when the application database lacks required portal tables."""
from sqlalchemy import inspect
REQUIRED_TABLES={'users','account_credentials','user_plots','community_profiles','plot_submissions','portal_publications','user_resources','support_tickets','ticket_messages','ticket_attachments','contact_otps','land_cover_analyses'}
def assert_portal_schema(bind):
    schema=inspect(bind)
    missing=(REQUIRED_TABLES|{'ocr_page_artifacts'})-set(schema.get_table_names())
    if missing:
        raise RuntimeError('Database update required. Run python -m scripts.migrate. Missing tables: '+', '.join(sorted(missing)))
    columns={column['name'] for column in schema.get_columns('contact_otps')}
    if 'delivery_method' not in columns:
        raise RuntimeError('Database update required. Run python -m scripts.migrate. Missing contact delivery migration.')
