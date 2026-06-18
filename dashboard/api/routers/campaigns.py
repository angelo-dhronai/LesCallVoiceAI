"""Outbound campaigns + leads (DB-backed). The dialer engine (outbound/) consumes these.

Endpoints cover CRUD, lead import, status transitions, and a single-lead originate
hook that hands off to the outbound engine. Bulk dialing is driven by the engine loop.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from shared.db.database import get_db

router = APIRouter()


class CampaignIn(BaseModel):
    name: str
    agent_id: Optional[int] = None
    caller_id: Optional[str] = None
    gateway: Optional[str] = None
    max_concurrency: int = 1
    retry_limit: int = 2
    schedule: Optional[str] = None


class LeadIn(BaseModel):
    name: Optional[str] = None
    phone: str
    notes: Optional[str] = None


@router.get("/campaigns")
def list_campaigns() -> list[dict]:
    with get_db() as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM campaigns ORDER BY id DESC")]
        for c in rows:
            counts = db.execute(
                "SELECT status, COUNT(*) n FROM leads WHERE campaign_id=? GROUP BY status",
                (c["id"],))
            c["lead_counts"] = {r["status"]: r["n"] for r in counts}
    return rows


@router.post("/campaigns")
def create_campaign(c: CampaignIn) -> dict:
    fields = c.model_dump()
    cols = ", ".join(fields); qs = ", ".join("?" for _ in fields)
    with get_db() as db:
        cur = db.execute(f"INSERT INTO campaigns ({cols}) VALUES ({qs})", tuple(fields.values()))
        cid = cur.lastrowid
        row = db.execute("SELECT * FROM campaigns WHERE id=?", (cid,)).fetchone()
    return dict(row)


@router.post("/campaigns/{campaign_id}/status")
def set_status(campaign_id: int, status: str) -> dict:
    if status not in ("draft", "running", "paused", "done"):
        raise HTTPException(400, "invalid status")
    with get_db() as db:
        cur = db.execute(
            "UPDATE campaigns SET status=?, updated_at=datetime('now') WHERE id=?",
            (status, campaign_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "campaign not found")
    return {"campaign_id": campaign_id, "status": status}


@router.get("/campaigns/{campaign_id}/leads")
def list_leads(campaign_id: int) -> list[dict]:
    with get_db() as db:
        return [dict(r) for r in db.execute(
            "SELECT * FROM leads WHERE campaign_id=? ORDER BY id", (campaign_id,))]


@router.post("/campaigns/{campaign_id}/leads")
def add_leads(campaign_id: int, leads: list[LeadIn]) -> dict:
    with get_db() as db:
        if not db.execute("SELECT id FROM campaigns WHERE id=?", (campaign_id,)).fetchone():
            raise HTTPException(404, "campaign not found")
        added = 0
        for lead in leads:
            db.execute(
                "INSERT INTO leads (campaign_id, name, phone, notes) VALUES (?,?,?,?)",
                (campaign_id, lead.name, lead.phone, lead.notes))
            added += 1
    return {"campaign_id": campaign_id, "added": added}


@router.post("/campaigns/{campaign_id}/dial")
async def dial_campaign(campaign_id: int, dry_run: bool = False) -> dict:
    """Run one dialing pass over the campaign's pending leads via the outbound engine."""
    from outbound.dialer import run_campaign
    return await run_campaign(campaign_id, dry_run=dry_run)


@router.post("/leads/{lead_id}/dial")
async def dial_lead_now(lead_id: int, dry_run: bool = False) -> dict:
    from outbound.dialer import dial_lead, CALLER_ID, GATEWAY
    with get_db() as db:
        row = db.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not row:
        raise HTTPException(404, "lead not found")
    status = await dial_lead(dict(row), CALLER_ID, GATEWAY, dry_run=dry_run)
    return {"lead_id": lead_id, "status": status}


@router.post("/leads/{lead_id}/dispose")
def dispose_lead(lead_id: int, disposition: str, notes: Optional[str] = None) -> dict:
    with get_db() as db:
        cur = db.execute(
            "UPDATE leads SET disposition=?, notes=COALESCE(?, notes) WHERE id=?",
            (disposition, notes, lead_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "lead not found")
    return {"lead_id": lead_id, "disposition": disposition}
