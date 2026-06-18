"""Plivo trunks + phone numbers, with automatic FreeSWITCH config generation.

Self-service per user: a user registers their own trunk + DID; POST .../apply
writes the FreeSWITCH gateway + inbound dialplan and reloads FS. All rows are
scoped to the logged-in user via `owner`.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from shared.db.database import get_db
from shared import freeswitch_config as fsc
from shared import kamailio_acl
from dashboard.api.auth import current_owner

router = APIRouter()


# ----------------------------- trunks --------------------------------------
class TrunkIn(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    name: str
    provider: Optional[str] = None   # any SIP carrier (Plivo, Twilio, Telnyx, …) — label only
    sip_domain: str
    proxy: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    register: int = 0
    caller_id: Optional[str] = None
    provider_ips: Optional[str] = None   # comma-separated CIDRs to allow (ACL)
    transport: str = "udp"


@router.get("/telephony/server-info")
def telephony_server_info(owner: str = Depends(current_owner)) -> dict:
    """Our SIP endpoint + ACL the user configures on their carrier (any SIP provider)."""
    with get_db() as db:
        cidrs = _all_provider_cidrs(db)
    return fsc.server_sip_info(cidrs)


@router.get("/trunks")
def list_trunks(owner: str = Depends(current_owner)) -> list[dict]:
    with get_db() as db:
        return [dict(r) for r in db.execute(
            "SELECT * FROM trunks WHERE owner=? ORDER BY id DESC", (owner,))]


@router.post("/trunks")
def create_trunk(trunk: TrunkIn, owner: str = Depends(current_owner)) -> dict:
    # Trunk name is globally unique (FS gateway name); namespace by owner to avoid clashes.
    fields = {"owner": owner, **trunk.model_dump()}
    fields["name"] = f"{_slug(owner)}_{fields['name']}"
    cols = ", ".join(fields)
    qs = ", ".join("?" for _ in fields)
    with get_db() as db:
        cur = db.execute(f"INSERT INTO trunks ({cols}) VALUES ({qs}) RETURNING id",
                         tuple(fields.values()))
        tid = cur.fetchone()["id"]
        row = db.execute("SELECT * FROM trunks WHERE id=?", (tid,)).fetchone()
        all_cidrs = _all_provider_cidrs(db)
    # Allow this carrier's IPs at the Kamailio edge (provider-agnostic).
    kamailio_acl.sync_carrier_acl(all_cidrs)
    return dict(row)


@router.get("/trunks/{trunk_id}/instructions")
def trunk_instructions(trunk_id: int, owner: str = Depends(current_owner)) -> dict:
    return fsc.sip_trunk_instructions(_trunk(trunk_id, owner))


@router.post("/trunks/{trunk_id}/apply")
def apply_trunk(trunk_id: int, owner: str = Depends(current_owner)) -> dict:
    """Generate + write FreeSWITCH gateway and inbound dialplan, then reload FS."""
    trunk = _trunk(trunk_id, owner)
    with get_db() as db:
        numbers = [dict(r) for r in db.execute(
            "SELECT * FROM phone_numbers WHERE trunk_id=? AND owner=?", (trunk_id, owner))]
        all_cidrs = _all_provider_cidrs(db)
    result = fsc.apply_config(trunk, numbers, reload=True, all_provider_cidrs=all_cidrs)
    # Propagate carrier IPs to the Kamailio edge allow-list (runtime reload, no restart).
    kam = kamailio_acl.sync_carrier_acl(all_cidrs)
    result["kamailio_acl"] = kam
    status = "applied" if result.get("reloaded") else "error"
    with get_db() as db:
        db.execute(
            "UPDATE trunks SET config_status=?, last_applied_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, trunk_id))
        db.execute(
            "UPDATE phone_numbers SET config_status=?, last_applied_at=CURRENT_TIMESTAMP "
            "WHERE trunk_id=?", (status, trunk_id))
    return {"status": status, **result}


# -------------------------- phone numbers ----------------------------------
class NumberIn(BaseModel):
    number: str
    trunk_id: Optional[int] = None
    inbound_agent_id: Optional[int] = None
    direction: str = "both"
    label: Optional[str] = None


@router.get("/numbers")
def list_numbers(owner: str = Depends(current_owner)) -> list[dict]:
    with get_db() as db:
        return [dict(r) for r in db.execute(
            "SELECT * FROM phone_numbers WHERE owner=? ORDER BY id DESC", (owner,))]


@router.post("/numbers")
def create_number(num: NumberIn, owner: str = Depends(current_owner)) -> dict:
    # Validate the referenced trunk/agent belong to this user.
    if num.trunk_id is not None:
        _trunk(num.trunk_id, owner)
    fields = {"owner": owner, **num.model_dump()}
    cols = ", ".join(fields)
    qs = ", ".join("?" for _ in fields)
    with get_db() as db:
        cur = db.execute(
            f"INSERT INTO phone_numbers ({cols}) VALUES ({qs}) RETURNING id",
            tuple(fields.values()))
        nid = cur.fetchone()["id"]
        row = db.execute("SELECT * FROM phone_numbers WHERE id=?", (nid,)).fetchone()
    return dict(row)


@router.delete("/numbers/{number_id}")
def delete_number(number_id: int, owner: str = Depends(current_owner)) -> dict:
    with get_db() as db:
        db.execute("DELETE FROM phone_numbers WHERE id=? AND owner=?", (number_id, owner))
    return {"deleted": number_id}


def _trunk(trunk_id: int, owner: str) -> dict:
    with get_db() as db:
        row = db.execute("SELECT * FROM trunks WHERE id=? AND owner=?",
                         (trunk_id, owner)).fetchone()
    if not row:
        raise HTTPException(404, "trunk not found")
    return dict(row)


def _slug(email: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in email.split("@")[0])[:24]


def _all_provider_cidrs(db) -> list[str]:
    """Aggregate provider CIDRs across ALL trunks (the FS ACL is one global list)."""
    cidrs: list[str] = []
    for r in db.execute("SELECT provider_ips FROM trunks WHERE provider_ips IS NOT NULL"):
        cidrs += [c.strip() for c in (dict(r)["provider_ips"] or "").split(",") if c.strip()]
    return sorted(set(cidrs))
