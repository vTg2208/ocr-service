"""OIDC authorization-code + PKCE login; stored database roles remain authoritative."""

from functools import lru_cache
import hashlib

from authlib.integrations.starlette_client import OAuth
from fastapi import HTTPException
from sqlalchemy import select

from app.config import get_settings
from app.db.models import User


def external_identity(issuer, subject):
    if not isinstance(subject, str) or not subject.strip():
        raise ValueError("OIDC subject is missing.")
    return "oidc:" + hashlib.sha256((issuer + "\0" + subject).encode()).hexdigest()


@lru_cache(maxsize=1)
def oidc_client():
    settings = get_settings()
    if not settings.oidc_enabled:
        raise HTTPException(404, "Single sign-on is not configured.")
    oauth = OAuth()
    oauth.register("authority", client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret.get_secret_value(),
        server_metadata_url=settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration",
        client_kwargs={"scope": "openid profile", "code_challenge_method": "S256", "timeout": 15})
    return oauth.create_client("authority")


def provision_identity(db, claims):
    settings = get_settings()
    if claims.get("iss") != settings.oidc_issuer:
        raise HTTPException(401, "Identity issuer mismatch.")
    external_id = external_identity(settings.oidc_issuer, claims.get("sub"))
    user = db.scalar(select(User).where(User.external_id == external_id))
    if user is None:
        if not settings.oidc_auto_provision:
            raise HTTPException(403, "An administrator must provision your account before sign-in.")
        user = User(external_id=external_id, display_name=str(claims.get("name") or "Community member")[:120], role="user")
        db.add(user)
        db.flush()
    # Never link by email, accept token roles, or elevate an existing account.
    return user
