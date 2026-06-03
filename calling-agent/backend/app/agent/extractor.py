"""
Background lead extraction — runs after each turn via asyncio.create_task.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openai import AsyncOpenAI
    from .memory import LeadProfile

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a data extraction assistant for a real estate company. "
    "Extract structured lead information from sales call snippets. "
    "Return ONLY valid JSON — no explanation, no markdown."
)

_PROMPT = """\
Extract lead data from this conversation snippet. Include only fields clearly mentioned.
Use null for anything not mentioned or unclear.

{{
  "name": null,              // caller's first name only
  "city": null,              // "Surat", "Vapi", or "Silvassa" — null if not one of these
  "property_type": null,     // "1BHK", "2BHK", "3BHK", "4BHK", or "plot"
  "budget": null,            // as mentioned: "70 lakh", "1.2 crore", "35L", etc.
  "purpose": null,           // "investment" or "self_use" — null if unclear
  "timeline": null,          // "immediately", "3 months", "6 months", "1 year", etc.
  "phone": null,             // 10-digit Indian mobile if mentioned
  "visit_scheduled": null,   // true ONLY if caller explicitly agreed to visit
  "visit_datetime": null,    // day/time string if visit scheduled
  "project_interest": null   // exact Pramukh project name if caller mentioned one
}}

User said: {user_text}
Agent said: {agent_text}

JSON only:"""

_MODEL = "gpt-4o-mini"


async def extract_lead_update(
    client: "AsyncOpenAI",
    user_text: str,
    agent_text: str,
    current_lead: "LeadProfile",
) -> dict:
    prompt = _PROMPT.format(user_text=user_text, agent_text=agent_text[:300])

    resp = await client.chat.completions.create(
        model=_MODEL,
        max_tokens=200,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user",   "content": prompt},
        ],
    )
    raw = resp.choices[0].message.content.strip()

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    data = json.loads(raw)

    updates = {}
    for key, value in data.items():
        if value is None or not hasattr(current_lead, key):
            continue
        current = getattr(current_lead, key)
        if current is None or current is False:
            updates[key] = value

    return updates
