"""Minimal bearer identity adapter; replace with an OIDC verifier in production."""

from dataclasses import dataclass

import jwt
from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import User
from app.db.session import get_db
from app.config import get_settings

settings = get_settings()


@dataclass(frozen=True)
class AuthenticatedUser:
    id: object
    external_id: str
    role: str
    display_name: str | None


def get_current_user(
    authorization: str | None = Header(None), db: Session = Depends(get_db),
) -> AuthenticatedUser:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required.")
    token = authorization[7:].strip()
    try:
        claims = jwt.decode(
            token, settings.auth_secret, algorithms=["HS256"],
            issuer=settings.auth_issuer, audience=settings.auth_audience,
            options={"require": ["sub", "iat", "exp", "iss", "aud"]},
        )
        external_id = claims["sub"]
    except (jwt.PyJWTError, KeyError):
        raise HTTPException(status_code=401, detail="Invalid authentication token.")
    user = db.scalar(select(User).where(User.external_id == external_id))
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid authentication token.")
    return AuthenticatedUser(user.id, user.external_id, user.role, user.display_name)


def require_admin(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Administrator role required.")
    return user
