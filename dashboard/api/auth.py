"""Google OAuth SSO + per-user (tenant) scoping for the dashboard.

Flow:
  GET  /api/auth/login     -> redirect to Google
  GET  /api/auth/callback  -> exchange code, upsert user, set session cookie, redirect to /
  GET  /api/auth/me        -> current user (or 401)
  POST /api/auth/logout    -> clear session

Tenant scoping: every owned row stores `owner` = the user's email. Routers depend
on `current_owner` (the logged-in email) and filter/insert by it.

Config (env, set in calling-agent/.env):
  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET   from Google Cloud console OAuth credentials
  OAUTH_REDIRECT_URI   default https://platform.lescall.dhronai.com/api/auth/callback
  SECRET_KEY           session signing key (set a stable random value in prod)
  PUBLIC_BASE_URL      where to send the user after login (default the domain)
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from shared.db.database import get_db

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
OAUTH_REDIRECT_URI = os.environ.get(
    "OAUTH_REDIRECT_URI", "https://platform.lescall.dhronai.com/api/auth/callback")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://platform.lescall.dhronai.com")
AUTH_ENABLED = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)

router = APIRouter()

_oauth = None


def _get_oauth():
    global _oauth
    if _oauth is None:
        from authlib.integrations.starlette_client import OAuth
        oauth = OAuth()
        oauth.register(
            name="google",
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
        _oauth = oauth
    return _oauth


def _upsert_user(info: dict) -> dict:
    email = info.get("email")
    with get_db() as db:
        row = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if row:
            db.execute("UPDATE users SET name=?, picture=?, google_sub=?, "
                       "last_login=CURRENT_TIMESTAMP WHERE email=?",
                       (info.get("name"), info.get("picture"), info.get("sub"), email))
        else:
            db.execute("INSERT INTO users (email, name, google_sub, picture, last_login) "
                       "VALUES (?,?,?,?,CURRENT_TIMESTAMP)",
                       (email, info.get("name"), info.get("sub"), info.get("picture")))
    return {"email": email, "name": info.get("name"), "picture": info.get("picture")}


# --------------------------- dependencies ----------------------------------
DEFAULT_OWNER = {"email": "default@local", "name": "Local", "picture": None}


def current_user(request: Request) -> dict:
    """Return the logged-in user dict, or 401. Overridable in tests.

    When Google OAuth is NOT configured (AUTH_ENABLED is False), the dashboard
    runs as a single open tenant (today's behaviour) so it is never bricked by
    missing credentials. Set GOOGLE_CLIENT_ID/SECRET to enforce real login.
    """
    if not AUTH_ENABLED:
        return DEFAULT_OWNER
    user = request.session.get("user") if hasattr(request, "session") else None
    if not user:
        raise HTTPException(401, "not authenticated")
    return user


def current_owner(user: dict = Depends(current_user)) -> str:
    """The tenant key (user email) every owned query/insert is scoped by."""
    return user["email"]


# ------------------------------ routes --------------------------------------
@router.get("/config")
def auth_config() -> dict:
    return {"auth_enabled": AUTH_ENABLED}


@router.get("/login")
async def login(request: Request):
    if not AUTH_ENABLED:
        raise HTTPException(503, "Google OAuth not configured (set GOOGLE_CLIENT_ID/SECRET)")
    return await _get_oauth().google.authorize_redirect(request, OAUTH_REDIRECT_URI)


@router.get("/callback")
async def callback(request: Request):
    if not AUTH_ENABLED:
        raise HTTPException(503, "Google OAuth not configured")
    token = await _get_oauth().google.authorize_access_token(request)
    info = token.get("userinfo") or {}
    if not info.get("email"):
        raise HTTPException(400, "no email in Google profile")
    request.session["user"] = _upsert_user(info)
    return RedirectResponse(url=PUBLIC_BASE_URL or "/")


@router.get("/me")
def me(user: dict = Depends(current_user)) -> dict:
    return user


@router.post("/logout")
def logout(request: Request) -> dict:
    request.session.clear()
    return {"ok": True}
