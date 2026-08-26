"""Temporary browser-session routes for the registry demonstration."""

import secrets

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import (
    SESSION_COOKIE,
    AuthenticatedUser,
    create_access_token,
    get_current_user,
)
from app.config import get_settings
from app.db.models import User
from app.db.session import get_db


router = APIRouter(prefix="/api/auth", tags=["authentication"])
settings = get_settings()


class DemoLoginRequest(BaseModel):
    access_code: str


@router.post("/demo-login")
def demo_login(payload: DemoLoginRequest, db: Session = Depends(get_db)):
    if not settings.demo_auth_enabled:
        raise HTTPException(status_code=404, detail="Demonstration login is disabled.")
    if not secrets.compare_digest(payload.access_code, settings.demo_access_code):
        raise HTTPException(status_code=401, detail="Invalid access code.")

    user = db.scalar(select(User).where(User.external_id == "registry-demo"))
    if user is None:
        user = User(
            external_id="registry-demo",
            display_name="Registry staff",
            role="user",
        )
        db.add(user)
    else:
        user.display_name = "Registry staff"
        user.role = "user"
    db.commit()

    token = create_access_token(
        user.external_id,
        minutes=settings.demo_session_minutes,
    )
    response = JSONResponse({
        "external_id": user.external_id,
        "display_name": user.display_name,
        "role": user.role,
    })
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.demo_session_minutes * 60,
        httponly=True,
        secure=settings.environment.casefold() == "production",
        samesite="strict",
        path="/",
    )
    return response


@router.get("/session")
def current_session(user: AuthenticatedUser = Depends(get_current_user)):
    return {
        "external_id": user.external_id,
        "display_name": user.display_name,
        "role": user.role,
    }


@router.post("/logout", status_code=204)
def logout():
    response = Response(status_code=204)
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        secure=settings.environment.casefold() == "production",
        httponly=True,
        samesite="strict",
    )
    return response
