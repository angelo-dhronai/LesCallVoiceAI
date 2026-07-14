"""
Use the LLM to extract structured YAML fields from document text.
Returns a diff preview; the caller decides whether to apply it.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_AGENTS_DIR = Path(__file__).resolve().parents[3] / "agents"

_PROMPT = """\
You are a structured data extractor for AI voice agent configuration files.

Read the document below and extract information to fill a voice agent's YAML configuration.
Return ONLY a JSON object (starting with {{ and ending with }}) with ONLY the fields you can confidently infer from the document.
NEVER return a plain string, array, or any value other than a JSON object.
Do NOT guess or hallucinate. If a field cannot be determined, omit it entirely.
If no fields can be extracted at all, return an empty object: {{}}

Extract these fields (use these exact key names):
  agent_name            — The spoken name the AI agent should use, if the document names one (string)
  company_name          — Full company name (string)
  company_tagline       — One-sentence brand descriptor, max 100 chars (string)
  role_description      — What role the AI agent plays, e.g. "Sales Agent and Fabric Expert" (string)
  contact               — Phone/WhatsApp number callers can be given (string)
  call_direction        — "inbound" if the agent answers calls (support/reception/booking), "outbound" if it places calls (sales outreach/follow-ups). Only if the document makes this clear. (string)
  portfolio_prompt_text — Detailed multi-paragraph description of products/services. Numbered entries, each with a name and description. (string)
  known_projects        — Dict of lowercase product/service keys → [canonical_name, "Standard"], e.g. {{"cotton fabric": ["Cotton Fabric", "Standard"]}} (object)
  locations             — List of cities/regions served (array of strings)
  objection_patterns    — Common objections callers raise, short lowercase phrases (array of strings)
  opening_greeting      — A natural 1-2 sentence opening for when the agent first speaks (string)
  appointment_slots     — Bookable visit/appointment time slots if the document lists availability, e.g. [{{"day": "Tomorrow", "time": "10:00 AM"}}] (array of objects with "day" and "time")
  extra_always_rules    — Business-specific rules the agent must always follow (array of strings)
  extra_never_rules     — Business-specific things the agent must never say or do (array of strings)

DOCUMENT:
{document_text}

Return ONLY the JSON object. No markdown, no explanation, no code fences."""


# Fields the builder considers essential for a working agent. Anything the
# document didn't yield comes back in "missing" so the UI can explicitly ask
# the user for it instead of silently leaving gaps.
_REQUIRED_FIELDS: list[tuple[str, str, str]] = [
    ("agent_name",            "Agent name",          "The spoken name the agent introduces itself with"),
    ("company_name",          "Company name",        "The business the agent represents"),
    ("company_tagline",       "Company tagline",     "One-line brand descriptor used in the agent's prompt"),
    ("role_description",      "Agent role",          "e.g. 'inbound support consultant' or 'outbound sales agent'"),
    ("contact",               "Contact number",      "Phone/WhatsApp the agent may share with callers"),
    ("call_direction",        "Call type",           "Will this agent take inbound calls or place outbound calls?"),
    ("opening_greeting",      "Opening greeting",    "The first thing the agent says on a call"),
    ("portfolio_prompt_text", "Products / services", "What the agent knows and can talk about"),
    ("locations",             "Locations served",    "Cities/regions the business covers"),
    ("appointment_slots",     "Calendar slots",      "Bookable time slots (recommended so the agent can schedule visits)"),
]


def compute_missing(fields: dict) -> list[dict]:
    """Which essential fields the extraction did NOT produce."""
    missing = []
    for key, label, hint in _REQUIRED_FIELDS:
        val = fields.get(key)
        if val is None or val == "" or val == [] or val == {}:
            missing.append({"key": key, "label": label, "hint": hint})
    return missing


def extract_fields_from_text(document_text: str) -> dict:
    """
    Call LLM to extract YAML-fillable fields.
    Returns {"fields": {...}, "missing": [...], "status": "ok"}
    or {"error": "...", "status": "error"}.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return {"status": "error", "error": "OPENAI_API_KEY not configured"}

    text = document_text[:14000] if len(document_text) > 14000 else document_text

    try:
        import openai
        client = openai.OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": _PROMPT.format(document_text=text)}],
            max_tokens=2500,
            temperature=0,
        )
        raw = resp.choices[0].message.content.strip()
        # Strip accidental markdown fences
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(lines[1:] if lines[-1].strip() == "```" else lines[1:-1])
            raw = raw.strip()
        fields = json.loads(raw)
        if not isinstance(fields, dict):
            logger.warning("[docs/yaml_filler] LLM returned %s instead of object: %r", type(fields).__name__, raw[:120])
            return {"status": "error", "error": f"LLM returned a {type(fields).__name__} instead of a JSON object. Try uploading a more detailed document."}
        return {"status": "ok", "fields": fields, "missing": compute_missing(fields)}
    except json.JSONDecodeError as exc:
        logger.warning("[docs/yaml_filler] JSON parse error: %s | raw=%r", exc, raw[:200] if 'raw' in dir() else '?')
        return {"status": "error", "error": f"LLM returned invalid JSON — try again or upload a different document."}
    except Exception as exc:
        logger.error("[docs/yaml_filler] Extraction error: %s", exc)
        return {"status": "error", "error": str(exc)}


