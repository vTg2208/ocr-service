"""Provision an OIDC subject with an explicit operator-assigned database role."""
import argparse
from sqlalchemy import select
from app.db.models import User
from app.db.session import get_session_factory
from app.services.oidc import external_identity


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--issuer',required=True)
    parser.add_argument('--subject',required=True)
    parser.add_argument('--display-name',required=True)
    parser.add_argument('--role',choices=['user','reviewer','admin'],default='user')
    args=parser.parse_args()
    with get_session_factory()() as db:
        key=external_identity(args.issuer,args.subject)
        user=db.scalar(select(User).where(User.external_id==key))
        if user is None:
            user=User(external_id=key,display_name=args.display_name,role=args.role);db.add(user)
        else:
            user.display_name=args.display_name;user.role=args.role
        db.commit()
        print(f'Provisioned {key} with role {args.role}.')


if __name__=='__main__':main()
