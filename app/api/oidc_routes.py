"""OIDC entry/callback and public sign-in capabilities without provider secrets."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.auth import SESSION_COOKIE, create_access_token
from app.config import get_settings
from app.db.session import get_db
from app.services.audit import record_audit
from app.services.oidc import oidc_client, provision_identity


router = APIRouter(prefix="/api/auth", tags=["authentication"])


@router.get("/config")
def auth_configuration():
    s = get_settings()
    return JSONResponse({"oidc_enabled": s.oidc_enabled, "local_auth_enabled": s.local_auth_enabled,
        "demo_auth_enabled": s.demo_auth_enabled, "oidc_login_url": "/api/auth/oidc/login" if s.oidc_enabled else None},
        headers={"Cache-Control": "no-store"})


@router.get("/oidc/login")
async def oidc_login(request: Request):
    client = oidc_client()
    try:
        metadata = await client.load_server_metadata()
        if metadata.get("issuer") != get_settings().oidc_issuer:
            raise ValueError("Issuer mismatch")
        return await client.authorize_redirect(request, get_settings().oidc_redirect_uri)
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(503, "Identity provider is unavailable or incorrectly configured.") from error


@router.get("/oidc/callback")
async def oidc_callback(request: Request, db: Session = Depends(get_db)):
    settings = get_settings()
    client = oidc_client()
    try:
        token = await client.authorize_access_token(request, leeway=30, claims_options={
            "iss": {"essential": True, "value": settings.oidc_issuer}, "sub": {"essential": True},
            "aud": {"essential": True, "value": settings.oidc_client_id}, "exp": {"essential": True},
            "iat": {"essential": True}})
        claims = token.get("userinfo")
        if not claims:
            raise ValueError("A validated ID token is required")
        user = provision_identity(db, claims)
        record_audit(db, actor_id=user.id, action="oidc_signed_in", entity_type="user", entity_id=user.id)
        db.commit()
    except HTTPException:
        db.rollback()
        request.session.clear()
        raise
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(409, "Account provisioning changed; start sign-in again.") from error
    except Exception as error:
        db.rollback()
        request.session.clear()
        raise HTTPException(401, "Single sign-on could not be verified. Start sign-in again.") from error
    request.session.clear()
    response = RedirectResponse("/my-plots" if user.role == "user" else "/fra", status_code=303)
    response.set_cookie(SESSION_COOKIE, create_access_token(user.external_id,
        minutes=settings.demo_session_minutes, authentication_method="oidc"),
        httponly=True, secure=settings.environment == "production", samesite="lax",
        max_age=settings.demo_session_minutes * 60, path="/")
    response.headers["Cache-Control"] = "no-store"
    return response
