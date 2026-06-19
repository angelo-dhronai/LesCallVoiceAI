"""Calls, transcripts, AI summaries, and analytics — scoped per tenant.

Recordings live on disk (recordings/<conn_id>/). Each recording's metadata.json
carries the `owner` (user email) of the agent that handled the call, written by
the telephony pipeline. Every endpoint here filters by that owner so a tenant can
only ever see their own calls. Summaries are generated on demand via OpenAI and
cached in the `calls` table.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from shared.db.database import get_db, _REPO_ROOT
from shared.transcript import parse_transcript_file
from shared.summarizer import summarize_turns
from dashboard.api.auth import current_owner

router = APIRouter()
RECORDINGS = _REPO_ROOT / "recordings"

# Platform admins (comma-separated emails) may see ALL calls, including legacy
# recordings made before per-tenant ownership existed. Everyone else sees only
# the recordings tagged with their own email.
ADMINS = {e.strip().lower() for e in os.environ.get("LESCALL_ADMINS", "").split(",") if e.strip()}


def _list_recording_dirs() -> list[Path]:
    if not RECORDINGS.exists():
        return []
    return sorted([d for d in RECORDINGS.iterdir() if d.is_dir()],
                  key=lambda p: p.stat().st_mtime, reverse=True)


def _read_meta(d: Path) -> dict:
    mp = d / "metadata.json"
    if mp.exists():
        try:
            return json.loads(mp.read_text())
        except Exception:
            return {}
    return {}


def _can_view(meta: dict, owner: str) -> bool:
    """A user may view a recording only if they own it (or they're an admin).

    Untagged legacy recordings (no owner in metadata) are visible to admins only,
    so a fresh tenant never sees calls that aren't theirs.
    """
    if owner.lower() in ADMINS:
        return True
    return (meta.get("owner") or "").lower() == owner.lower()


def _owned_dir(conn_id: str, owner: str) -> Path:
    """Resolve a recording dir, enforcing tenant ownership (404 if not visible)."""
    d = RECORDINGS / conn_id
    if not d.exists() or not _can_view(_read_meta(d), owner):
        raise HTTPException(404, "call not found")
    return d


@router.get("/calls")
def list_calls(limit: int = 100, owner: str = Depends(current_owner)) -> list[dict]:
    out = []
    for d in _list_recording_dirs():
        meta = _read_meta(d)
        if not _can_view(meta, owner):
            continue
        with get_db() as db:
            row = db.execute(
                "SELECT summary, sentiment, outcome FROM calls WHERE conn_id=?", (d.name,)
            ).fetchone()
        out.append({
            "conn_id": d.name,
            "has_audio": (d / "caller_audio.wav").exists(),
            "has_transcript": (d / "transcript.txt").exists(),
            "metadata": meta,
            "summary": row["summary"] if row else None,
            "sentiment": row["sentiment"] if row else None,
            "outcome": row["outcome"] if row else None,
        })
        if len(out) >= limit:
            break
    return out


@router.get("/calls/{conn_id}/transcript")
def get_transcript(conn_id: str, owner: str = Depends(current_owner)) -> dict:
    d = _owned_dir(conn_id, owner)
    tp = d / "transcript.txt"
    if not tp.exists():
        raise HTTPException(404, "transcript not found")
    return {"conn_id": conn_id, "turns": parse_transcript_file(tp)}


@router.post("/calls/{conn_id}/summary")
def make_summary(conn_id: str, owner: str = Depends(current_owner)) -> dict:
    """Generate (or regenerate) the AI summary and cache it in the calls table."""
    d = _owned_dir(conn_id, owner)
    tp = d / "transcript.txt"
    if not tp.exists():
        raise HTTPException(404, "transcript not found")
    turns = parse_transcript_file(tp)
    try:
        result = summarize_turns(turns)
    except Exception as e:
        raise HTTPException(502, f"summary failed: {e}")

    with get_db() as db:
        exists = db.execute("SELECT id FROM calls WHERE conn_id=?", (conn_id,)).fetchone()
        if exists:
            db.execute(
                "UPDATE calls SET summary=?, sentiment=?, outcome=? WHERE conn_id=?",
                (result["summary"], result["sentiment"], result["outcome"], conn_id))
        else:
            db.execute(
                "INSERT INTO calls (conn_id, direction, summary, sentiment, outcome) "
                "VALUES (?,?,?,?,?)",
                (conn_id, "inbound", result["summary"], result["sentiment"], result["outcome"]))
    return {"conn_id": conn_id, **result}


@router.get("/analytics")
def analytics(owner: str = Depends(current_owner)) -> dict:
    """Headline numbers for the dashboard overview, scoped to the logged-in user.

    Recording counts are restricted to calls this tenant owns (admins see all),
    matching the per-tenant visibility enforced on /calls.
    """
    visible = [d for d in _list_recording_dirs() if _can_view(_read_meta(d), owner)]
    total = len(visible)
    with_transcript = sum(1 for d in visible if (d / "transcript.txt").exists())
    conn_ids = [d.name for d in visible]
    with get_db() as db:
        by_outcome: dict[str, int] = {}
        by_sentiment: dict[str, int] = {}
        # Aggregate cached summaries only for this tenant's visible recordings.
        if conn_ids:
            placeholders = ",".join("?" for _ in conn_ids)
            for r in db.execute(
                f"SELECT outcome, COUNT(*) n FROM calls WHERE conn_id IN ({placeholders}) "
                "GROUP BY outcome", tuple(conn_ids)):
                by_outcome[r["outcome"] or "unknown"] = r["n"]
            for r in db.execute(
                f"SELECT sentiment, COUNT(*) n FROM calls WHERE conn_id IN ({placeholders}) "
                "GROUP BY sentiment", tuple(conn_ids)):
                by_sentiment[r["sentiment"] or "unknown"] = r["n"]
        leads = db.execute(
            "SELECT COUNT(*) n FROM leads l JOIN campaigns c ON c.id=l.campaign_id "
            "WHERE c.owner=?", (owner,)).fetchone()["n"]
        campaigns = db.execute(
            "SELECT COUNT(*) n FROM campaigns WHERE owner=?", (owner,)).fetchone()["n"]
        agents = db.execute(
            "SELECT COUNT(*) n FROM agents WHERE owner=?", (owner,)).fetchone()["n"]
    return {
        "total_recordings": total,
        "with_transcript": with_transcript,
        "summaries_by_outcome": by_outcome,
        "summaries_by_sentiment": by_sentiment,
        "leads": leads,
        "campaigns": campaigns,
        "agents": agents,
    }
