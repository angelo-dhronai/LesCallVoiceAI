"""Plivo trunks + phone numbers, with automatic FreeSWITCH config generation.

Add a trunk and a DID here; POST .../apply writes the FreeSWITCH gateway + inbound
dialplan and reloads FS. GET .../instructions shows the SIP trunk config the operator
must create on the provider side.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from shared.db.database import get_db
from shared import freeswitch_config as fsc

router = APIRouter()


# ----------------------------- trunks --------------------------------------
class TrunkIn(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    name: str
    provider: str = "plivo"
    sip_domain: str
    proxy: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    register: int = 0
    caller_id: Optional[str] = None


@router.get("/trunks")
def list_trunks() -> list[dict]:
    with get_db() as db:
        return [dict(r) for r in db.execute("SELECT * FROM trunks ORDER BY id DESC")]


@router.post("/trunks")
def create_trunk(trunk: TrunkIn) -> dict:
    fields = trunk.model_dump()
    cols = ", ".join(fields)
    qs = ", ".join("?" for _ in fields)
    with get_db() as db:
        cur = db.execute(f"INSERT INTO trunks ({cols}) VALUES ({qs})", tuple(fields.values()))
        tid = cur.lastrowid
        row = db.execute("SELECT * FROM trunks WHERE id=?", (tid,)).fetchone()
    return dict(row)


@router.get("/trunks/{trunk_id}/instructions")
def trunk_instructions(trunk_id: int) -> dict:
    trunk = _trunk(trunk_id)
    return fsc.sip_trunk_instructions(trunk)


@router.post("/trunks/{trunk_id}/apply")
def apply_trunk(trunk_id: int) -> dict:
    """Generate + write FreeSWITCH gateway and inbound dialplan, then reload FS."""
    trunk = _trunk(trunk_id)
    with get_db() as db:
        numbers = [dict(r) for r in db.execute(
            "SELECT * FROM phone_numbers WHERE trunk_id=?", (trunk_id,))]
    result = fsc.apply_config(trunk, numbers, reload=True)
    status = "applied" if result.get("reloaded") else "error"
    with get_db() as db:
        db.execute(
            "UPDATE trunks SET config_status=?, last_applied_at=datetime('now') WHERE id=?",
            (status, trunk_id))
        db.execute(
            "UPDATE phone_numbers SET config_status=?, last_applied_at=datetime('now') "
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
def list_numbers() -> list[dict]:
    with get_db() as db:
        return [dict(r) for r in db.execute("SELECT * FROM phone_numbers ORDER BY id DESC")]


@router.post("/numbers")
def create_number(num: NumberIn) -> dict:
    fields = num.model_dump()
    cols = ", ".join(fields)
    qs = ", ".join("?" for _ in fields)
    with get_db() as db:
        cur = db.execute(
            f"INSERT INTO phone_numbers ({cols}) VALUES ({qs})", tuple(fields.values()))
        nid = cur.lastrowid
        row = db.execute("SELECT * FROM phone_numbers WHERE id=?", (nid,)).fetchone()
    return dict(row)


@router.delete("/numbers/{number_id}")
def delete_number(number_id: int) -> dict:
    with get_db() as db:
        db.execute("DELETE FROM phone_numbers WHERE id=?", (number_id,))
    return {"deleted": number_id}


def _trunk(trunk_id: int) -> dict:
    with get_db() as db:
        row = db.execute("SELECT * FROM trunks WHERE id=?", (trunk_id,)).fetchone()
    if not row:
        raise HTTPException(404, "trunk not found")
    return dict(row)
