"""Calls, transcripts, AI summaries, and analytics.

Reads existing on-disk recordings (recordings/<conn_id>/) — no pipeline changes.
Summaries are generated on demand via OpenAI and cached in the `calls` table.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from shared.db.database import get_db, _REPO_ROOT
from shared.transcript import parse_transcript_file
from shared.summarizer import summarize_turns

router = APIRouter()
RECORDINGS = _REPO_ROOT / "recordings"


def _list_recording_dirs() -> list[Path]:
    if not RECORDINGS.exists():
        return []
    return sorted([d for d in RECORDINGS.iterdir() if d.is_dir()],
                  key=lambda p: p.stat().st_mtime, reverse=True)


@router.get("/calls")
def list_calls(limit: int = 100) -> list[dict]:
    out = []
    for d in _list_recording_dirs()[:limit]:
        meta = {}
        mp = d / "metadata.json"
        if mp.exists():
            try:
                meta = json.loads(mp.read_text())
            except Exception:
                meta = {}
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
    return out


@router.get("/calls/{conn_id}/transcript")
def get_transcript(conn_id: str) -> dict:
    d = RECORDINGS / conn_id
    tp = d / "transcript.txt"
    if not tp.exists():
        raise HTTPException(404, "transcript not found")
    return {"conn_id": conn_id, "turns": parse_transcript_file(tp)}


@router.post("/calls/{conn_id}/summary")
def make_summary(conn_id: str) -> dict:
    """Generate (or regenerate) the AI summary and cache it in the calls table."""
    d = RECORDINGS / conn_id
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
def analytics() -> dict:
    """Headline numbers for the dashboard overview."""
    dirs = _list_recording_dirs()
    total = len(dirs)
    with_transcript = sum(1 for d in dirs if (d / "transcript.txt").exists())
    with get_db() as db:
        by_outcome = {r["outcome"] or "unknown": r["n"] for r in db.execute(
            "SELECT outcome, COUNT(*) n FROM calls GROUP BY outcome")}
        by_sentiment = {r["sentiment"] or "unknown": r["n"] for r in db.execute(
            "SELECT sentiment, COUNT(*) n FROM calls GROUP BY sentiment")}
        leads = db.execute("SELECT COUNT(*) n FROM leads").fetchone()["n"]
        campaigns = db.execute("SELECT COUNT(*) n FROM campaigns").fetchone()["n"]
        agents = db.execute("SELECT COUNT(*) n FROM agents").fetchone()["n"]
    return {
        "total_recordings": total,
        "with_transcript": with_transcript,
        "summaries_by_outcome": by_outcome,
        "summaries_by_sentiment": by_sentiment,
        "leads": leads,
        "campaigns": campaigns,
        "agents": agents,
    }
