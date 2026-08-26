"""Minimal bearer identity adapter; replace with an OIDC verifier in production."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Cookie, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import User
from app.db.session import get_db
from app.config import get_settings

settings = get_settings()
SESSION_COOKIE = "parcel_registry_session"


@dataclass(frozen=True)
class AuthenticatedUser:
    id: object
    external_id: str
    role: str
    display_name: str | None


def create_access_token(external_id: str, *, minutes: int) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": external_id,
            "iat": now,
            "exp": now + timedelta(minutes=minutes),
            "iss": settings.auth_issuer,
            "aud": settings.auth_audience,
        },
        settings.auth_secret,
        algorithm="HS256",
    )


def get_current_user(
    authorization: str | None = Header(None),
    session_token: str | None = Cookie(None, alias=SESSION_COOKIE),
    db: Session = Depends(get_db),
) -> AuthenticatedUser:
    if authorization:
        if not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Authentication required.")
        token = authorization[7:].strip()
    else:
        token = session_token
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required.")
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


def require_reviewer(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
    if user.role not in {"reviewer", "admin"}:
        raise HTTPException(status_code=403, detail="Reviewer or administrator role required.")
    return user
