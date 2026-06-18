"""Data-driven stage machine for ConversationRouter.

The canonical stage *keys* are fixed (GREETING → DISCOVERY → PROJECT_MATCH →
INVITE_VISIT → CONFIRMATION → CLOSING) because extraction/transition code across
the pipeline keys off them. What IS data-driven, per agent:

  * the LLM **goal text** shown at each stage, and
  * the **advancement condition** that moves the call to the next stage.

An agent supplies overrides via the DB `agents.stages` JSON column, e.g.::

    {
      "DISCOVERY":     {"goal": "...custom...", "advance_when": {"min_turns": 4}},
      "PROJECT_MATCH": {"goal": "...custom..."}
    }

Anything omitted falls back to the defaults below, which reproduce today's
hand-tuned Pramukh behaviour exactly. Invalid JSON → defaults (never raises).

Advancement condition grammar (JSON-friendly, evaluated against a call context):
    {"always": true}
    {"min_turns": N}              # discovery_turns >= N
    {"has": "name"|"location"|"appointment"}
    {"flag": "confirmed"}
    {"booking_valid": true}       # passes _validate_booking_for_closing()
    {"all": [cond, ...]}          # AND
    {"any": [cond, ...]}          # OR
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger("stage_machine")

# Canonical ordered stage keys.
STAGE_ORDER = ["GREETING", "DISCOVERY", "PROJECT_MATCH", "INVITE_VISIT", "CONFIRMATION", "CLOSING"]

# Default per-stage goal text (verbatim from the legacy hardcoded _stage_goals).
DEFAULT_GOALS = {
    "GREETING": (
        "Caller just responded to your introduction. "
        "Warmly acknowledge what they said in their language. "
        "Ask for their name — ONE question only."
    ),
    "DISCOVERY": (
        "You are a proactive property sales consultant. Your mission: qualify this caller "
        "and build enough rapport to earn a site visit.\n"
        "\n"
        "MANDATORY FLOW — after EVERY caller response:\n"
        "  1. Acknowledge what they just said (1 sentence, genuinely).\n"
        "  2. Check QUALIFICATION STATUS below — ask the NEXT MISSING FIELD in priority order.\n"
        "  3. If all core fields are captured, pivot to a project recommendation naturally.\n"
        "\n"
        "COLLECTION PRIORITY (one question per turn, skip if already known):\n"
        "  1. Name  2. City (Surat / Vapi / Silvassa)  3. Configuration (1/2/3 BHK)\n"
        "  4. Budget (comfortable range — ask softly)  5. Purpose (self-use or investment)\n"
        "  6. Timeline (when planning to buy)  7. Phone / WhatsApp\n"
        "\n"
        "Do NOT wait for the caller to volunteer information — ask for it warmly.\n"
        "Do NOT skip fields — every answer helps you match the right property.\n"
        "If the caller volunteers a field before you ask, capture it and move to the next one."
    ),
    "PROJECT_MATCH": (
        "You have enough information. Now be the expert.\n"
        "Pick EXACTLY ONE project that fits what this caller described — budget, location, purpose.\n"
        "Name the project. Give 2 specific reasons why it fits THEM personally.\n"
        "Never list the whole portfolio — make it feel like a personal recommendation.\n"
        "End your recommendation with a natural bridge toward a site visit:\n"
        "'Photos don't do it justice — would you like to see it in person?'"
    ),
    "INVITE_VISIT": (
        "The caller is interested. Your only goal now: book the site visit.\n"
        "Give EXACTLY TWO specific time options (from the available slots).\n"
        "When they pick a slot, confirm: date, time, and their name for the register.\n"
        "Ask for WhatsApp to send the site address and directions.\n"
        "Do NOT give more options — scarcity creates urgency."
    ),
    "CONFIRMATION": (
        "Confirm the visit details: day, time, property, and caller's name.\n"
        "Ask if any family member is joining (great buying decisions involve family).\n"
        "Offer to WhatsApp the exact site address and parking instructions."
    ),
    "CLOSING": (
        "Warm, genuine close using caller's name. Express real excitement about meeting them. "
        "Keep the door open — any question, just call or WhatsApp."
    ),
}

# Default advancement conditions (reproduce legacy _maybe_advance_stage logic).
# GREETING advances on the first user turn (handled at turn start, not here) → {}.
# CLOSING is terminal → {}.
DEFAULT_ADVANCE = {
    "GREETING": {},
    "DISCOVERY": {"any": [
        {"all": [{"has": "name"}, {"has": "location"}, {"min_turns": 3}]},
        {"min_turns": 12},
    ]},
    "PROJECT_MATCH": {"min_turns": 9},
    "INVITE_VISIT": {"has": "appointment"},
    "CONFIRMATION": {"all": [{"flag": "confirmed"}, {"booking_valid": True}]},
    "CLOSING": {},
}


def build_stage_spec(agent_config) -> dict:
    """Return {stage_key: {"goal": str, "advance_when": dict}} merged with overrides."""
    spec = {k: {"goal": DEFAULT_GOALS[k], "advance_when": DEFAULT_ADVANCE[k]} for k in STAGE_ORDER}
    raw = getattr(agent_config, "stages", None) if agent_config else None
    if not raw:
        return spec
    try:
        overrides = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (ValueError, TypeError) as e:
        logger.warning("[STAGE] invalid stages override (%s); using defaults", e)
        return spec
    for key, val in overrides.items():
        if key not in spec or not isinstance(val, dict):
            continue
        if val.get("goal"):
            spec[key]["goal"] = val["goal"]
        if isinstance(val.get("advance_when"), dict):
            spec[key]["advance_when"] = val["advance_when"]
    logger.info("[STAGE] applied overrides for: %s", ", ".join(overrides.keys()))
    return spec


def next_stage(stage_key: str) -> str | None:
    try:
        i = STAGE_ORDER.index(stage_key)
    except ValueError:
        return None
    return STAGE_ORDER[i + 1] if i + 1 < len(STAGE_ORDER) else None


def should_advance(cond: dict, ctx: dict) -> bool:
    """Evaluate a declarative advancement condition against the call context.

    ctx keys: name(bool), location(bool), appointment(bool), turns(int),
              confirmed(bool), booking_valid(bool).
    Empty/unknown condition → False (never auto-advance).
    """
    if not cond:
        return False
    if "always" in cond:
        return bool(cond["always"])
    if "all" in cond:
        return all(should_advance(c, ctx) for c in cond["all"])
    if "any" in cond:
        return any(should_advance(c, ctx) for c in cond["any"])
    if "min_turns" in cond:
        return ctx.get("turns", 0) >= int(cond["min_turns"])
    if "has" in cond:
        return bool(ctx.get(cond["has"], False))
    if "flag" in cond:
        return bool(ctx.get(cond["flag"], False))
    if "booking_valid" in cond:
        return ctx.get("booking_valid", False) == bool(cond["booking_valid"])
    return False
