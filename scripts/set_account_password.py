"""Provision a password for an existing account without changing its stored role."""
import argparse
import getpass
from sqlalchemy import select
from app.db.models import User
from app.db.user_plot_models import AccountCredential
from app.db.session import get_session_factory
from app.services.account_passwords import hash_password

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('external_id')
    args = parser.parse_args()
    password = getpass.getpass('New password (10–128 characters): ')
    if password != getpass.getpass('Confirm password: '):
        parser.error('Passwords do not match.')
    try: encoded = hash_password(password)
    except ValueError as error: parser.error(str(error))
    with get_session_factory()() as db:
        user = db.scalar(select(User).where(User.external_id == args.external_id.strip().lower()))
        if user is None: parser.error('Create this account first using scripts.create_user.')
        credential = db.get(AccountCredential, user.id)
        if credential: credential.password_hash = encoded
        else: db.add(AccountCredential(user_id=user.id, password_hash=encoded))
        db.commit()
        print(f'Password set. Stored role: {user.role}')

if __name__ == '__main__': main()
