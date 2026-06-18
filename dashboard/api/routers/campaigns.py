"""Outbound campaigns + leads (DB-backed, per-user). The dialer engine consumes these.

All rows are scoped to the logged-in user via `owner`. Leads are reached only
through a campaign the user owns.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from shared.db.database import get_db
from dashboard.api.auth import current_owner

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


def _owned_campaign(campaign_id: int, owner: str) -> dict:
    with get_db() as db:
        row = db.execute("SELECT * FROM campaigns WHERE id=? AND owner=?",
                         (campaign_id, owner)).fetchone()
    if not row:
        raise HTTPException(404, "campaign not found")
    return dict(row)


@router.get("/campaigns")
def list_campaigns(owner: str = Depends(current_owner)) -> list[dict]:
    with get_db() as db:
        rows = [dict(r) for r in db.execute(
            "SELECT * FROM campaigns WHERE owner=? ORDER BY id DESC", (owner,))]
        for c in rows:
            counts = db.execute(
                "SELECT status, COUNT(*) n FROM leads WHERE campaign_id=? GROUP BY status",
                (c["id"],))
            c["lead_counts"] = {r["status"]: r["n"] for r in counts}
    return rows


@router.post("/campaigns")
def create_campaign(c: CampaignIn, owner: str = Depends(current_owner)) -> dict:
    # If an agent is bound, it must belong to this user.
    if c.agent_id is not None:
        with get_db() as db:
            if not db.execute("SELECT id FROM agents WHERE id=? AND owner=?",
                              (c.agent_id, owner)).fetchone():
                raise HTTPException(400, "agent not found")
    fields = {"owner": owner, **c.model_dump()}
    cols = ", ".join(fields); qs = ", ".join("?" for _ in fields)
    with get_db() as db:
        cur = db.execute(f"INSERT INTO campaigns ({cols}) VALUES ({qs}) RETURNING id",
                         tuple(fields.values()))
        cid = cur.fetchone()["id"]
        row = db.execute("SELECT * FROM campaigns WHERE id=?", (cid,)).fetchone()
    return dict(row)


@router.post("/campaigns/{campaign_id}/status")
def set_status(campaign_id: int, status: str, owner: str = Depends(current_owner)) -> dict:
    if status not in ("draft", "running", "paused", "done"):
        raise HTTPException(400, "invalid status")
    _owned_campaign(campaign_id, owner)
    with get_db() as db:
        db.execute("UPDATE campaigns SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                   (status, campaign_id))
    return {"campaign_id": campaign_id, "status": status}


@router.get("/campaigns/{campaign_id}/leads")
def list_leads(campaign_id: int, owner: str = Depends(current_owner)) -> list[dict]:
    _owned_campaign(campaign_id, owner)
    with get_db() as db:
        return [dict(r) for r in db.execute(
            "SELECT * FROM leads WHERE campaign_id=? ORDER BY id", (campaign_id,))]


@router.post("/campaigns/{campaign_id}/leads")
def add_leads(campaign_id: int, leads: list[LeadIn], owner: str = Depends(current_owner)) -> dict:
    _owned_campaign(campaign_id, owner)
    with get_db() as db:
        added = 0
        for lead in leads:
            db.execute(
                "INSERT INTO leads (campaign_id, name, phone, notes) VALUES (?,?,?,?)",
                (campaign_id, lead.name, lead.phone, lead.notes))
            added += 1
    return {"campaign_id": campaign_id, "added": added}


@router.post("/campaigns/{campaign_id}/dial")
async def dial_campaign(campaign_id: int, dry_run: bool = False,
                        owner: str = Depends(current_owner)) -> dict:
    """Run one dialing pass over the campaign's pending leads via the outbound engine."""
    _owned_campaign(campaign_id, owner)
    from outbound.dialer import run_campaign
    return await run_campaign(campaign_id, dry_run=dry_run)


@router.post("/leads/{lead_id}/dial")
async def dial_lead_now(lead_id: int, dry_run: bool = False,
                        owner: str = Depends(current_owner)) -> dict:
    from outbound.dialer import dial_lead, CALLER_ID, GATEWAY
    row = _owned_lead(lead_id, owner)
    status = await dial_lead(row, CALLER_ID, GATEWAY, dry_run=dry_run)
    return {"lead_id": lead_id, "status": status}


@router.post("/leads/{lead_id}/dispose")
def dispose_lead(lead_id: int, disposition: str, notes: Optional[str] = None,
                 owner: str = Depends(current_owner)) -> dict:
    _owned_lead(lead_id, owner)
    with get_db() as db:
        db.execute("UPDATE leads SET disposition=?, notes=COALESCE(?, notes) WHERE id=?",
                   (disposition, notes, lead_id))
    return {"lead_id": lead_id, "disposition": disposition}


def _owned_lead(lead_id: int, owner: str) -> dict:
    """Fetch a lead, verifying its campaign belongs to the user."""
    with get_db() as db:
        row = db.execute(
            "SELECT l.* FROM leads l JOIN campaigns c ON c.id = l.campaign_id "
            "WHERE l.id=? AND c.owner=?", (lead_id, owner)).fetchone()
    if not row:
        raise HTTPException(404, "lead not found")
    return dict(row)
