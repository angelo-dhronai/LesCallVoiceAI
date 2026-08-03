"""
spoken_surfaces — answers "what will the caller actually HEAR" for an agent
definition by driving the real conversation engine on throwaway routers
(same recipe as boot_check.py). Read-only, offline, deterministic; never
re-describes engine precedence in prose — the line comes from the engine.

Born from the 2026-08-01 Jethalal incident: the Copilot edited
response_bank.opening_greeting on an outbound agent and reported success,
but outbound calls speak outbound.opening_greeting when it is set — the
change was applied, unreachable, and inaudible. Every consumer of this
module (Copilot digest, apply-time lints, post-apply readback, fleet lint)
exists so that class of failure surfaces before a live call does.

Callers in async contexts must run the resolver via `asyncio.to_thread(...)`
— the greeting leg uses `asyncio.run` internally and needs a loop-free
thread (identical contract to engine_boot_check).
"""

from __future__ import annotations

import asyncio
import copy
import re

from .schema import AgentDefinition

# ── dead keys ───────────────────────────────────────────────────────────────
# response_bank.localized_responses keys the ENGINE actually reads (via
# ConversationRouter._localized / _localized_list). Any OTHER key under
# localized_responses is accepted by the schema, rendered in diffs, and read
# by NO code path — a silent no-op write. diff.py's own docstring uses
# `localized_responses.hi.opening_greeting` as an example path; the engine
# has never spoken it. test_spoken_surfaces.py keeps this set honest with a
# source scan of conversation.py.
LOCALIZED_KEYS_READ = frozenset(
    {"greeting", "gratitude", "closing", "close_confirm", "close_confirmed"})

_LOCALIZED_LEAF = re.compile(
    r"^response_bank\.localized_responses\.[^.\[\]]+\.([^.\[\]]+)$")

# Substituted into the engine's {contact_name} slot, then swapped back so
# previews display the template semantics instead of a fake name.
_NAME_SENTINEL = "ZZCONTACTZZ"


def dead_path_reason(path: str) -> str | None:
    """Non-None when writing `path` provably changes nothing on any call."""
    m = _LOCALIZED_LEAF.match(str(path).strip())
    if m and m.group(1) not in LOCALIZED_KEYS_READ:
        return (
            f"'{path}' is read by no engine code path — writing it changes "
            "nothing on any call. The localized keys the engine speaks are: "
            + ", ".join(sorted(LOCALIZED_KEYS_READ))
            + ". The opening line lives in response_bank.opening_greeting "
            "(inbound) / outbound.opening_greeting (outbound calls).")
    return None


# ── opening resolution (engine-driven) ──────────────────────────────────────

def _coerce(agent_or_dict) -> AgentDefinition:
    if isinstance(agent_or_dict, AgentDefinition):
        return agent_or_dict
    return AgentDefinition.from_dict(dict(agent_or_dict))


def _opening_line(agent: AgentDefinition, direction: str, lang: str) -> str:
    from ..voice.conversation import ConversationRouter

    router = ConversationRouter(agent_def=agent, default_language=lang)
    # Deterministic calendar — live bookings must never influence resolution
    # (same guard as boot_check).
    router._available_calendar_slots = (
        lambda: list(agent.calendar.mock_slots or []))
    router.call_direction = direction
    if direction == "outbound":
        router.outbound_contact_name = _NAME_SENTINEL
        router.outbound_contact_phone = "9999999999"
    result = asyncio.run(router.generate_opening())
    text = getattr(result, "text", "") or ""
    return text.replace(_NAME_SENTINEL, "{contact_name}")


def _resolve_one(agent: AgentDefinition, direction: str, lang: str) -> dict:
    """The exact first line for direction × lang, plus WHICH field produced
    it — decided by re-running the engine with the candidate source cleared,
    never by mirroring the engine's precedence rules here."""
    line = _opening_line(agent, direction, lang)
    source = "response_bank.opening_greeting"
    if direction == "outbound" and (agent.outbound.opening_greeting or {}):
        stripped = copy.deepcopy(agent)
        stripped.outbound.opening_greeting = {}
        if _opening_line(stripped, direction, lang) != line:
            source = "outbound.opening_greeting"
    return {"line": line, "source": source}


