"""lescall dashboard API — FastAPI backend.

Run:  uvicorn dashboard.api.main:app --host 0.0.0.0 --port 8080
Initializes the DB on startup; mounts feature routers under /api.
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from shared.db.database import init_db
from dashboard.api import auth
from dashboard.api.routers import agents, trunks, calls, campaigns, live, knowledge

app = FastAPI(title="lescall dashboard API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Signed session cookie for OAuth login state + the logged-in user.
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SECRET_KEY", "dev-insecure-change-me"),
    same_site="lax",
    https_only=os.environ.get("SESSION_HTTPS_ONLY", "0") == "1",
)


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(agents.router, prefix="/api/agents", tags=["agents"])
app.include_router(trunks.router, prefix="/api", tags=["telephony"])
app.include_router(calls.router, prefix="/api", tags=["calls"])
app.include_router(campaigns.router, prefix="/api", tags=["campaigns"])
app.include_router(live.router, prefix="/api", tags=["live"])
app.include_router(knowledge.router, prefix="/api", tags=["knowledge"])
