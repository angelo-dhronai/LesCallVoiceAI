"""Dashboard auth: Google OAuth sessions + the `current_owner` tenant boundary.

The multi-tenant rule (docs/MULTITENANT_BYON_PLAN.md §1): every tenant-facing
endpoint takes `owner: str = Depends(current_owner)` and filters by it. This
module provides that dependency plus the login plumbing.

Two modes, switched by whether GOOGLE_CLIENT_ID is configured:

* SINGLE-TENANT FALLBACK (default, no env set): current_owner() returns
  LESCALL_DEFAULT_OWNER (tech@dhronai.com). Nothing requires login — the
  dashboard behaves exactly as before multi-tenancy. This is deliberate: the
  static UI has no login page yet, so enforcement can't precede it.
* MULTI-TENANT (GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET set): /auth/login →
  Google consent → /auth/callback verifies the code, upserts a users row in
  the shared DB, and sets a signed session cookie. current_owner() then
  requires a valid session (401 otherwise).

Sessions are stateless signed cookies (HMAC-SHA256 over a JSON payload with
expiry) — no server-side session store to lose on restart. Secret comes from
LESCALL_SESSION_SECRET or a generated dashboard/.session_secret file.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

logger = logging.getLogger("dashboard.auth")

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# OAuth creds may live in calling-agent/.env alongside the other keys; load it
# BEFORE the env reads below (mount order in server.py must not matter).
try:
    from dotenv import load_dotenv
    load_dotenv(_REPO_ROOT / "calling-agent" / ".env")
except Exception:  # pragma: no cover - dotenv is a venv dep
    pass

DEFAULT_OWNER = os.environ.get("LESCALL_DEFAULT_OWNER", "tech@dhronai.com")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
# Public callback URL; must be registered in the Google OAuth client.
OAUTH_REDIRECT_URL = os.environ.get(
    "OAUTH_REDIRECT_URL", "http://localhost:8080/auth/callback")
SESSION_COOKIE = "lescall_session"
STATE_COOKIE = "lescall_oauth_state"
SESSION_TTL_S = int(os.environ.get("LESCALL_SESSION_TTL_S", str(7 * 24 * 3600)))

_SECRET_FILE = Path(__file__).parent / ".session_secret"


def _session_secret() -> bytes:
    env = os.environ.get("LESCALL_SESSION_SECRET")
    if env:
        return env.encode("utf-8")
    try:
        if _SECRET_FILE.exists():
            return _SECRET_FILE.read_bytes()
        secret = secrets.token_bytes(32)
        _SECRET_FILE.write_bytes(secret)
        _SECRET_FILE.chmod(0o600)
        return secret
    except Exception as exc:  # unwritable dir — sessions won't survive restart
        logger.warning("session secret not persistable (%r); using process-local", exc)
        global _FALLBACK_SECRET
        try:
            return _FALLBACK_SECRET
        except NameError:
            _FALLBACK_SECRET = secrets.token_bytes(32)
            return _FALLBACK_SECRET


def oauth_enabled() -> bool:
    """True when ANY OAuth env is set — a partial configuration fails CLOSED
    (sessions required, login unusable until fixed) rather than silently
    falling back to unauthenticated single-tenant mode."""
    return bool(GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET)


def _oauth_fully_configured() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def _cookie_secure() -> bool:
    return OAUTH_REDIRECT_URL.startswith("https://")


def is_admin(email: str) -> bool:
    """Roles are flattened (2026-07-15): NO account has admin status — every
    user gets the same owner-scoped view and the same dashboard options. The
    function is kept as the single choke point so the remaining admin-only
    branches (legacy single-tenant endpoints, see-all-rows scopes, the
    platform-fallback-agent controls) stay uniformly dead instead of being
    half-removed. Legacy pre-tenancy RESOURCES (the shared Plivo trunk,
    unowned rows) still belong to DEFAULT_OWNER — that is data ownership,
    not a role."""
    return False


def allowed_emails() -> set[str]:
    """Who may log in. Comma-separated LESCALL_ALLOWED_EMAILS; "*" opens
    signup to any verified Google account.

    Defaults to DEFAULT_OWNER only: the legacy dashboard endpoints are not yet
    owner-scoped, so open signup would let any Google account see this
    tenant's data. Widen deliberately, per email, until that follow-on lands.
    """
    raw = os.environ.get("LESCALL_ALLOWED_EMAILS", DEFAULT_OWNER)
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


# ---------------------------------------------------------------------------
# Signed-cookie sessions
# ---------------------------------------------------------------------------

def _sign(payload: bytes) -> str:
    return hmac.new(_session_secret(), payload, hashlib.sha256).hexdigest()


def make_session_cookie(email: str, name: str = "", picture: str = "") -> str:
    payload = json.dumps({
        "email": email, "name": name, "picture": picture,
        "exp": int(time.time()) + SESSION_TTL_S,
    }, separators=(",", ":")).encode("utf-8")
    b64 = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"{b64}.{_sign(payload)}"


def read_session_cookie(cookie: str | None) -> dict | None:
    """Verified session payload, or None for missing/tampered/expired."""
    if not cookie or "." not in cookie:
        return None
    b64, sig = cookie.rsplit(".", 1)
    try:
        payload = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
    except Exception:
        return None
    if not hmac.compare_digest(_sign(payload), sig):
        return None
    try:
        data = json.loads(payload)
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get("email"):
        return None
    if int(data.get("exp", 0)) < time.time():
        return None
    return data


# ---------------------------------------------------------------------------
# The tenant boundary
# ---------------------------------------------------------------------------

def current_owner(request: Request) -> str:
    """FastAPI dependency: the logged-in tenant's email.

    Single-tenant fallback returns DEFAULT_OWNER so existing deployments keep
    working before OAuth is configured; once configured, a valid session is
    required (401 otherwise).
    """
    if not oauth_enabled():
        return DEFAULT_OWNER
    session = read_session_cookie(request.cookies.get(SESSION_COOKIE))
    if not session:
        raise HTTPException(status_code=401, detail="login required")
    return session["email"]


def _upsert_user(email: str, name: str, google_sub: str, picture: str) -> None:
    """Record the login in the shared DB. Best-effort — login must not fail
    because the DB is momentarily unavailable."""
    try:
        from shared.db.database import get_db
        with get_db() as db:
            db.execute(
                "INSERT INTO users (email, name, google_sub, picture) VALUES (?,?,?,?) "
                "ON CONFLICT (email) DO NOTHING",
                (email, name, google_sub, picture))
            db.execute(
                "UPDATE users SET last_login=CURRENT_TIMESTAMP, name=?, picture=? "
                "WHERE email=?",
                (name, picture, email))
    except Exception as exc:
        logger.warning("user upsert failed for %s: %r", email, exc)


# ---------------------------------------------------------------------------
# OAuth endpoints
# ---------------------------------------------------------------------------

router = APIRouter()


@router.get("/api/auth/me")
def me(request: Request):
    if not oauth_enabled():
        return {"mode": "single-tenant", "email": DEFAULT_OWNER,
                "login_required": False}
    session = read_session_cookie(request.cookies.get(SESSION_COOKIE))
    if not session:
        return JSONResponse({"mode": "multi-tenant", "login_required": True}, status_code=401)
    return {"mode": "multi-tenant", "login_required": False,
            "email": session["email"], "name": session.get("name", ""),
            "picture": session.get("picture", "")}


# ---------------------------------------------------------------------------
# Account profile (Settings page). Owner-scoped: each user reads/writes only
# their own users row. `phone` is where BYON test calls are placed.
# ---------------------------------------------------------------------------

@router.get("/api/auth/profile")
def get_profile(request: Request):
    owner = current_owner(request)
    row = None
    try:
        from shared.db.database import get_db
        with get_db() as db:
            row = db.execute(
                "SELECT name, phone, company, picture FROM users WHERE email=?",
                (owner,)).fetchone()
    except Exception as exc:
        logger.warning("profile read failed for %s: %r", owner, exc)
    return {
        "email": owner,
        "name": (row["name"] if row else "") or "",
        "phone": (row["phone"] if row else "") or "",
        "company": (row["company"] if row else "") or "",
        "picture": (row["picture"] if row else "") or "",
    }


@router.put("/api/auth/profile")
async def put_profile(request: Request):
    owner = current_owner(request)
    try:
        body = await request.json()
        assert isinstance(body, dict)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    name = str(body.get("name") or "").strip()[:120]
    company = str(body.get("company") or "").strip()[:120]
    phone_raw = str(body.get("phone") or "").strip()
    phone_digits = "".join(ch for ch in phone_raw if ch.isdigit())
    if phone_raw and len(phone_digits) < 7:
        raise HTTPException(status_code=400,
                            detail="phone must have at least 7 digits (include country code)")
    # Store digits-only with country code so fs_cli originate can dial it as-is.
    phone = phone_digits
    try:
        from shared.db.database import get_db
        with get_db() as db:
            existing = db.execute("SELECT id FROM users WHERE email=?", (owner,)).fetchone()
            if existing:
                db.execute("UPDATE users SET name=?, phone=?, company=? WHERE email=?",
                           (name, phone, company, owner))
            else:
                db.execute("INSERT INTO users (email, name, phone, company) VALUES (?,?,?,?)",
                           (owner, name, phone, company))
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("profile save failed for %s: %r", owner, exc)
        raise HTTPException(status_code=500, detail="could not save profile")
    logger.info("profile updated for %s", owner)
    return {"status": "saved", "email": owner, "name": name, "phone": phone,
            "company": company}


@router.get("/auth/login")
def login():
    if not _oauth_fully_configured():
        return JSONResponse(
            {"error": "OAuth not (fully) configured — set BOTH GOOGLE_CLIENT_ID "
                      "and GOOGLE_CLIENT_SECRET"},
            status_code=503)
    state = secrets.token_urlsafe(24)
    params = urllib.parse.urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": OAUTH_REDIRECT_URL,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    })
    resp = RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")
    resp.set_cookie(STATE_COOKIE, state, max_age=600, httponly=True, samesite="lax",
                    secure=_cookie_secure())
    return resp


@router.get("/auth/callback")
def callback(request: Request, code: str = "", state: str = ""):
    if not _oauth_fully_configured():
        return JSONResponse({"error": "OAuth not configured"}, status_code=503)
    if not code or not state or state != request.cookies.get(STATE_COOKIE):
        return RedirectResponse("/login?error=state")

    body = urllib.parse.urlencode({
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": OAUTH_REDIRECT_URL,
        "grant_type": "authorization_code",
    }).encode("ascii")
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token", data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=10) as fh:
            token = json.loads(fh.read().decode("utf-8"))
    except Exception as exc:
        logger.error("OAuth code exchange failed: %r", exc)
        return RedirectResponse("/login?error=exchange")

    # The id_token arrives directly from Google's token endpoint over TLS, so
    # decoding its payload without JWKS signature verification is sufficient.
    id_token = token.get("id_token", "")
    try:
        payload_b64 = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(
            payload_b64 + "=" * (-len(payload_b64) % 4)))
    except Exception:
        return RedirectResponse("/login?error=exchange")

    email = claims.get("email", "")
    if not email or not claims.get("email_verified", False):
        return RedirectResponse("/login?error=unverified")

    allowed = allowed_emails()
    if "*" not in allowed and email.lower() not in allowed:
        logger.warning("login rejected (not in LESCALL_ALLOWED_EMAILS): %s", email)
        return RedirectResponse("/login?error=not_authorized")

    _upsert_user(email, claims.get("name", ""), claims.get("sub", ""),
                 claims.get("picture", ""))
    resp = RedirectResponse("/")
    resp.set_cookie(
        SESSION_COOKIE, make_session_cookie(email, claims.get("name", ""),
                                            claims.get("picture", "")),
        max_age=SESSION_TTL_S, httponly=True, samesite="lax",
        secure=_cookie_secure())
    resp.delete_cookie(STATE_COOKIE)
    logger.info("login: %s", email)
    try:
        from shared.audit import record
        record(email, "login", ip=request.client.host if request.client else None)
    except Exception:
        pass
    return resp


@router.get("/auth/logout")
def logout():
    resp = RedirectResponse("/login" if oauth_enabled() else "/")
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@router.get("/login")
def login_page(request: Request):
    """The branded login page (static/login.html)."""
    if not oauth_enabled():
        return RedirectResponse("/")  # single-tenant mode: no login wall
    if read_session_cookie(request.cookies.get(SESSION_COOKIE)):
        return RedirectResponse("/")
    page = Path(__file__).parent / "static" / "login.html"
    if page.exists():
        return FileResponse(str(page), media_type="text/html")
    return RedirectResponse("/auth/login")  # page missing — straight to Google


# ---------------------------------------------------------------------------
# The login wall
# ---------------------------------------------------------------------------

# Reachable without a session: the login surface itself + static assets
# (login.html needs its fonts/styles) + the session probe.
# /api/recordings/dl/ is the public tokenized recording download: the 256-bit
# random token in the path is the credential, so no session is required.
_OPEN_PREFIXES = ("/auth/", "/login", "/static/", "/api/auth/me", "/favicon",
                  "/api/recordings/dl/")

# API prefixes tenants may use — every handler under these performs its own
# per-owner scoping. EVERYTHING ELSE under /api/ is DENIED for every session
# (roles are flattened; there are no admins): the legacy stores (runtime
# control, keys, follow-up automation…) are single-tenant surfaces with no
# owner column, so exposing them to any session would leak the founding
# tenant's data. Deny by default: a newly added legacy endpoint is
# automatically unreachable until it is owner-scoped and listed here.
_TENANT_API_PREFIXES = (
    "/api/auth/",
    "/api/agents",       # owner-scoped in agent_builder_api
    "/api/copilot",      # owner-scoped agent creation chat
    "/api/trunks",
    "/api/numbers",
    "/api/telephony",
    # Per-tenant contacts + call history (Part B Phase 3). Every endpoint under
    # these is owner-scoped: contacts by owner column; recordings/history/
    # analysis by the calls-table folder→owner map (legacy folders = admin);
    # live calls by the snapshot's owner field (see _scope_live_state).
    "/api/outbound",
    "/api/calls/history",
    "/api/calls/recordings",
    "/api/calls/transcripts",
    "/api/calls/active",
    "/api/calls/live-state",
    "/api/usage/minutes",   # billing view; owner-scoped inside the endpoint
    "/api/analysis",
    "/api/analytics/conversation",
    # Homepage KPIs. Was deliberately absent — every figure came from the
    # credit-log JSONL, which has no owner column, so the page would have shown
    # platform-wide totals to every tenant. Now sourced from the calls table
    # via _owner_call_stats() and scoped, so it can be exposed (2026-07-22).
    "/api/overview",
    # Intelligence layer (deploy/PLAN-intelligence-layer.md): every endpoint
    # under /api/intel is owner-scoped via the current_owner dependency.
    "/api/intel",
    # Shared-infrastructure health (pipeline/telephony up-down) — not tenant
    # data, and every tenant legitimately needs to see whether the platform is
    # up. Returns only service running-flags + ports, no per-call info.
    "/api/health",
)


def install_login_gate(app) -> None:
    """Session wall + tenancy gate whenever OAuth is enabled. A complete no-op
    in single-tenant fallback mode, so installing it is always safe."""

    @app.middleware("http")
    async def _login_gate(request: Request, call_next):
        if oauth_enabled():
            path = request.url.path
            if not any(path.startswith(p) for p in _OPEN_PREFIXES):
                session = read_session_cookie(request.cookies.get(SESSION_COOKIE))
                if not session:
                    accepts_html = "text/html" in (request.headers.get("accept") or "")
                    if not path.startswith("/api/") and (path == "/" or accepts_html):
                        return RedirectResponse("/login")
                    return JSONResponse({"detail": "login required"}, status_code=401)
                if (path.startswith("/api/")
                        and not any(path.startswith(p) for p in _TENANT_API_PREFIXES)):
                    return JSONResponse(
                        {"detail": "not available"}, status_code=403)
        return await call_next(request)