def resolve_spoken_surfaces(agent_or_dict, direction: str | None = None) -> dict:
    """Openings per supported language for the agent's (or given) direction,
    each with its source field; `shadowed` lists greeting-looking YAML paths
    that are NOT what this agent's calls speak."""
    agent = _coerce(agent_or_dict)
    direction = (direction or getattr(agent, "call_direction", "")
                 or "inbound").lower()
    langs = list(agent.languages.supported or []) \
        or [agent.languages.default or "en"]
    default_lang = agent.languages.default or langs[0]
    if default_lang not in langs:
        langs.insert(0, default_lang)

    openings = {lang: _resolve_one(agent, direction, lang) for lang in langs}

    shadowed: list[dict] = []
    rb = (agent.response_bank.opening_greeting or "").strip()
    ob_values = {str(v).strip()
                 for v in (agent.outbound.opening_greeting or {}).values()
                 if str(v).strip()}
    ob_wins = any(o["source"] == "outbound.opening_greeting"
                  for o in openings.values())
    # Only a real trap when the dead field SAYS something different — twin
    # builders deliberately write the same opener into both homes.
    if direction == "outbound" and rb and ob_wins and rb not in ob_values:
        shadowed.append({
            "path": "response_bank.opening_greeting",
            "note": ("NOT spoken on this agent's outbound calls — the live "
                     "opening comes from outbound.opening_greeting. Edit "
                     "that field (every supported language), or clear it to "
                     "fall back to response_bank.opening_greeting."),
        })
    if direction == "inbound" and ob_values:
        shadowed.append({
            "path": "outbound.opening_greeting",
            "note": ("dormant — this agent answers inbound calls, which "
                     "open with response_bank.opening_greeting; the "
                     "outbound greeting is only spoken if the agent ever "
                     "places outbound calls."),
        })

    return {
        "direction": direction,
        "default_language": default_lang,
        "openings": openings,
        "shadowed": shadowed,
        "callback_note": (
            "scheduled follow-up calls open with a platform-composed "
            "callback line referencing the earlier conversation — no "
            "greeting field changes that"),
    }


# ── change-set analysis (for apply-time checks) ─────────────────────────────

_GREETING_PATHS = ("response_bank.opening_greeting",
                   "outbound.opening_greeting")


def touches_opening(changed_paths) -> bool:
    return any(str(p).startswith(_GREETING_PATHS) or str(p) == "call_direction"
               for p in (changed_paths or ()))


def reachability_findings(merged_or_agent, changed_paths) -> dict:
    """{'blockers': [...], 'warnings': [...]} for a set of dotted paths a
    write is about to touch (blockers) / has touched (warnings), evaluated
    against the post-merge definition.

    Pure string/field checks — no router spin-up — so it is safe to call
    inline before a write. The hearable readback (which DOES drive the
    engine) is `resolve_spoken_surfaces`, run after the write.
    """
    blockers: list[str] = []
    warnings: list[str] = []
    paths = [str(p) for p in (changed_paths or ())]

    for p in paths:
        reason = dead_path_reason(p)
        if reason:
            blockers.append(reason)

    try:
        agent = _coerce(merged_or_agent)
    except Exception:
        return {"blockers": blockers, "warnings": warnings}
    direction = (getattr(agent, "call_direction", "") or "inbound").lower()
    rb = (agent.response_bank.opening_greeting or "").strip()
    ob_values = {str(v).strip()
                 for v in (agent.outbound.opening_greeting or {}).values()
                 if str(v).strip()}

    touched_rb = any(p == "response_bank.opening_greeting" for p in paths)
    touched_ob = any(p.startswith("outbound.opening_greeting") for p in paths)

    if (direction == "outbound" and touched_rb and ob_values
            and rb not in ob_values):
        warnings.append(
            "response_bank.opening_greeting is NOT spoken on this agent's "
            "outbound calls — they open with outbound.opening_greeting, "
            "which still says: "
            + " / ".join(sorted(ob_values))[:200]
            + ". To change what callers hear, edit outbound.opening_greeting "
            "for every supported language (or clear it).")
    if direction == "inbound" and touched_ob and not touched_rb:
        warnings.append(
            "this agent answers inbound calls, which open with "
            "response_bank.opening_greeting — outbound.opening_greeting is "
            "only spoken if the agent ever places outbound calls.")

    return {"blockers": blockers, "warnings": warnings}
