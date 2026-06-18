"""Agent CRUD + pitch maker. Agents are DB-backed AI personas loaded at call time."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from shared.db.database import get_db
from dashboard.api.auth import current_owner

router = APIRouter()


class AgentIn(BaseModel):
    name: str
    company: Optional[str] = None
    persona: Optional[str] = None
    system_prompt: str
    pitch: Optional[str] = None
    language: str = "auto"
    tts_speaker: str = "shubh"
    greeting: Optional[str] = None
    knowledge: Optional[str] = None
    stages: Optional[str] = None     # JSON: per-stage goal/advance_when overrides
    is_active: int = 1


@router.get("")
def list_agents(owner: str = Depends(current_owner)) -> list[dict]:
    with get_db() as db:
        return [dict(r) for r in db.execute(
            "SELECT * FROM agents WHERE owner=? ORDER BY id DESC", (owner,))]


@router.get("/{agent_id}")
def get_agent(agent_id: int, owner: str = Depends(current_owner)) -> dict:
    with get_db() as db:
        row = db.execute("SELECT * FROM agents WHERE id=? AND owner=?",
                         (agent_id, owner)).fetchone()
    if not row:
        raise HTTPException(404, "agent not found")
    return dict(row)


@router.post("")
def create_agent(agent: AgentIn, owner: str = Depends(current_owner)) -> dict:
    fields = {"owner": owner, **agent.model_dump()}
    cols = ", ".join(fields)
    qs = ", ".join("?" for _ in fields)
    with get_db() as db:
        cur = db.execute(f"INSERT INTO agents ({cols}) VALUES ({qs}) RETURNING id",
                         tuple(fields.values()))
        agent_id = cur.fetchone()["id"]
    return get_agent(agent_id, owner)


@router.put("/{agent_id}")
def update_agent(agent_id: int, agent: AgentIn, owner: str = Depends(current_owner)) -> dict:
    fields = agent.model_dump()
    sets = ", ".join(f"{k}=?" for k in fields)
    with get_db() as db:
        cur = db.execute(
            f"UPDATE agents SET {sets}, updated_at=CURRENT_TIMESTAMP WHERE id=? AND owner=?",
            (*fields.values(), agent_id, owner),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "agent not found")
    return get_agent(agent_id, owner)


@router.delete("/{agent_id}")
def delete_agent(agent_id: int, owner: str = Depends(current_owner)) -> dict:
    with get_db() as db:
        db.execute("DELETE FROM agents WHERE id=? AND owner=?", (agent_id, owner))
    return {"deleted": agent_id}
