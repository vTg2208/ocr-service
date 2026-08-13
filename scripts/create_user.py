import argparse

from sqlalchemy import select

from app.db.models import User
from app.db.session import get_session_factory


def main():
    parser = argparse.ArgumentParser(description="Create or update a central registry user.")
    parser.add_argument("external_id")
    parser.add_argument("--display-name")
    parser.add_argument("--role", choices=("user", "admin"), default="user")
    args = parser.parse_args()
    with get_session_factory()() as session:
        user = session.scalar(select(User).where(User.external_id == args.external_id))
        if user is None:
            user = User(external_id=args.external_id)
            session.add(user)
        user.display_name, user.role = args.display_name, args.role
        session.commit()
        print(user.id)


if __name__ == "__main__":
    main()