def apply_fields(agent_folder: str, fields: dict) -> dict:
    """
    Merge extracted fields into an agent's YAML file.
    Returns {"status": "ok", "applied": [...]} or {"status": "error", "error": "..."}.
    """
    yaml_path = _AGENTS_DIR / agent_folder / "agent.yaml"
    if not yaml_path.exists():
        return {"status": "error", "error": f"Agent folder '{agent_folder}' not found"}

    try:
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}

        applied: list[str] = []

        _set(data, ["identity", "agent_name"],      fields, "agent_name",         applied)
        _set(data, ["identity", "contact"],         fields, "contact",            applied)
        _set(data, ["identity", "company_name"],    fields, "company_name",       applied)
        _set(data, ["identity", "company_tagline"], fields, "company_tagline",     applied)
        _set(data, ["identity", "role_description"], fields, "role_description",   applied)

        _set(data, ["knowledge_base", "portfolio_prompt_text"], fields, "portfolio_prompt_text", applied)
        _set(data, ["knowledge_base", "known_projects"],        fields, "known_projects",        applied)
        _set(data, ["knowledge_base", "locations"],             fields, "locations",             applied)
        _set(data, ["knowledge_base", "objection_patterns"],    fields, "objection_patterns",    applied)

        _set(data, ["response_bank", "opening_greeting"],       fields, "opening_greeting",      applied)

        _set(data, ["llm_config", "extra_always_rules"],        fields, "extra_always_rules",    applied)
        _set(data, ["llm_config", "extra_never_rules"],         fields, "extra_never_rules",     applied)

        direction = str(fields.get("call_direction", "")).strip().lower()
        if direction in ("inbound", "outbound"):
            data["call_direction"] = direction
            applied.append("call_direction")

        slots = fields.get("appointment_slots")
        if isinstance(slots, list):
            clean = [
                {"day": str(s.get("day", "")).strip(), "time": str(s.get("time", "")).strip()}
                for s in slots
                if isinstance(s, dict) and s.get("day") and s.get("time")
            ]
            if clean:
                cal = data.setdefault("calendar", {"type": "mock", "mock_slots": [], "slots_text": {}})
                cal["type"] = cal.get("type") or "mock"
                cal["mock_slots"] = clean
                slot_str = ", ".join(f"{s['day']} at {s['time']}" for s in clean)
                texts = cal.get("slots_text") or {}
                # Regenerate for every configured language; translations of a
                # stale slot list are worse than an English list of real slots.
                cal["slots_text"] = {k: slot_str for k in texts} if texts else {"en": slot_str}
                applied.append("appointment_slots")

        if not applied:
            return {"status": "ok", "applied": []}

        # Route through the store choke point (plan §5.1): revision snapshot,
        # audit stamps, validation, atomic write, registry invalidation.
        # No expected_paths scoping: apply_fields maps document fields to a
        # known-safe fixed path set that the diff proof would only re-derive.
        from ..agent_definition import store as agent_store
        agent_store.write_agent(
            agent_folder, data, actor="docfill",
            reason="document field extraction: " + ", ".join(applied)[:150],
            agents_dir=_AGENTS_DIR)

        logger.info("[docs/yaml_filler] Applied %d fields to agent '%s'", len(applied), agent_folder)
        return {"status": "ok", "applied": applied}

    except Exception as exc:
        logger.error("[docs/yaml_filler] Apply error: %s", exc)
        return {"status": "error", "error": str(exc)}


def _set(data: dict, path: list[str], fields: dict, key: str, applied: list[str]) -> None:
    """Set data[path[0]][path[1]]... = fields[key] if key present in fields."""
    if key not in fields:
        return
    node = data
    for step in path[:-1]:
        node = node.setdefault(step, {})
    node[path[-1]] = fields[key]
    applied.append(key)
