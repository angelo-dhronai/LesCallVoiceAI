"""Seed / migrate existing data into the DB.

- Creates the default "Vishal Patel / Pramukh Group" agent (idempotent by name).
- Imports dashboard/outbound_contacts.json into a default "Legacy Outbound" campaign.

Safe to re-run: skips the agent if it already exists and upserts leads by phone.
Run:  python3 -m shared.db.seed
"""
from __future__ import annotations

import json
from pathlib import Path

from shared.db.database import get_db, init_db, _REPO_ROOT

DEFAULT_SYSTEM_PROMPT = (
    "You are Vishal Patel, a proactive sales agent for Pramukh Group, one of "
    "Gujarat's most trusted real estate developers. You are warm, concise, and "
    "bilingual (English / Hindi / Gujarati). Detect the caller's language and "
    "mirror it. Drive the conversation through: GREETING -> DISCOVERY -> "
    "PROJECT_MATCH -> INVITE_VISIT -> CONFIRMATION -> CLOSING. Keep every reply "
    "to 1-2 spoken sentences. Never re-ask a confirmed detail. Never read a phone "
    "number back verbatim."
)


def seed_default_agent() -> int:
    with get_db() as db:
        row = db.execute("SELECT id FROM agents WHERE name=?", ("Vishal Patel",)).fetchone()
        if row:
            return row["id"]
        cur = db.execute(
            """INSERT INTO agents (name, company, persona, system_prompt, pitch,
                                   language, tts_speaker, greeting)
               VALUES (?,?,?,?,?,?,?,?) RETURNING id""",
            (
                "Vishal Patel",
                "Pramukh Group",
                "Warm bilingual real-estate sales agent.",
                DEFAULT_SYSTEM_PROMPT,
                "Pramukh Group delivers premium homes in Gujarat's fastest-growing "
                "corridors — let me find the right project for you and book a site visit.",
                "auto",
                "shubh",
                "Hello! Vishal here from Pramukh Group — how can I help you today?",
            ),
        )
        return cur.fetchone()["id"]


def import_legacy_contacts(agent_id: int) -> int:
    contacts_path = _REPO_ROOT / "dashboard" / "outbound_contacts.json"
    if not contacts_path.exists():
        return 0
    contacts = json.loads(contacts_path.read_text() or "[]")
    with get_db() as db:
        row = db.execute("SELECT id FROM campaigns WHERE name=?", ("Legacy Outbound",)).fetchone()
        if row:
            campaign_id = row["id"]
        else:
            cur = db.execute(
                "INSERT INTO campaigns (name, agent_id, status) VALUES (?,?,?) RETURNING id",
                ("Legacy Outbound", agent_id, "paused"),
            )
            campaign_id = cur.fetchone()["id"]

        imported = 0
        for c in contacts:
            phone = c.get("phone")
            if not phone:
                continue
            exists = db.execute(
                "SELECT id FROM leads WHERE campaign_id=? AND phone=?", (campaign_id, phone)
            ).fetchone()
            if exists:
                continue
            db.execute(
                """INSERT INTO leads (campaign_id, name, phone, status, disposition,
                                      attempts, last_called_at, notes)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    campaign_id,
                    c.get("name"),
                    phone,
                    "called" if c.get("call_count") else "pending",
                    c.get("disposition"),
                    c.get("call_count", 0),
                    c.get("last_called_at"),
                    c.get("notes"),
                ),
            )
            imported += 1
        return imported


def main() -> None:
    init_db()
    agent_id = seed_default_agent()
    n = import_legacy_contacts(agent_id)
    print(f"Seeded agent id={agent_id}; imported {n} legacy leads.")


if __name__ == "__main__":
    main()
