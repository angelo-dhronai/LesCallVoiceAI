"""
Agent Copilot API — conversational agent builder mounted into the dashboard.

Exposes a ChatGPT-style copilot that creates / edits / manages voice agents by
interviewing the user, then writing through the SAME plumbing the Agent
Builder uses (AgentDefinition schema + agents/<slug>/agent.yaml + registry).

Endpoints
---------
POST /api/copilot/chat                  {session_id, message} -> {reply, actions, agents_changed}
GET  /api/copilot/sessions/{session_id} -> {transcript: [...]}  (for page reload)

Design notes
------------
* Server-side agentic loop (max ~8 tool iterations per user turn) against the
  OpenAI-compatible endpoint configured in calling-agent/.env
  (LLM_BASE_URL / LLM_API_KEY / LLM_MODEL — Groq in production).
* Tools reuse agent_builder_api's handler coroutines where they are plain
  async functions (list/get/activate/get_routing). Handlers that need a
  fastapi Request (create/update/validate/set_routing) are replicated in a
  few lines on top of AgentDefinition / agent_registry — same code paths,
  same files on disk.
* create fills unspecified sections with sensible defaults modeled on
  agents/dhronai_ea/agent.yaml, so the LLM only has to draft the
  business-specific content (greeting, stage goals, knowledge, objections).
* update NEVER replaces from scratch: it loads the current full definition,
  DEEP-MERGES the partial changes onto it, re-validates via
  AgentDefinition.from_dict, then writes — calendar / slots /
  qa_field_signals / stt_corrections etc. always survive an edit.
* Sessions: in-memory dict + JSON persistence under dashboard/copilot_sessions/
  (lazily reloaded, so a dashboard restart keeps conversations).
"""
from __future__ import annotations

import copy
import dataclasses
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request

logger = logging.getLogger("agent_copilot_api")

# agent_builder_api already puts calling-agent on sys.path and loads .env.
import agent_builder_api as builder                                 # noqa: E402
from agent_builder_api import _AGENTS_DIR, _read_active             # noqa: E402
from backend.app.agent_definition.schema import (                   # noqa: E402
    AgentDefinition, AgentIdentity, PersonalityConfig, LanguageConfig,
    TTSVoiceConfig, CallFlowConfig, StageDefinition, SlotDefinition,
    KnowledgeBase, ResponseBank, CalendarConfig, LLMConfig,
    BrandConfig, OutboundConfig, GuardrailsConfig, FollowUpConfig,
    PostCallConfig,
)
from backend.app.agent_definition.registry import agent_registry    # noqa: E402
from backend.app.agent_definition import store as agent_store       # noqa: E402
from backend.app.agent_definition.diff import (                     # noqa: E402
    apply_ops as _apply_ops, compute_diff as _compute_diff,
    render_human as _render_diff,
)
import agent_builder_api as builder                                 # noqa: E402
from copilot_interview import (                                     # noqa: E402
    INTERVIEW_LAYERS, interview_progress, render_interview_state,
)

_UPLOADS_DIR = Path(__file__).parent / "copilot_uploads"


def _interview_state(session: dict) -> dict:
    """Lazy interview state on a session (old session files unaffected)."""
    return session.setdefault(
        "interview", {"draft": {}, "skipped": [], "provenance": {}, "phase": "idle"})


async def _copilot_tenancy_dep(request: Request):
    """Resolve owner, propagate to the builder's ContextVar (Copilot creates
    agents through builder handlers, which must claim the slug for THIS tenant),
    and enforce per-owner session access. Async on purpose — see builder."""
    import auth
    owner = auth.current_owner(request)
    request.state.owner = owner
    builder._REQ_OWNER.set(owner)
    sid = request.path_params.get("session_id")
    if sid and not _session_owner_ok(sid, owner):
        raise HTTPException(status_code=404, detail="session not found")


router = APIRouter(dependencies=[Depends(_copilot_tenancy_dep)])

_SESSIONS_DIR = Path(__file__).parent / "copilot_sessions"
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,80}$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{1,60}$")
_MAX_TOOL_ITERATIONS = 8
_CONTEXT_MAX_MESSAGES = 48  # trim very long sessions before sending to the LLM


# ─────────────────────────────────────────────────────────────────────────────
# LLM client (OpenAI-compatible — Groq in production)
# ─────────────────────────────────────────────────────────────────────────────
_client = None


def _env(name: str, default: str = "") -> str:
    """COPILOT_LLM_* overrides win over the voice pipeline's LLM_* config,
    so the copilot can run on a different model/key without touching the
    live calling pipeline (Groq rate limits are per-model)."""
    return (os.environ.get(f"COPILOT_{name}") or os.environ.get(name) or default).strip()


def _get_client():
    global _client
    if _client is None:
        from openai import AsyncOpenAI
        base_url = _env("LLM_BASE_URL")
        api_key = _env("LLM_API_KEY")
        if not base_url or not api_key:
            raise HTTPException(
                status_code=503,
                detail="Copilot LLM not configured — set LLM_BASE_URL / LLM_API_KEY in calling-agent/.env",
            )
        # Generous retries: Groq free tier rate-limits aggressively (429 with
        # Retry-After up to ~60s) and the SDK honors Retry-After between tries.
        _client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=120.0, max_retries=5)
    return _client


def _model_name() -> str:
    return _env("LLM_MODEL", "llama-3.3-70b-versatile")


# ─────────────────────────────────────────────────────────────────────────────
# Default agent definition (modeled on agents/dhronai_ea/agent.yaml)
# ─────────────────────────────────────────────────────────────────────────────
_LANG_STRINGS: dict[str, dict[str, Any]] = {
    "en": {
        "fillers": ["One moment please...", "Sure, one second..."],
        "lang_fallback": "Sorry, could you repeat that?",
        "timeout_fallback": "Let me get back to you on that.",
        "decline_farewell": "No problem at all — thank you for your time, have a great day!",
        "graceful_close": "Thank you so much for your time. Have a wonderful day!",
        "lang_switch": "Sure, let's continue in English.",
        "slots_text": "Tomorrow at 11:00 AM, Tomorrow at 4:00 PM, or This Saturday at 12:00 PM",
        "register_note": (
            "Use natural, warm, professional English — courteous and concise, "
            "like a polished front-desk professional. Never corporate or robotic."
        ),
    },
    "hi": {
        "fillers": ["एक मिनट...", "हाँ, एक सेकंड..."],
        "lang_fallback": "माफ़ कीजिए, क्या आप दोबारा बोल सकते हैं?",
        "timeout_fallback": "मैं इस पर वापस आती हूँ।",
        "decline_farewell": "कोई बात नहीं, आपके समय के लिए धन्यवाद। आपका दिन शुभ रहे!",
        "graceful_close": "आपके समय के लिए बहुत धन्यवाद! आपका दिन शुभ हो।",
        "lang_switch": "जी बिल्कुल, हम हिंदी में बात करते हैं।",
        "slots_text": "कल सुबह 11 बजे, कल शाम 4 बजे, या इस शनिवार दोपहर 12 बजे",
        "register_note": (
            "Use natural, modern conversational Hindi — polite and warm. Code-switch to "
            "everyday English words Indians use ('appointment', 'time', 'service'). "
            "Avoid formal/Sanskritized vocabulary. Address the caller respectfully (aap)."
        ),
    },
    "gu": {
        "fillers": ["એક મિનિટ...", "હા, એક સેકંડ..."],
        "lang_fallback": "માફ કરશો, ફરી બોલશો?",
        "timeout_fallback": "હું આના પર પાછી આવું છું.",
        "decline_farewell": "કંઈ વાંધો નહીં, તમારા સમય માટે આભાર. તમારો દિવસ શુભ રહે!",
        "graceful_close": "તમારા સમય માટે ખૂબ ખૂબ આભાર! તમારો દિવસ શુભ રહે.",
        "lang_switch": "ચોક્કસ, આપણે ગુજરાતીમાં વાત કરીએ.",
        "slots_text": "કાલે સવારે 11 વાગ્યે, કાલે સાંજે 4 વાગ્યે, અથવા આ શનિવારે બપોરે 12 વાગ્યે",
        "register_note": (
            "Use natural conversational Gujarati — warm and polite. Keep technical/"
            "business terms in English. Address the caller respectfully (tame)."
        ),
    },
}

_DEFAULT_STAGES = [
    {
        "id": "GREETING",
        "order": 0,
        "goal": (
            "Warmly greet the caller in their language, introduce yourself and the company "
            "in one short sentence, and ask how you can help — ONE question only."
        ),
        "turns_until_advance": 1,
    },
    {
        "id": "DISCOVERY",
        "order": 1,
        "goal": (
            "Understand what the caller needs. Ask ONE short question at a time. "
            "Acknowledge each answer before the next question. Once their need is clear, "
            "move toward proposing a concrete next step."
        ),
        "turns_until_advance": 6,
    },
    {
        "id": "INVITE_VISIT",
        "order": 2,
        "goal": (
            "The caller is interested. Propose a concrete next step (appointment / visit / "
            "callback) and offer EXACTLY TWO specific options from the available slots. "
            "When they pick one, confirm the day and time."
        ),
        "turns_until_advance": 99,
    },
    {
        "id": "CONFIRMATION",
        "order": 3,
        "goal": (
            "Confirm the booking details clearly: day, time, and the caller's name. "
            "Ask for the best phone number or WhatsApp to send the confirmation."
        ),
        "turns_until_advance": 99,
    },
    {
        "id": "CLOSING",
        "order": 4,
        "goal": (
            "Warm, genuine close using the caller's name. Thank them and let them know "
            "they can call back any time."
        ),
        "turns_until_advance": 99,
    },
]

_DEFAULT_OUTBOUND_STAGES = [
    {
        "id": "OPENING",
        "order": 0,
        "goal": (
            "You placed this call. Confirm you're speaking with the right person, introduce "
            "yourself and who you represent in ONE short sentence, and ask if it's a good time "
            "to talk. Exactly ONE question — then wait."
        ),
        # 2, not 1: stage advancement runs before the turn's prompt is built,
        # so a threshold of 1 would skip this stage's goal entirely.
        "turns_until_advance": 2,
    },
    {
        "id": "PITCH",
        "order": 1,
        "goal": (
            "Deliver the core message of the call. State plainly why you're calling, then make "
            "your case using the SPECIFIC talking points in the KNOWLEDGE section — lead with the "
            "single strongest point, then support it with the others. Keep each turn to 2-3 "
            "sentences, and check they're following before piling on more."
        ),
        "turns_until_advance": 6,
    },
    {
        "id": "HANDLE_QUESTIONS",
        "order": 2,
        "goal": (
            "Answer questions and push-back directly and confidently using the facts you have. "
            "Acknowledge the concern first, then counter it with one concrete, specific point. "
            "Stay warm and never defensive; if you don't know something, say you'll follow up."
        ),
        # Finite: with 99 here the ASK stage — the whole point of an outbound
        # call — was unreachable (turn-count is the only advancement path).
        "turns_until_advance": 3,
    },
    {
        "id": "ASK",
        "order": 3,
        "goal": (
            "Make the specific ask this call exists for — clearly and directly. State exactly the "
            "outcome, decision, or next step you want from them, and give them the chance to agree."
        ),
        "turns_until_advance": 4,
    },
    {
        "id": "CLOSING",
        "order": 4,
        "goal": (
            "Thank them genuinely by name, restate any agreed next step in one sentence, and close "
            "warmly. Leave a positive final impression."
        ),
        "turns_until_advance": 99,
    },
]

_DEFAULT_OBJECTION_PATTERNS = [
    "not interested", "busy right now", "call later", "don't need", "no time",
    "don't want", "stop calling", "talk later", "later please", "maybe later",
    "some other time", "another time", "not right now", "call me later",
    "call back later", "baad mein baat", "phir baat", "abhi nahi", "abhi busy",
]

_DEFAULT_TIME_PATTERNS = [
    "morning", "afternoon", "evening", "tomorrow", "today", "this week", "next week",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "10 am", "11 am", "12 pm", "2 pm", "3 pm", "4 pm",
    "subah", "dopahar", "shaam", "kal", "parso", "aaj",
]


def _default_definition(name: str, provided: dict) -> dict:
    """Full, schema-valid default definition; the LLM's fields are merged on top."""
    langs = provided.get("languages")
    if not isinstance(langs, dict):  # belt-and-braces; _normalize_definition fixes this upstream
        langs = {"supported": langs} if isinstance(langs, list) else {}
    supported = [l for l in (langs.get("supported") or ["en"]) if isinstance(l, str)] or ["en"]
    default_lang = langs.get("default") or supported[0]
    if default_lang not in supported:
        default_lang = supported[0]

    def s(lang: str, key: str):
        return (_LANG_STRINGS.get(lang) or _LANG_STRINGS["en"])[key]

    tts_voices = {l: {"model": "bulbul:v3", "speaker": "simran", "pace": 1.15} for l in supported}
    register_notes = {l: s(l, "register_note") for l in supported}
    slots_text = {l: s(l, "slots_text") for l in supported}

    # Outbound agents lead the call and need a pitch-shaped flow, not the
    # inbound welcome→discovery→book skeleton. Pick the skeleton that matches
    # what the platform will actually do on the call.
    direction = str(provided.get("call_direction") or "inbound").strip().lower()
    base_stages = _DEFAULT_OUTBOUND_STAGES if direction == "outbound" else _DEFAULT_STAGES

    pretty = name.replace("_", " ").replace("-", " ").title()
    return {
        "agent_id": f"{name}-v1",
        "version": "1.0.0",
        "identity": {
            "agent_name": pretty,
            "company_name": pretty,
            "company_tagline": pretty,
            "role_description": "inbound customer assistant",
            "contact": "",
            "pronouns": "she",
        },
        "personality": {
            "pronouns": "she",
            "energy": "medium",
            "tone": "conversational",
            "style": "natural",
            "lingo": "",
            "custom_instructions": "Keep every reply to 1-2 short spoken sentences. Be warm and never pushy.",
        },
        "languages": {
            "supported": supported,
            "default": default_lang,
            "stt_forced_language": "",
            "allow_code_mixing": default_lang == "hi",
            "tts_voices": tts_voices,
            "register_notes": register_notes,
        },
        "call_flow": {
            "stages": copy.deepcopy(base_stages),
            "decline_threshold": 2,
        },
        "knowledge_base": {
            "known_projects": {},
            "locations": [],
            "portfolio_prompt_text": "No specific products configured. Use general knowledge to assist the caller.",
            "portfolio_section_label": "KNOWLEDGE BASE",
            "location_aliases": [],
            "objection_patterns": list(_DEFAULT_OBJECTION_PATTERNS),
            "time_patterns": list(_DEFAULT_TIME_PATTERNS),
            "qa_field_signals": {
                "name": ["your name", "naam", "aapka naam", "may i know your name", "who am i speaking"],
            },
            "stt_corrections": {
                "city_corrections": {},
                "phonetic_corrections": {},
                "real_estate_corrections": {},
                "project_name_corrections": {},
            },
        },
        "response_bank": {
            "opening_greeting": f"Hello! I'm {pretty}. How can I help you today?",
            "filler_responses": {l: list(s(l, "fillers")) for l in supported},
            "identity_responses": {},
            "objection_responses": {},
            "decline_farewell": {l: s(l, "decline_farewell") for l in supported},
            "lang_switch_responses": {l: s(l, "lang_switch") for l in supported},
            "lang_fallbacks": {l: s(l, "lang_fallback") for l in supported},
            "timeout_fallbacks": {l: s(l, "timeout_fallback") for l in supported},
            "response_fallback": s(default_lang, "lang_fallback"),
            "graceful_close": {l: s(l, "graceful_close") for l in supported},
            "localized_responses": {},
            "proactive_prompts": {},
        },
        "calendar": {
            "type": "mock",
            "mock_slots": [
                {"day": "Tomorrow", "time": "11:00 AM"},
                {"day": "Tomorrow", "time": "4:00 PM"},
                {"day": "This Saturday", "time": "12:00 PM"},
            ],
            "slots_text": slots_text,
        },
        "llm_config": {
            "model": "gpt-4o-mini",
            "max_tokens": 160,
            "timeout_s": 5.0,
            "stream_timeout_s": 7.0,
            "filler_timeout_s": 0.6,
            "intent_classify_timeout_s": 1.0,
            "max_response_length": 400,
            "extra_never_rules": [],
            "extra_always_rules": ["Always keep replies to 1-2 short spoken sentences."],
        },
        # AgentSpec v2 sections — all-empty defaults (plan §12 ledger):
        # empty = exactly today's engine behaviour.
        "brand": {},
        "outbound": {},
        "guardrails": {"use_builtin_rules": True},
        "follow_up": {},
        "post_call": {},
    }


def _deep_merge(base: dict, changes: dict) -> dict:
    """Recursively merge `changes` onto `base` in place. Lists/scalars replace."""
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def _coerce_obj(value: Any) -> dict:
    """The LLM sometimes passes JSON objects as strings — accept both."""
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


# Sections of the definition and the dataclass whose fields define what keys
# are real. Anything else the LLM invents (e.g. response_bank.follow_up) is
# dropped WITH A NOTE so the model can put the content somewhere real.
_SECTION_DATACLASSES: dict[str, type] = {
    "identity": AgentIdentity,
    "personality": PersonalityConfig,
    "languages": LanguageConfig,
    "call_flow": CallFlowConfig,
    "knowledge_base": KnowledgeBase,
    "response_bank": ResponseBank,
    "calendar": CalendarConfig,
    "llm_config": LLMConfig,
    # AgentSpec v2 sections
    "brand": BrandConfig,
    "outbound": OutboundConfig,
    "guardrails": GuardrailsConfig,
    "follow_up": FollowUpConfig,
    "post_call": PostCallConfig,
}
# revision/updated_at/updated_by are server-managed audit fields: they must
# survive the whole-document normalize on update (the current definition
# carries them), but LLM-provided input is stripped of them in create/update.
_SERVER_MANAGED_KEYS = {"revision", "updated_at", "updated_by"}
_TOP_LEVEL_KEYS = {"agent_id", "version", "call_direction", "slots",
                   *_SERVER_MANAGED_KEYS, *_SECTION_DATACLASSES}

# ResponseBank fields that must be per-language dicts. The LLM often sends a
# bare string — wrap it under the default language instead of crashing.
_RB_LANG_STR_FIELDS = (
    "identity_responses", "decline_farewell", "lang_switch_responses",
    "lang_fallbacks", "timeout_fallbacks", "graceful_close", "slots_prompt",
)
_RB_LANG_LIST_FIELDS = ("filler_responses", "objection_responses", "decline_responses")


def _fields_of(cls: type) -> set[str]:
    import dataclasses as _dc
    return {f.name for f in _dc.fields(cls)}


def _default_lang_of(d: dict) -> str:
    langs = d.get("languages")
    if isinstance(langs, dict):
        dl = langs.get("default")
        if isinstance(dl, str) and dl:
            return dl
        sup = langs.get("supported")
        if isinstance(sup, list) and sup:
            return str(sup[0])
    return "en"


# The TWO template placeholders the runtime understands, both only in the
# opening greeting (conversation.py generate_opening):
#   {contact_name} — the outbound contact's name, falling back to "sir/ma'am"
#   {current_time} — morning/afternoon/evening in IST at call time
# LLMs constantly invent other syntaxes — ${contact_name}, {{contact_name}},
# [contact name], {time_of_day} — which would be SPOKEN LITERALLY on a real
# call. Canonicalize them all.
_PLACEHOLDER_RE = re.compile(
    r"(?:\$\{|\{\{|\{|\[|<)\s*"
    r"(?:contact[ _\-]?name|customer[ _\-]?name|person[ _\-]?name|callee[ _\-]?name|contact|name)"
    r"\s*(?:\}\}|\}|\]|>)"
    r"|\$contact[ _\-]?name\b",
    re.IGNORECASE,
)

_TIME_PLACEHOLDER_RE = re.compile(
    r"(?:\$\{|\{\{|\{|\[|<)\s*"
    r"(?:current[ _\-]?time|time[ _\-]?of[ _\-]?day|greeting[ _\-]?time|day[ _\-]?part)"
    r"\s*(?:\}\}|\}|\]|>)"
    r"|\$current[ _\-]?time\b",
    re.IGNORECASE,
)


def _canonicalize_placeholders(text: str) -> str:
    text = _PLACEHOLDER_RE.sub("{contact_name}", text)
    return _TIME_PLACEHOLDER_RE.sub("{current_time}", text)


def _any_placeholder(text: str) -> bool:
    return bool(_PLACEHOLDER_RE.search(text) or _TIME_PLACEHOLDER_RE.search(text))


def _prune_nones(node: Any) -> Any:
    """Recursively remove None values. A None from the LLM must NEVER survive
    to the deep-merge, where it would clobber a good platform default and
    ship an agent that crashes mid-call (e.g. opening_greeting: null)."""
    if isinstance(node, dict):
        return {k: _prune_nones(v) for k, v in node.items() if v is not None}
    if isinstance(node, list):
        return [_prune_nones(x) for x in node if x is not None]
    return node


def _normalize_definition(d: dict, notes: list[str] | None = None) -> dict:
    """Deterministically repair every known LLM shape mistake, in place.

    This is the load-bearing wall between a sloppy tool call and a broken
    (or worse: silently wrong) agent. Every fix observed in production is
    covered; anything unknown is dropped with an explanatory note rather
    than crashing create/update. Idempotent — safe to run twice.
    """
    if notes is None:
        notes = []

    # ── Nones out first — they'd otherwise clobber good defaults on merge ───
    pruned = _prune_nones(d)
    d.clear()
    d.update(pruned)

    # ── sections in impossible container shapes ─────────────────────────────
    for section in ("identity", "personality"):
        val = d.get(section)
        if isinstance(val, list):
            first_dict = next((x for x in val if isinstance(x, dict)), None)
            if first_dict is not None:
                d[section] = first_dict
            elif val and isinstance(val[0], str):
                d[section] = (
                    {"agent_name": val[0]} if section == "identity"
                    else {"custom_instructions": " ".join(str(x) for x in val)}
                )
            else:
                d.pop(section)
                notes.append(f"{section} had an unusable shape — ignored")
    if isinstance(d.get("slots"), dict):
        d["slots"] = [d["slots"]]

    # ── languages: list/str → {"supported": [...], "default": first} ────────
    langs = d.get("languages")
    if isinstance(langs, (list, tuple)):
        d["languages"] = langs = {"supported": [str(x).strip().lower() for x in langs if str(x).strip()]}
        notes.append("languages was a plain list — converted to {supported: [...]}")
    elif isinstance(langs, str):
        d["languages"] = langs = {"supported": [langs.strip().lower()]}
        notes.append("languages was a plain string — converted to {supported: [...]}")
    if isinstance(langs, dict):
        sup = langs.get("supported")
        if isinstance(sup, str):
            langs["supported"] = [sup.strip().lower()]
        elif isinstance(sup, list):
            langs["supported"] = [str(x).strip().lower() for x in sup if str(x).strip()]
        elif "supported" in langs:
            langs.pop("supported")
            notes.append("languages.supported had an unusable shape — platform default kept")
        # An EMPTY supported list must not survive: on merge it would erase
        # the platform default and ship a zero-language agent as success.
        if "supported" in langs and not langs["supported"]:
            langs.pop("supported")
            notes.append("languages.supported was empty — platform default kept")
        dl = langs.get("default")
        if isinstance(dl, list):
            langs["default"] = str(dl[0]) if dl else ""
        elif dl is not None and not isinstance(dl, str):
            langs.pop("default", None)
        if not langs.get("default") and langs.get("supported"):
            langs["default"] = langs["supported"][0]

    # ── stray top-level tts_voices/voice/speaker → languages.tts_voices ─────
    stray_tts = d.pop("tts_voices", None)
    stray_voice = d.pop("voice", None)
    stray_voice = stray_voice if isinstance(stray_voice, str) else d.pop("speaker", None)
    if stray_tts is not None or stray_voice is not None:
        if not isinstance(d.get("languages"), dict):
            d["languages"] = {}
        lang_d = d["languages"]
        if isinstance(stray_tts, dict):
            existing = lang_d.get("tts_voices")
            lang_d["tts_voices"] = {**stray_tts, **existing} if isinstance(existing, dict) else stray_tts
            notes.append("tts_voices moved inside languages (its real home)")
        if isinstance(stray_voice, str) and stray_voice.strip():
            tv = lang_d.setdefault("tts_voices", {})
            for code in lang_d.get("supported") or ["en"]:
                tv.setdefault(code, {"model": "bulbul:v3", "speaker": stray_voice.strip().lower(), "pace": 1.15})
            notes.append(f"voice '{stray_voice}' applied as the TTS speaker for every language")
    # tts voice given as a bare speaker string per language
    tv = (d.get("languages") or {}).get("tts_voices") if isinstance(d.get("languages"), dict) else None
    if isinstance(tv, dict):
        voice_fields = _fields_of(TTSVoiceConfig)
        for code, v in list(tv.items()):
            if isinstance(v, str):
                tv[code] = {"model": "bulbul:v3", "speaker": v.strip().lower(), "pace": 1.15}
            elif isinstance(v, dict):
                v.setdefault("model", "bulbul:v3")
                v.setdefault("speaker", "simran")
                if v.get("model") not in ("bulbul:v2", "bulbul:v3-beta", "bulbul:v3"):
                    # speaker names sometimes land in model (e.g. model: shubh);
                    # Sarvam 400s the whole call, so coerce to a valid model
                    if isinstance(v.get("model"), str) and v["model"].strip() and v.get("speaker") in (None, "", "simran"):
                        v["speaker"] = v["model"].strip().lower()
                    v["model"] = "bulbul:v3"
                    notes.append(f"tts model for '{code}' was invalid — reset to bulbul:v3")
                for k in [k for k in v if k not in voice_fields]:
                    v.pop(k)
            else:  # numeric / junk — keep the platform default voice instead
                tv.pop(code)
    elif tv is not None and isinstance(d.get("languages"), dict):
        d["languages"].pop("tts_voices", None)
        notes.append("tts_voices had an unusable shape — platform default voices kept")

    # ── call_direction: list/"both"/junk → "inbound" | "outbound" ───────────
    if "call_direction" in d:
        cd = d.get("call_direction")
        if isinstance(cd, (list, tuple, set)):
            text = " ".join(str(x).strip().lower() for x in cd)
        else:
            text = str(cd).strip().lower()
        # Word-level matching — substring tricks invert on phrases like
        # "inbound and outbound" (contains both) or "outgoing" (contains "in").
        has_in = bool(re.search(r"in\s*bound|incoming|receiv|answer", text)) or text in ("in", "both", "all")
        has_out = bool(re.search(r"out\s*bound|outgoing|place|dial", text)) or text == "out"
        if (has_in and has_out) or "both" in text:
            d["call_direction"] = "inbound"
            notes.append(
                "call type 'both' stored as 'inbound' — the agent automatically switches to the "
                "outbound approach whenever the platform places the call, so inbound covers both"
            )
        elif has_out:
            d["call_direction"] = "outbound"
        elif has_in:
            d["call_direction"] = "inbound"
        else:
            d.pop("call_direction", None)
            notes.append(f"call_direction {cd!r} not understood — left unchanged")

    # ── call_flow: bare list → {"stages": [...]}; stage row repairs ─────────
    cf = d.get("call_flow")
    if isinstance(cf, list):
        d["call_flow"] = cf = {"stages": cf}
    if isinstance(cf, dict) and isinstance(cf.get("stages"), list):
        # Both skeletons: LLM-authored outbound stages (OPENING/PITCH/...)
        # that omit turns_until_advance must inherit the outbound pacing,
        # not the generic fallback. Only shared id is CLOSING (99 in both).
        turns_default = {s["id"]: s["turns_until_advance"]
                         for s in (_DEFAULT_STAGES + _DEFAULT_OUTBOUND_STAGES)}
        stage_fields = _fields_of(StageDefinition)
        fixed_stages = []
        for i, stage in enumerate(cf["stages"]):
            if isinstance(stage, str):
                stage = {"id": stage, "goal": stage}
            if not isinstance(stage, dict):
                continue
            if "id" in stage:
                stage["id"] = str(stage["id"]).upper().replace(" ", "_")
            stage["order"] = i
            stage.setdefault("turns_until_advance", turns_default.get(stage.get("id", ""), 8))
            for k in [k for k in stage if k not in stage_fields]:
                stage.pop(k)
            fixed_stages.append(stage)
        if fixed_stages:
            cf["stages"] = fixed_stages
        else:
            # An empty stages list would erase the default 5-stage flow on
            # merge and ship an agent with nothing to run. Keep the default.
            cf.pop("stages")
            notes.append("call_flow.stages was empty/unusable — default call flow kept")

    # ── identity / personality given as plain strings ───────────────────────
    if isinstance(d.get("identity"), str):
        d["identity"] = {"agent_name": d["identity"]}
    if isinstance(d.get("personality"), str):
        d["personality"] = {"custom_instructions": d["personality"]}

    # ── knowledge_base coercions ─────────────────────────────────────────────
    kb = d.get("knowledge_base")
    if isinstance(kb, str):
        d["knowledge_base"] = kb = {"portfolio_prompt_text": kb}
    elif isinstance(kb, list):
        # A bare list of facts → fold into the portfolio paragraph so the
        # content SURVIVES instead of crashing from_dict on kb["known_projects"]
        # (list indices error) or leaving a list where a dict is expected.
        facts = [str(x).strip() for x in kb if str(x).strip()]
        d["knowledge_base"] = kb = {"portfolio_prompt_text": " ".join(facts)} if facts else {}
        notes.append("knowledge_base was a list — folded into the knowledge paragraph")
    if isinstance(kb, dict):
        loc = kb.get("locations")
        if isinstance(loc, str):
            kb["locations"] = [s.strip() for s in loc.split(",") if s.strip()]
        elif isinstance(loc, list):
            kb["locations"] = [str(x).strip() for x in loc if str(x).strip()]
        elif "locations" in kb:
            kb.pop("locations")
        if not isinstance(kb.get("portfolio_prompt_text", ""), str):
            kb["portfolio_prompt_text"] = str(kb["portfolio_prompt_text"])
        kp = kb.get("known_projects")
        if isinstance(kp, list):  # ["Product A", {"name": ...}] → keyed dict
            fixed_kp: dict[str, list] = {}
            for item in kp:
                if isinstance(item, str) and item.strip():
                    fixed_kp[item.strip().lower()] = [item.strip(), "Standard"]
                elif isinstance(item, dict) and item.get("name"):
                    nm = str(item["name"]).strip()
                    fixed_kp[nm.lower()] = [nm, str(item.get("price") or item.get("location") or "Standard")]
            kb["known_projects"] = kp = fixed_kp
            notes.append("known_projects was a list — converted to the keyed form")
        if isinstance(kp, dict):
            for k, v in list(kp.items()):
                if isinstance(v, str):
                    kp[k] = [v, "Standard"]
                elif isinstance(v, list) and len(v) == 1:
                    kp[k] = [v[0], "Standard"]
                elif not isinstance(v, (list, tuple)):
                    kp[k] = [str(v), "Standard"]
        elif kp is not None:
            kb.pop("known_projects", None)
        # stt_corrections / qa_field_signals are real KB fields (so the
        # invented-key filter keeps them) but from_dict calls .get() on
        # stt_corrections — a list there is the exact "'list' object has no
        # attribute 'get'" crash. Enforce dict-shape here.
        sc = kb.get("stt_corrections")
        if sc is not None and not isinstance(sc, dict):
            kb.pop("stt_corrections", None)
            notes.append("stt_corrections had an unusable shape — platform default kept")
        elif isinstance(sc, dict):
            for sub in list(sc):
                if not isinstance(sc[sub], dict):
                    sc.pop(sub)
        qs = kb.get("qa_field_signals")
        if qs is not None and not isinstance(qs, dict):
            kb.pop("qa_field_signals", None)
            notes.append("qa_field_signals had an unusable shape — ignored")

    # ── calendar coercions ───────────────────────────────────────────────────
    cal = d.get("calendar")
    if isinstance(cal, list):  # bare slot list → proper calendar block
        d["calendar"] = cal = {"type": "mock", "mock_slots": cal}
    if isinstance(cal, dict):
        cal.setdefault("type", "mock")
        ms = cal.get("mock_slots")
        if isinstance(ms, list):
            fixed_slots = []
            for row in ms:
                if isinstance(row, dict) and row.get("day") and row.get("time"):
                    fixed_slots.append({"day": str(row["day"]).strip(), "time": str(row["time"]).strip()})
                elif isinstance(row, str) and " at " in row:
                    day, _, tme = row.partition(" at ")
                    if day.strip() and tme.strip():
                        fixed_slots.append({"day": day.strip(), "time": tme.strip()})
            cal["mock_slots"] = fixed_slots
        elif "mock_slots" in cal:
            cal.pop("mock_slots")
            notes.append("calendar.mock_slots had an unusable shape — existing slots kept")
        st = cal.get("slots_text")
        if st is not None and not isinstance(st, dict):
            cal.pop("slots_text")
    elif cal is not None:
        d.pop("calendar", None)
        notes.append("calendar had an unusable shape — ignored")

    # ── response_bank repairs ────────────────────────────────────────────────
    rb = d.get("response_bank")
    if isinstance(rb, str):
        d["response_bank"] = rb = {"opening_greeting": rb}
    if isinstance(rb, dict):
        default_lang = _default_lang_of(d)
        greeting = rb.get("opening_greeting")
        if isinstance(greeting, dict):
            picked = greeting.get(default_lang) or next(iter(greeting.values()), "")
            if str(picked).strip():
                rb["opening_greeting"] = str(picked)
            else:
                rb.pop("opening_greeting")
                notes.append("opening_greeting was empty — existing/default greeting kept")
        elif isinstance(greeting, (list, tuple)):
            # A bilingual-greeting-as-list must not become a spoken Python repr.
            picked = next((x.strip() for x in greeting if isinstance(x, str) and x.strip()), "")
            if picked:
                rb["opening_greeting"] = picked
                notes.append("opening_greeting was a list — kept the first line only")
            else:
                rb.pop("opening_greeting")
                notes.append("opening_greeting was empty — existing/default greeting kept")
        elif greeting is not None and not isinstance(greeting, str):
            rb["opening_greeting"] = str(greeting)
        # Canonicalize invented placeholder syntaxes — ${contact_name} etc.
        # would be spoken literally on a real call.
        g = rb.get("opening_greeting")
        if isinstance(g, str) and _any_placeholder(g):
            fixed = _canonicalize_placeholders(g)
            if fixed != g:
                rb["opening_greeting"] = fixed
                notes.append(
                    "placeholder in the greeting rewritten to {contact_name}/{current_time} — "
                    "the only templates the call engine understands (single curly braces)"
                )
        if "response_fallback" in rb and not isinstance(rb["response_fallback"], str):
            rb["response_fallback"] = str(rb["response_fallback"])
        for key in _RB_LANG_STR_FIELDS:
            val = rb.get(key)
            if isinstance(val, str):
                rb[key] = {default_lang: val}
            elif isinstance(val, dict):
                for lang, v in list(val.items()):
                    if not isinstance(v, str):
                        val[lang] = str(v)
            elif val is not None:
                rb.pop(key)
        for key in _RB_LANG_LIST_FIELDS:
            val = rb.get(key)
            if isinstance(val, str):
                rb[key] = {default_lang: [val]}
            elif isinstance(val, list):
                rb[key] = {default_lang: [str(x) for x in val]}
            elif isinstance(val, dict):
                for lang, v in list(val.items()):
                    if isinstance(v, str):
                        val[lang] = [v]
                    elif isinstance(v, list):
                        val[lang] = [str(x) for x in v]
                    else:
                        val[lang] = [str(v)]
            elif val is not None:
                rb.pop(key)

    # ── identity / personality: every field is a plain string ───────────────
    # (except the v2 typed personality fields, validated separately below)
    _PERS_TYPED = {"traits", "energy_mirroring", "verbosity_max_sentences"}
    for section in ("identity", "personality"):
        val = d.get(section)
        if isinstance(val, dict):
            for k, v in list(val.items()):
                if section == "personality" and k in _PERS_TYPED:
                    continue
                if isinstance(v, (dict, list)):
                    val.pop(k)
                    notes.append(f"{section}.{k} had an unusable shape — ignored")
                elif not isinstance(v, str):
                    val[k] = str(v)
    pers = d.get("personality")
    if isinstance(pers, dict):
        if "ai_disclosure" in pers and str(pers.get("ai_disclosure")).strip().lower() \
                not in ("deflect", "admit", "admit_if_pressed"):
            notes.append(f"personality.ai_disclosure {pers['ai_disclosure']!r} invalid — reset to 'deflect'")
            pers["ai_disclosure"] = "deflect"
        elif isinstance(pers.get("ai_disclosure"), str):
            pers["ai_disclosure"] = pers["ai_disclosure"].strip().lower()
        if "traits" in pers:
            t = pers["traits"]
            pers["traits"] = ([str(x) for x in t] if isinstance(t, list)
                              else [str(t)] if isinstance(t, str) and t.strip() else [])
        if "energy_mirroring" in pers and not isinstance(pers["energy_mirroring"], bool):
            v = str(pers["energy_mirroring"]).strip().lower()
            pers["energy_mirroring"] = v in ("1", "true", "yes", "on")
        if "verbosity_max_sentences" in pers:
            try:
                pers["verbosity_max_sentences"] = max(1, min(8, int(pers["verbosity_max_sentences"])))
            except (TypeError, ValueError):
                pers.pop("verbosity_max_sentences")

    # ── slots rows — shape filter + v2 value validation ─────────────────────
    if isinstance(d.get("slots"), list):
        slot_fields = _fields_of(SlotDefinition)
        fixed_slots = []
        for s in d["slots"]:
            if not (isinstance(s, dict) and s.get("name")):
                continue
            s = {k: v for k, v in s.items() if k in slot_fields}
            s["name"] = str(s["name"]).strip().lower().replace(" ", "_")
            # enum-ish fields: coerce invalid values to their defaults —
            # a hallucinated value must never abort a save that would
            # otherwise succeed.
            if "type" in s and str(s.get("type")).strip().lower() not in (
                    "", "text", "number", "money", "enum", "boolean",
                    "phone", "date", "time", "location"):
                notes.append(f"slot '{s['name']}' type {s['type']!r} invalid — cleared")
                s["type"] = ""
            elif isinstance(s.get("type"), str):
                s["type"] = s["type"].strip().lower()
            if "merge_policy" in s and str(s.get("merge_policy")).strip().lower() not in (
                    "free", "correction_gated", "confirm"):
                notes.append(f"slot '{s['name']}' merge_policy {s['merge_policy']!r} invalid — reset to 'free'")
                s["merge_policy"] = "free"
            elif isinstance(s.get("merge_policy"), str):
                s["merge_policy"] = s["merge_policy"].strip().lower()
            if "confirm_readback" in s and str(s.get("confirm_readback")).strip().lower() not in (
                    "", "verbatim", "digit_groups_2"):
                notes.append(f"slot '{s['name']}' confirm_readback {s['confirm_readback']!r} invalid — cleared")
                s["confirm_readback"] = ""
            elif isinstance(s.get("confirm_readback"), str):
                s["confirm_readback"] = s["confirm_readback"].strip().lower()
            for int_key, default in (("priority", 100), ("max_asks", 0)):
                if int_key in s:
                    try:
                        s[int_key] = int(s[int_key])
                    except (TypeError, ValueError):
                        s[int_key] = default
            # extract_patterns: cap count and per-pattern length; drop
            # patterns that don't compile (they'd be rejected at load).
            if "extract_patterns" in s:
                pats = s["extract_patterns"] if isinstance(s["extract_patterns"], list) else []
                good = []
                for p in pats[:8]:
                    p = str(p)[:200]
                    try:
                        re.compile(p)
                        good.append(p)
                    except re.error:
                        notes.append(f"slot '{s['name']}' extract pattern {p!r} does not compile — dropped")
                s["extract_patterns"] = good
            if "depends_on" in s and not isinstance(s["depends_on"], list):
                s["depends_on"] = [str(s["depends_on"])] if isinstance(s["depends_on"], str) else []
            fixed_slots.append(s)
        # depends_on: names must exist in the slot set; cycles are dropped.
        names = {s["name"] for s in fixed_slots}
        for s in fixed_slots:
            deps = [str(x).strip().lower().replace(" ", "_") for x in (s.get("depends_on") or [])]
            valid = [x for x in deps if x in names and x != s["name"]]
            if len(valid) != len(deps):
                notes.append(f"slot '{s['name']}' depends_on entries not in the slot set were dropped")
            if valid:
                s["depends_on"] = valid
            elif "depends_on" in s:
                s["depends_on"] = []
        # cycle detection (DFS); on a cycle, clear depends_on for its members
        graph = {s["name"]: set(s.get("depends_on") or []) for s in fixed_slots}
        def _cyclic(node, stack):
            if node in stack:
                return True
            stack = stack | {node}
            return any(_cyclic(n, stack) for n in graph.get(node, ()))
        cyclic_nodes = {n for n in graph if _cyclic(n, frozenset())}
        if cyclic_nodes:
            notes.append(f"slot dependency cycle among {sorted(cyclic_nodes)} — dependencies cleared")
            for s in fixed_slots:
                if s["name"] in cyclic_nodes:
                    s["depends_on"] = []
        d["slots"] = fixed_slots

    # ── v2 sections: shape coercions ─────────────────────────────────────────
    default_lang_v2 = _default_lang_of(d)

    def _lang_dict(val):
        """str → {default_lang: str}; dict of str kept; anything else None."""
        if isinstance(val, str):
            return {default_lang_v2: val} if val.strip() else None
        if isinstance(val, dict):
            return {str(k): str(v) for k, v in val.items() if isinstance(v, (str, int, float))}
        return None

    br = d.get("brand")
    if isinstance(br, str):
        d["brand"] = br = {"spoken_company_name": br}
    if isinstance(br, dict):
        for k in ("credibility_markers", "trust_proofs", "regions_served"):
            if k in br:
                v = br[k]
                br[k] = ([str(x) for x in v] if isinstance(v, list)
                         else [str(v)] if isinstance(v, str) and v.strip() else [])
        for k in ("spoken_company_name", "positioning_tagline", "contact_phone", "whatsapp_number"):
            if k in br and not isinstance(br[k], str):
                br[k] = str(br[k])
    elif br is not None:
        d.pop("brand", None)
        notes.append("brand had an unusable shape — ignored")

    ob = d.get("outbound")
    if isinstance(ob, dict):
        for k in ("identity_confirm_line", "good_time_check_line", "pitch",
                  "wrong_person_response", "busy_response", "wrong_number_close",
                  "opening_greeting"):
            if k in ob:
                fixed = _lang_dict(ob[k])
                if fixed is None:
                    ob.pop(k)
                    notes.append(f"outbound.{k} had an unusable shape — ignored")
                else:
                    ob[k] = fixed
        if "wrong_number_patterns" in ob:
            v = ob["wrong_number_patterns"]
            ob["wrong_number_patterns"] = ([str(x) for x in v] if isinstance(v, list)
                                           else [str(v)] if isinstance(v, str) else [])
        if "direction_brief" in ob and not isinstance(ob["direction_brief"], str):
            ob["direction_brief"] = str(ob["direction_brief"])
    elif ob is not None:
        d.pop("outbound", None)
        notes.append("outbound had an unusable shape — ignored")

    gr = d.get("guardrails")
    if isinstance(gr, dict):
        for k in ("never_rules", "always_rules"):
            if k in gr:
                v = gr[k]
                gr[k] = ([str(x) for x in v] if isinstance(v, list)
                         else [str(v)] if isinstance(v, str) and v.strip() else [])
        if "use_builtin_rules" in gr and not isinstance(gr["use_builtin_rules"], bool):
            gr["use_builtin_rules"] = str(gr["use_builtin_rules"]).strip().lower() in ("1", "true", "yes", "on")
        for k in ("honesty", "compliance", "escalation"):
            if k in gr and not isinstance(gr[k], dict):
                gr.pop(k)
                notes.append(f"guardrails.{k} had an unusable shape — ignored")
        comp = gr.get("compliance")
        if isinstance(comp, dict):
            for k in ("disclaimers", "dnd_phrases", "restricted_topics"):
                if k in comp and not isinstance(comp[k], list):
                    comp[k] = [str(comp[k])] if isinstance(comp[k], str) and comp[k].strip() else []
        esc = gr.get("escalation")
        if isinstance(esc, dict):
            hm = esc.get("handoff_message")
            if hm is not None:
                fixed = _lang_dict(hm)
                if fixed is None:
                    esc.pop("handoff_message")
                else:
                    esc["handoff_message"] = fixed
    elif gr is not None:
        d.pop("guardrails", None)
        notes.append("guardrails had an unusable shape — ignored")

    fu = d.get("follow_up")
    if isinstance(fu, dict):
        wp = fu.get("whatsapp_promise")
        if wp is not None and not isinstance(wp, dict):
            fu.pop("whatsapp_promise")
            notes.append("follow_up.whatsapp_promise had an unusable shape — ignored")
        elif isinstance(wp, dict):
            pl = wp.get("promise_line")
            if pl is not None:
                fixed = _lang_dict(pl)
                if fixed is None:
                    wp.pop("promise_line")
                else:
                    wp["promise_line"] = fixed
            if "assets" in wp and not isinstance(wp["assets"], list):
                wp["assets"] = [str(wp["assets"])] if isinstance(wp["assets"], str) else []
        cb = fu.get("callback")
        if cb is not None and not isinstance(cb, dict):
            fu.pop("callback")
    elif fu is not None:
        d.pop("follow_up", None)
        notes.append("follow_up had an unusable shape — ignored")

    pc = d.get("post_call")
    if isinstance(pc, dict):
        for k in ("success_definition", "scoring", "lead_profile"):
            if k in pc and not isinstance(pc[k], dict):
                pc.pop(k)
                notes.append(f"post_call.{k} had an unusable shape — ignored")
        if "outcomes" in pc:
            oc = pc["outcomes"]
            pc["outcomes"] = [o for o in oc if isinstance(o, dict)] if isinstance(oc, list) else []
    elif pc is not None:
        d.pop("post_call", None)
        notes.append("post_call had an unusable shape — ignored")

    # ── stage v2 fields: role/advance_when/branches validation ──────────────
    cf2 = d.get("call_flow")
    if isinstance(cf2, dict) and isinstance(cf2.get("stages"), list):
        stage_ids = {str(s.get("id") or "") for s in cf2["stages"] if isinstance(s, dict)}
        _ROLES = ("", "greeting", "confirm_person", "discovery", "pitch",
                  "recommend", "scheduling", "confirmation", "closing", "custom")
        for s in cf2["stages"]:
            if not isinstance(s, dict):
                continue
            if "role" in s:
                role = str(s.get("role") or "").strip().lower()
                if role not in _ROLES:
                    notes.append(f"stage {s.get('id')} role {s['role']!r} invalid — cleared")
                    role = ""
                s["role"] = role
            if "collect" in s and not isinstance(s["collect"], list):
                s["collect"] = [str(s["collect"])] if isinstance(s["collect"], str) else []
            if "advance_when" in s and not isinstance(s["advance_when"], dict):
                s.pop("advance_when")
                notes.append(f"stage {s.get('id')} advance_when had an unusable shape — ignored")
            if "branches" in s:
                brs = s["branches"] if isinstance(s["branches"], list) else []
                good_brs = []
                for b in brs:
                    if not isinstance(b, dict):
                        continue
                    goto = str(b.get("goto") or "").upper().replace(" ", "_")
                    if goto and goto not in stage_ids:
                        notes.append(f"branch goto {b.get('goto')!r} names a stage that "
                                     "does not exist — branch dropped")
                        continue
                    b["goto"] = goto
                    if "max_takes" in b:
                        try:
                            b["max_takes"] = max(1, int(b["max_takes"]))
                        except (TypeError, ValueError):
                            b["max_takes"] = 1
                    if "actions" in b:
                        acts = b["actions"] if isinstance(b["actions"], list) else [b["actions"]]
                        b["actions"] = [str(a) for a in acts if str(a) in (
                            "capture_phone", "promise_whatsapp_media",
                            "book_visit", "schedule_callback")]
                    good_brs.append(b)
                s["branches"] = good_brs

    # ── knowledge_base v2 fields ─────────────────────────────────────────────
    kb2 = d.get("knowledge_base")
    if isinstance(kb2, dict):
        if "catalog" in kb2:
            cat = kb2["catalog"]
            kb2["catalog"] = ([c for c in cat if isinstance(c, dict) and c.get("name")]
                              if isinstance(cat, list) else [])
        if "match_rules" in kb2:
            mr = kb2["match_rules"]
            kb2["match_rules"] = ([m for m in mr if isinstance(m, dict)]
                                  if isinstance(mr, list) else [])
        for k in ("pricing_policy", "fact_policy", "product_lock"):
            if k in kb2 and not isinstance(kb2[k], dict):
                kb2.pop(k)
                notes.append(f"knowledge_base.{k} had an unusable shape — ignored")

    # ── response_bank v2 fields ──────────────────────────────────────────────
    rb2 = d.get("response_bank")
    if isinstance(rb2, dict):
        if "objection_playbook" in rb2:
            pb = rb2["objection_playbook"]
            good_pb = []
            for o in (pb if isinstance(pb, list) else []):
                if not isinstance(o, dict):
                    continue
                if "patterns" in o and not isinstance(o["patterns"], list):
                    o["patterns"] = [str(o["patterns"])] if isinstance(o["patterns"], str) else []
                ans = o.get("answer")
                if ans is not None:
                    fixed = _lang_dict(ans)
                    if fixed is None:
                        o.pop("answer")
                    else:
                        o["answer"] = fixed
                good_pb.append(o)
            rb2["objection_playbook"] = good_pb
        if "closing_responses" in rb2:
            fixed = _lang_dict(rb2["closing_responses"])
            if fixed is None:
                rb2.pop("closing_responses")
            else:
                rb2["closing_responses"] = fixed
        if "ai_disclosure_responses" in rb2:
            fixed = _lang_dict(rb2["ai_disclosure_responses"])
            if fixed is None:
                rb2.pop("ai_disclosure_responses")
            else:
                rb2["ai_disclosure_responses"] = fixed
        if "canned_phrases" in rb2:
            cp = rb2["canned_phrases"]
            good_cp = []
            for c in (cp if isinstance(cp, list) else []):
                if not isinstance(c, dict):
                    continue
                if "patterns" in c and not isinstance(c["patterns"], list):
                    c["patterns"] = [str(c["patterns"])] if isinstance(c["patterns"], str) else []
                resp = c.get("responses")
                if resp is not None:
                    fixed = _lang_dict(resp)
                    if fixed is None:
                        c.pop("responses")
                    else:
                        c["responses"] = fixed
                good_cp.append(c)
            rb2["canned_phrases"] = good_cp
        if "enable_greeting_shortcut" in rb2 and not isinstance(rb2["enable_greeting_shortcut"], bool):
            rb2["enable_greeting_shortcut"] = str(rb2["enable_greeting_shortcut"]).strip().lower() in (
                "1", "true", "yes", "on")

    # ── drop invented keys (with notes so the LLM relocates the content) ────
    for section, cls in _SECTION_DATACLASSES.items():
        val = d.get(section)
        if isinstance(val, dict):
            allowed = _fields_of(cls)
            for k in [k for k in val if k not in allowed]:
                val.pop(k)
                notes.append(
                    f"'{section}.{k}' is not a real setting — its content was NOT saved. "
                    "Put it in a stage goal, the greeting, or the knowledge text instead."
                )
        elif val is not None:
            # Last-line guard: a section that reached here in a non-dict shape
            # (e.g. llm_config as a list) would crash from_dict on `.get()`
            # — the '\'list\' object has no attribute \'get\'' failure. Drop it
            # so the platform default is used instead of bricking create/update.
            d.pop(section)
            notes.append(
                f"'{section}' had an unusable shape (expected settings, not a list/value) "
                "— platform default kept. Put that content in a stage goal or the knowledge text."
            )
    for k in [k for k in d if k not in _TOP_LEVEL_KEYS]:
        d.pop(k)
        notes.append(f"top-level '{k}' is not a real setting — dropped")

    return d


def _copilot_actor() -> str:
    return f"{builder._ctx_owner()}:copilot"


def _invariant_violations(merged: dict) -> list[str]:
    """§5.4: the whole-document passes that used to silently MUTATE untouched
    fields become validation-time CHECKS that fail the proposal instead —
    otherwise the scoped-diff proof would read the mutations as collateral."""
    problems: list[str] = []
    langs = merged.get("languages") or {}
    sup = langs.get("supported") if isinstance(langs, dict) else None
    if not (isinstance(sup, list) and any(isinstance(x, str) and x.strip() for x in sup)):
        problems.append("languages.supported would become empty")
    elif isinstance(langs, dict) and langs.get("default") not in sup:
        problems.append("languages.default would not be in supported")
    cf = merged.get("call_flow") or {}
    if not (isinstance(cf, dict) and cf.get("stages")):
        problems.append("call_flow.stages would become empty")
    rb = merged.get("response_bank") or {}
    greeting = rb.get("opening_greeting") if isinstance(rb, dict) else None
    if not (isinstance(greeting, str) and greeting.strip()):
        problems.append("opening_greeting would become empty")
    return problems


def _paths_of_changes(changes: dict, prefix: str = "") -> set[str]:
    """Dotted path prefixes a partial-changes dict intends to touch (leaf
    subtrees; lists are replaced whole so the list path itself is the scope)."""
    out: set[str] = set()
    for k, v in (changes or {}).items():
        p = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict) and v:
            out |= _paths_of_changes(v, p)
        else:
            out.add(p)
    return out


def _read_full_definition(slug: str) -> tuple[dict, str]:
    """(canonical full definition dict, rev sha). Canonical = through
    from_dict→asdict so proposals diff against the same form the store's
    scoping proof uses."""
    raw, rev = agent_store.read_agent(slug, _AGENTS_DIR)
    canon = dataclasses.asdict(AgentDefinition.from_dict(raw))
    return canon, rev


def _visible_slug(folder: str) -> bool:
    """Is `folder` an agent the current tenant may see? Multi-tenant guard so
    name-resolution NEVER enumerates or matches another tenant's agents (the
    error messages here are shown to the user by the copilot)."""
    try:
        allowed = builder._visible_slugs(builder._ctx_owner())  # None = admin
    except Exception:
        allowed = None
    return allowed is None or folder in allowed


def _resolve_agent_slug(name: str) -> str:
    """Users say 'Ananya', not 'dhronai_ea' — resolve display names to slugs.

    Order: exact folder → lowercased/underscored folder → the agent whose
    spoken name (identity.agent_name) matches, as a whole, a word, or a
    prefix. Ambiguity and no-match raise with a human-friendly list of the
    agents that DO exist, so the copilot can ask instead of guessing.

    Only the CALLER's own agents are ever considered or named — a tenant can
    never discover another tenant's agents through this resolver.
    """
    raw = str(name).strip()
    if not raw:
        raise ValueError("No agent name given")
    # Escape hatch for ambiguity errors: every error path below prints options
    # as "Display (folder)" — accept that exact form back, so any option we
    # show the user is itself a valid, unambiguous input.
    m = re.fullmatch(r".{0,80}\(([A-Za-z0-9_\-]{1,80})\)\s*", raw)
    if m and _visible_slug(m.group(1)) and (_AGENTS_DIR / m.group(1) / "agent.yaml").exists():
        return m.group(1)
    # Folder lookups only for plain names — anything with path separators or
    # dots must not escape the agents dir (or reach into _archived/).
    folder_match = ""
    if re.fullmatch(r"[A-Za-z0-9 _\-]{1,80}", raw):
        if _visible_slug(raw) and (_AGENTS_DIR / raw / "agent.yaml").exists():
            folder_match = raw
        else:
            slug = raw.lower().replace(" ", "_").replace("-", "_")
            for candidate in (slug, slug.replace("_", "-")):
                if _visible_slug(candidate) and (_AGENTS_DIR / candidate / "agent.yaml").exists():
                    folder_match = candidate
                    break
    if folder_match:
        # Guard against the split-brain trap: if a DIFFERENT agent's spoken
        # name is exactly what the user said, the folder shortcut would
        # silently edit the wrong agent (seen in production: two agents both
        # displaying "LesCall Agent"; edits went to the inactive duplicate
        # while calls ran the other). Ambiguity must be surfaced, not guessed.
        collisions = []
        for ypath in sorted(_AGENTS_DIR.glob("*/agent.yaml")):
            folder = ypath.parent.name
            if folder == folder_match or not _visible_slug(folder):
                continue
            try:
                display = (agent_registry.get(folder, _AGENTS_DIR).identity.agent_name or "").strip()
            except Exception:
                continue
            if display.lower() == raw.lower():
                collisions.append(f"{display} ({folder})")
        if collisions:
            try:
                fm_display = (agent_registry.get(folder_match, _AGENTS_DIR).identity.agent_name
                              or folder_match).strip()
            except Exception:
                fm_display = folder_match
            raise ValueError(
                f"'{raw}' is ambiguous — more than one agent answers to it: "
                f"{fm_display} ({folder_match}), {', '.join(collisions)}. Ask the user which "
                "one they mean, then pass that choice back EXACTLY as written including the "
                "part in parentheses."
            )
        return folder_match

    wanted = raw.lower()
    matches: list[tuple[str, str]] = []  # (folder, display)
    display_list: list[str] = []
    for ypath in sorted(_AGENTS_DIR.glob("*/agent.yaml")):
        folder = ypath.parent.name
        if not _visible_slug(folder):
            continue
        try:
            agent = agent_registry.get(folder, _AGENTS_DIR)
            display = (agent.identity.agent_name or "").strip()
        except Exception:
            continue
        display_list.append(f"{display or folder} ({folder})")
        d = display.lower()
        if d and (d == wanted or wanted in d.split() or d.startswith(wanted)):
            matches.append((folder, display))
    if len(matches) == 1:
        return matches[0][0]
    if len(matches) > 1:
        opts = ", ".join(f"{disp} ({f})" for f, disp in matches)
        raise ValueError(f"More than one agent matches '{raw}': {opts}. Ask the user which one they mean.")
    raise ValueError(f"No agent called '{raw}'. The agents that exist are: {', '.join(display_list) or 'none yet'}.")


# Tools whose `name` argument refers to an existing agent (resolved before
# execution so the LLM can pass whatever the user said).
_TOOLS_WITH_AGENT_NAME = {
    "get_agent", "update_agent", "activate_agent",
    "get_routing", "set_routing", "replace_mentions",
    "propose_update", "update_stage", "add_stage", "remove_stage",
    "reorder_stages", "update_slot", "add_slot", "remove_slot",
    "list_revisions", "rollback_agent",
}

# Tools that change something real. Used by the truthfulness backstop: if all
# of these failed in a turn, the reply MUST admit it.
_MUTATING_TOOLS = {"create_agent", "update_agent", "activate_agent", "set_routing",
                   "replace_mentions", "apply_update", "update_stage", "add_stage",
                   "remove_stage", "reorder_stages", "update_slot", "add_slot",
                   "remove_slot", "rollback_agent"}

# Plain-English verbs for action chips ("Couldn't create the agent: ...").
_TOOL_VERBS = {
    "list_agents": "check the agents",
    "get_agent": "look at the agent",
    "validate_agent": "check the draft",
    "create_agent": "create the agent",
    "update_agent": "update the agent",
    "replace_mentions": "apply the replacement",
    "activate_agent": "activate the agent",
    "get_routing": "check call routing",
    "set_routing": "update call routing",
    "propose_update": "prepare the change",
    "apply_update": "apply the change",
    "discard_update": "discard the change",
    "update_stage": "edit the call stage",
    "add_stage": "add the call stage",
    "remove_stage": "remove the call stage",
    "reorder_stages": "reorder the call stages",
    "update_slot": "edit the question",
    "add_slot": "add the question",
    "remove_slot": "remove the question",
    "list_revisions": "check the edit history",
    "rollback_agent": "undo the change",
}


def _failure_payload(tool: str, error: str) -> dict:
    """Tool-result body for a failed call — written so a sloppy LLM cannot
    misread it as success, and knows to either retry (fixed) or own up."""
    return {
        "status": "FAILED",
        "error": error,
        "reality": (
            "This operation DID NOT HAPPEN — nothing was created, changed, or activated. "
            "If you can fix the input, retry NOW in this same turn. Otherwise tell the "
            "user plainly that it failed and what you'll do about it. Do NOT claim success."
        ),
    }


# Friendly names for changed sections in action summaries — the user should
# never see raw config keys like "response_bank".
_SECTION_LABELS = {
    "identity": "who the agent is",
    "personality": "personality",
    "languages": "languages & voice",
    "call_flow": "call flow",
    "knowledge_base": "what the agent knows",
    "response_bank": "responses & greeting",
    "calendar": "calendar",
    "llm_config": "rules",
    "call_direction": "call type",
    "slots": "questions asked",
    "brand": "brand & trust",
    "outbound": "outbound scripts",
    "guardrails": "rules & compliance",
    "follow_up": "follow-ups",
    "post_call": "call outcomes",
}


def _friendly_sections(keys) -> str:
    return ", ".join(dict.fromkeys(_SECTION_LABELS.get(k, k.replace("_", " ")) for k in keys))


def _replace_in_tree(node: Any, pattern: "re.Pattern", replacement: str) -> tuple[Any, int]:
    """Recursively replace text in every string VALUE of a nested structure.
    Keys are left untouched (stage ids, language codes, lookup keys must not
    silently change). Returns (new_node, replacement_count)."""
    if isinstance(node, str):
        # lambda replacement = literal text; backslashes in the replacement
        # would otherwise be interpreted as regex group references
        new, n = pattern.subn(lambda _m: replacement, node)
        return new, n
    if isinstance(node, (list, tuple)):
        total = 0
        out = []
        for item in node:
            new, n = _replace_in_tree(item, pattern, replacement)
            out.append(new)
            total += n
        return out, total  # tuples come back as lists; from_dict re-tuples known_projects
    if isinstance(node, dict):
        total = 0
        out = {}
        for k, v in node.items():
            new, n = _replace_in_tree(v, pattern, replacement)
            out[k] = new
            total += n
        return out, total
    return node, 0


def _enforce_invariants(merged: dict, notes: list[str]) -> None:
    """Final gate before validate/write: whatever the merge produced, these
    properties must hold or the agent would die on its first call."""
    langs = merged.setdefault("languages", {})
    sup = langs.get("supported")
    clean_sup = [x for x in sup if isinstance(x, str) and x.strip()] if isinstance(sup, list) else []
    if not clean_sup:
        clean_sup = ["en"]
        notes.append("no usable languages — defaulted to English")
    langs["supported"] = clean_sup
    if langs.get("default") not in clean_sup:
        langs["default"] = clean_sup[0]
    tv = langs.get("tts_voices")
    if not isinstance(tv, dict):
        tv = langs["tts_voices"] = {}
    for code in clean_sup:
        if not isinstance(tv.get(code), dict):
            tv[code] = {"model": "bulbul:v3", "speaker": "simran", "pace": 1.15}
    rn = langs.get("register_notes")
    if not isinstance(rn, dict):
        rn = langs["register_notes"] = {}
    for code in clean_sup:
        rn.setdefault(code, (_LANG_STRINGS.get(code) or _LANG_STRINGS["en"])["register_note"])

    cf = merged.setdefault("call_flow", {})
    if not isinstance(cf, dict):
        cf = merged["call_flow"] = {}
    if not cf.get("stages"):
        cf["stages"] = copy.deepcopy(_DEFAULT_STAGES)
        notes.append("call flow was empty — default 5-stage flow restored")

    rb = merged.setdefault("response_bank", {})
    if not isinstance(rb, dict):
        rb = merged["response_bank"] = {}
    greeting = rb.get("opening_greeting")
    if not isinstance(greeting, str) or not greeting.strip():
        agent_name = (merged.get("identity") or {}).get("agent_name") or "your assistant"
        rb["opening_greeting"] = f"Hello! I'm {agent_name}. How can I help you today?"
        notes.append("opening greeting was missing — a simple default was set (tell the user)")


def _quality_warnings(merged: dict) -> list[str]:
    """Deterministic depth check on the FINAL definition. The runtime only
    speaks as richly as these fields are written — a one-line stage goal or an
    empty knowledge paragraph makes a shallow call no matter how good the chat
    was. Returned in the tool result so the copilot enriches thin content in
    the SAME turn instead of reporting a hollow agent as done."""
    w: list[str] = []
    direction = str(merged.get("call_direction") or "inbound").strip().lower()

    cf = merged.get("call_flow") or {}
    stages = cf.get("stages") if isinstance(cf, dict) else None
    stages = stages if isinstance(stages, list) else []
    thin = [
        str(s.get("id") or f"#{i}")
        for i, s in enumerate(stages)
        if isinstance(s, dict) and len(str(s.get("goal") or "").strip()) < 45
    ]
    if thin:
        w.append(
            "These call stages have goals too short to steer a real call: "
            f"{', '.join(thin)}. Rewrite each as 2-4 full sentences of concrete instructions — "
            "what to say, which specific talking points to use, and the ONE thing to accomplish "
            "before moving on."
        )
    if len(stages) < 2:
        w.append(
            "The call has only one stage. A real call needs a flow "
            + ("(opening → pitch → handle questions → the ask → close)."
               if direction == "outbound" else
               "(greeting → discovery → propose next step → confirm → close).")
            + " Give it distinct stages so the agent knows how to progress."
        )

    # Stage goals are INSTRUCTIONS to the AI — a verbatim script line (or a
    # template placeholder) in a goal never reaches the caller's ear the way
    # the author intended. Both patterns showed up in production.
    scripty = [
        str(s.get("id") or f"#{i}")
        for i, s in enumerate(stages)
        if isinstance(s, dict) and (
            _any_placeholder(str(s.get("goal") or ""))
            or re.match(r"\s*(?:hello|hi|hey|namaste|namaskar|नमस्ते|नमस्कार)\b[ ,!]",
                        str(s.get("goal") or ""), re.IGNORECASE)
        )
    ]
    if scripty:
        w.append(
            f"These stage goals look like word-for-word script lines: {', '.join(scripty)}. "
            "Stage goals are INSTRUCTIONS to the AI, not spoken text — placeholders like "
            "{contact_name} do NOT work there. Put the exact opening line in "
            "response_bank.opening_greeting (where {contact_name} IS substituted) and rewrite "
            "the stage goal as instructions for what to accomplish."
        )

    kb = merged.get("knowledge_base") or {}
    ppt = str((kb.get("portfolio_prompt_text") if isinstance(kb, dict) else "") or "").strip()
    if not ppt or ppt.startswith("No specific products") or len(ppt) < 80:
        w.append(
            "The knowledge paragraph is empty or generic. Fill it with the SPECIFIC facts, "
            "talking points, names, numbers and details this agent must know — this text is "
            "read on every single turn of the call, so it is where the substance lives."
        )

    rb = merged.get("response_bank") or {}
    if direction == "outbound" and isinstance(rb, dict) and not rb.get("objection_responses"):
        w.append(
            "This outbound agent has no objection responses. Add 1-2 per language for the "
            "pushback this specific call will get (e.g. 'not now', 'why should I', a flat no)."
        )
    greeting = str(rb.get("opening_greeting") or "") if isinstance(rb, dict) else ""
    _name_m = re.search(r"\b(?:Mr|Mrs|Ms|Miss|Dr|Shri|Smt)\.?\s+([A-Zऀ-ॿ][\w''-]*)", greeting)
    _agent_name = str(((merged.get("identity") or {}).get("agent_name")) or "").lower()
    if (
        direction == "outbound"
        and "{contact_name}" not in greeting
        and _name_m
        # the agent introducing ITSELF ("this is Dr. Mehta from City Clinic")
        # is fine — only a hardcoded name that isn't the agent's own is stale
        and _name_m.group(1).lower() not in _agent_name
    ):
        w.append(
            f"The opening greeting hardcodes the name '{_name_m.group(0)}', but this outbound "
            "agent may call different contacts. If that is the person being called, replace the "
            "name with {contact_name} — at call time it becomes the dialed contact's name from "
            "Outbound Calls. If it is the agent's own name, ignore this."
        )

    # Placeholders anywhere OUTSIDE opening_greeting are never substituted —
    # they would be spoken literally ("dollar contact name") on a real call.
    if isinstance(rb, dict):
        def _walk(node):
            if isinstance(node, str):
                yield node
            elif isinstance(node, dict):
                for v in node.values():
                    yield from _walk(v)
            elif isinstance(node, (list, tuple)):
                for v in node:
                    yield from _walk(v)
        bad_fields = sorted({
            key for key, val in rb.items()
            if key != "opening_greeting"
            and any(_any_placeholder(s) for s in _walk(val))
        })
        if bad_fields:
            w.append(
                "Template placeholders only work in the opening greeting — these response fields "
                f"contain placeholder text that would be SPOKEN LITERALLY on a call: {', '.join(bad_fields)}. "
                "Rewrite them without placeholders (or move the personalization into the greeting)."
            )
    return w


def _parse_tool_args(raw: Any) -> dict:
    """Tolerant parse of a tool call's `arguments` field.

    Groq/llama emits empty strings, "null", or double-encoded JSON for
    no-parameter tools — treat all of those as {} instead of erroring.
    """
    if raw is None or isinstance(raw, dict):
        return raw or {}
    text = str(raw).strip()
    if not text or text in ("null", '""', "''", "{}"):
        return {}
    parsed = json.loads(text)
    if isinstance(parsed, str):  # double-encoded
        parsed = json.loads(parsed) if parsed.strip() else {}
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise ValueError("arguments must be a JSON object")
    return parsed


# ─────────────────────────────────────────────────────────────────────────────
# Tools — same underlying logic as agent_builder_api endpoints
# ─────────────────────────────────────────────────────────────────────────────
TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "list_agents",
            "description": "List every voice agent on the platform and which one is active (answering inbound calls).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_agent",
            "description": "Read the full current definition of one agent (identity, languages, stages, greeting, knowledge, etc.). Always call this before editing an agent you haven't read this conversation.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "agent slug, e.g. 'pramukh'"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_agent",
            "description": "Dry-run schema validation of a (possibly partial) agent definition merged onto platform defaults. No files are written.",
            "parameters": {
                "type": "object",
                "properties": {"definition": {"type": "object", "description": "agent definition JSON to validate"}},
                "required": ["definition"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_agent",
            "description": (
                "Create a new voice agent. Provide ONLY the fields you want to customize — "
                "every unspecified section is filled with sensible platform defaults "
                "(calendar slots, filler lines, timeouts, objection patterns, stage skeleton). "
                "Fails if the slug already exists."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "slug: lowercase letters, digits, _ or -, e.g. 'salon_receptionist'"},
                    "definition": {"type": "object", "description": "partial agent definition JSON (identity, languages, call_flow, knowledge_base, response_bank, personality, llm_config)"},
                },
                "required": ["name", "definition"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_agent",
            "description": (
                "Edit an existing agent. Pass the MINIMAL partial changes — they are deep-merged "
                "onto the current full definition, so everything else is preserved. "
                "Nested objects merge; lists (e.g. call_flow.stages) are replaced whole."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "agent slug"},
                    "changes": {"type": "object", "description": "partial definition, e.g. {\"response_bank\": {\"opening_greeting\": \"...\"}}"},
                },
                "required": ["name", "changes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_mentions",
            "description": (
                "Find-and-replace a piece of text EVERYWHERE in an agent — greeting, stage goals, "
                "knowledge, canned responses, rules, everything at once (case-insensitive). "
                "ALWAYS use this instead of update_agent when the user renames or swaps something that "
                "appears in multiple places: a person (boss/owner), company name, product, city, phone "
                "number, price, or the agent's own name. This guarantees no stale mention survives."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "agent slug or spoken name"},
                    "find": {"type": "string", "description": "exact text to find, e.g. 'Mr. Ben'"},
                    "replace": {"type": "string", "description": "replacement text, e.g. 'Mr. Umang'"},
                    "dry_run": {"type": "boolean", "description": "true = only count/preview matches, write nothing"},
                },
                "required": ["name", "find", "replace"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_draft",
            "description": (
                "Record interview answers into the new-agent DRAFT (server-side, survives any "
                "context length). Call this IMMEDIATELY whenever the user gives information for "
                "a new agent — mine info dumps completely. Pass the same partial-definition "
                "shape as create_agent. Returns which topics are still open and the next "
                "question to ask. Set source='proposal' when recording defaults YOU proposed "
                "and the user accepted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "changes": {"type": "object", "description": "partial agent definition to merge into the draft"},
                    "source": {"type": "string", "enum": ["user", "proposal"]},
                },
                "required": ["changes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skip_layer",
            "description": (
                "Skip ONE interview topic because the USER explicitly declined it ('use defaults', "
                "'skip that'). Quote their exact words — this is audited. The only way to unblock "
                "create_agent without answering a compulsory topic."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "layer_id": {"type": "string", "description": "topic id from the INTERVIEW STATE / create refusal"},
                    "user_words": {"type": "string", "description": "the user's exact words declining this topic"},
                },
                "required": ["layer_id", "user_words"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ingest_document",
            "description": (
                "Read a document the user attached to this chat (doc_id from the attachment note) "
                "and pull its facts (products, prices, brand, greeting…) into the new-agent draft."
            ),
            "parameters": {
                "type": "object",
                "properties": {"doc_id": {"type": "string"}},
                "required": ["doc_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_update",
            "description": (
                "Prepare an edit WITHOUT applying it: builds the candidate, validates it, and "
                "returns a human-readable diff for the user to confirm. Use for any non-trivial "
                "edit. Provide either `changes` (partial definition, deep-merged) or `ops` "
                "(surgical path operations)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "agent slug or spoken name"},
                    "changes": {"type": "object", "description": "partial definition to merge"},
                    "ops": {"type": "array", "items": {"type": "object"},
                            "description": "surgical ops: [{op: set|append|remove_at|move, path, value?, to?}]"},
                    "reason": {"type": "string", "description": "one-line reason for the change"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_update",
            "description": "Apply a previously proposed change AFTER the user confirms. Commits with concurrency protection (409 if the agent changed since the proposal).",
            "parameters": {
                "type": "object",
                "properties": {"change_id": {"type": "string"}},
                "required": ["change_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "discard_update",
            "description": "Discard a previously proposed change the user rejected.",
            "parameters": {
                "type": "object",
                "properties": {"change_id": {"type": "string"}},
                "required": ["change_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_stage",
            "description": "Surgically edit ONE call-flow stage by id (goal, turns_until_advance, role, collect, advance_when, branches, label) — never resend the whole stage list.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "stage_id": {"type": "string"},
                    "fields": {"type": "object", "description": "only the stage fields to change"},
                },
                "required": ["name", "stage_id", "fields"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_stage",
            "description": "Insert one new call-flow stage (after `after_id`, or at the end). Orders are renumbered automatically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "stage": {"type": "object", "description": "the new stage {id, goal, ...}"},
                    "after_id": {"type": "string", "description": "existing stage id to insert after"},
                },
                "required": ["name", "stage"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_stage",
            "description": "Remove one call-flow stage by id. Orders are renumbered automatically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "stage_id": {"type": "string"},
                },
                "required": ["name", "stage_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reorder_stages",
            "description": "Reorder the call-flow stages. Pass the COMPLETE list of existing stage ids in the new order.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "ordered_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "ordered_ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_slot",
            "description": "Surgically edit ONE data-capture slot by name (question wording, type, priority, depends_on, merge_policy, confirm_readback, max_asks, ...).",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "slot_name": {"type": "string"},
                    "fields": {"type": "object"},
                },
                "required": ["name", "slot_name", "fields"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_slot",
            "description": "Add one data-capture slot (a piece of information the agent must collect).",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "slot": {"type": "object", "description": "the new slot {name, question: {lang: str}, ...}"},
                },
                "required": ["name", "slot"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_slot",
            "description": "Remove one data-capture slot by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "slot_name": {"type": "string"},
                },
                "required": ["name", "slot_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_revisions",
            "description": "List an agent's saved edit history (who changed it, when, why) — every change is reversible.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rollback_agent",
            "description": "Restore an agent to an earlier revision from list_revisions. Only after the user explicitly asks to undo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "rev_id": {"type": "integer"},
                },
                "required": ["name", "rev_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "activate_agent",
            "description": "Make this agent answer all inbound phone calls (writes agents/active_agent.json). Only call after the user explicitly confirms.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "agent slug"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_routing",
            "description": "Read an agent's call-routing rules (DID, caller-ID prefixes, time windows, fallback agent).",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "agent slug"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_routing",
            "description": "Write an agent's routing.yaml. Body keys: did, caller_id_prefixes, time_windows, fallback_agent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "agent slug"},
                    "routing": {"type": "object", "description": "routing config JSON"},
                },
                "required": ["name", "routing"],
            },
        },
    },
]


async def _execute_tool(tool: str, args: dict, guard: dict | None = None) -> tuple[Any, str, bool, bool]:
    """Run one tool. Returns (result, summary, ok, mutated_agents).

    `guard` carries per-turn safety state: {"user_turns": int, "created_this_turn": bool}.
    Two rules are enforced SERVER-SIDE (the LLM cannot bypass them):
      * create_agent is refused on the session's very first user message —
        the copilot must interview before building.
      * activate_agent is refused in the same turn as a successful
        create_agent — activation needs the user's explicit confirmation.
    """
    guard = guard or {}
    owner = builder._ctx_owner()

    # Tenant boundary for every tool that names an EXISTING agent: the LLM
    # cannot be trusted to respect ownership, so enforce it here regardless of
    # what slug it passes. create_agent is exempted (it claims a new slug).
    _named = str(args.get("name") or "").strip()
    if _named and tool != "create_agent":
        builder._assert_slug_access(_named, owner)

    if tool == "list_agents":
        res = await builder.list_agents()
        names = [a.get("name") for a in res.get("agents", [])]
        return res, f"Checked your {len(names)} agents (live: {res.get('active') or 'none'})", True, False

    if tool == "get_agent":
        name = str(args["name"]).strip()
        res = await builder.get_agent(name)
        return res, f"Looked at {_agent_display_name(name)}", True, False

    if tool == "validate_agent":
        notes: list[str] = []
        definition = _coerce_obj(args.get("definition") or {})
        definition.pop("name", None)
        _normalize_definition(definition, notes)
        merged = _default_definition("preview_agent", definition)
        _normalize_definition(_deep_merge(merged, definition))
        _enforce_invariants(merged, notes)
        agent = AgentDefinition.from_dict(merged)
        res = {
            "valid": True,
            "agent_id": agent.agent_id,
            "stages": [s.id for s in agent.call_flow.stages],
            "languages": agent.languages.supported,
            "notes": notes,
        }
        return res, "Checked the draft — it's valid", True, False

    if tool == "create_agent":
        if guard.get("user_turns", 99) < 2:
            raise ValueError(
                "Refused: this is the user's first message — interview them first. "
                "Ask ONE short clarifying question (persona, voice, greeting, or what "
                "the agent must know), and only create after they've answered and agreed."
            )
        # v2 hardened create gate (plan §6.2): every compulsory interview
        # layer must be complete or EXPLICITLY skipped. Dump-and-done is
        # mechanically impossible; "just build it" = per-layer skip_layer.
        _session = guard.get("session") or {}
        _iv = _interview_state(_session)
        _progress = interview_progress(_iv.get("draft") or {}, _iv.get("skipped") or [])
        if not _progress["complete"]:
            raise ValueError(
                "Refused: the interview is not finished — these layers are open: "
                + ", ".join(_progress["open_layers"])
                + f". Ask the next question now: {_progress['next_question']!r}. "
                "If the user explicitly said to skip a topic, call skip_layer with "
                "their words; only then can the agent be created."
            )
        name = str(args["name"]).strip().lower()
        notes: list[str] = []
        definition = _coerce_obj(args.get("definition") or {})
        definition.pop("name", None)
        for k in _SERVER_MANAGED_KEYS:  # audit fields are server-stamped only
            definition.pop(k, None)
        _normalize_definition(definition, notes)
        if not _SLUG_RE.match(name):
            raise ValueError("slug must be lowercase letters/digits with _ or - (2-61 chars)")
        target_dir = _AGENTS_DIR / name
        if target_dir.exists():
            raise ValueError(f"Agent '{name}' already exists — use update_agent to edit it")
        _existing_owner = builder._slug_owner(name)
        if _existing_owner and _existing_owner != owner and not builder._ctx_is_admin():
            raise ValueError(f"Agent '{name}' already exists")
        # v2: create CONSUMES the draft — everything captured during the
        # interview lands in the YAML even if the final tool call is thin and
        # the chat history was trimmed (fixes the 48-message context loss).
        _draft = copy.deepcopy(_iv.get("draft") or {})
        _draft.pop("_ingested_docs", None)
        merged = _default_definition(name, _deep_merge(copy.deepcopy(_draft), definition))
        _normalize_definition(_deep_merge(merged, _draft))
        _normalize_definition(_deep_merge(merged, definition))
        _enforce_invariants(merged, notes)
        agent = AgentDefinition.from_dict(merged)          # validate before any write
        target_dir.mkdir(parents=True, exist_ok=True)
        agent.to_yaml(target_dir / "agent.yaml")
        agent_registry.put(name, agent)
        try:
            builder._claim_slug(name, owner, agent.identity.agent_name)
        except Exception as exc:
            if not builder._ctx_is_admin():
                import shutil as _sh
                _sh.rmtree(target_dir, ignore_errors=True)
                agent_registry.invalidate(name)
                raise ValueError(f"could not register ownership: {exc}")
        logger.info("[copilot] created agent '%s' owner=%s (%d input repairs)", name, owner, len(notes))
        _iv["phase"] = "built"
        res = {
            "status": "created", "name": name, "agent_id": agent.agent_id,
            "notes": notes, "quality": _quality_warnings(merged),
            "provenance_confirmed": sum(1 for v in (_iv.get("provenance") or {}).values()
                                        if v == "confirmed"),
        }
        return res, f"Created {agent.identity.agent_name or name}", True, True

    if tool == "update_agent":
        name = str(args["name"]).strip()
        notes: list[str] = []
        changes = _coerce_obj(args.get("changes") or {})
        changes.pop("name", None)
        for k in _SERVER_MANAGED_KEYS:  # audit fields are server-stamped only
            changes.pop(k, None)
        _normalize_definition(changes, notes)
        # Sections that filtering emptied out carry no change — strip them so
        # a "nothing real left" update fails loudly instead of reporting
        # success while writing nothing.
        for k in [k for k, v in changes.items() if isinstance(v, dict) and not v]:
            changes.pop(k)
        if not changes:
            raise ValueError(
                "None of those changes map to real settings — nothing was saved. "
                + " ".join(notes)
            )
        yaml_path = _AGENTS_DIR / name / "agent.yaml"
        if not yaml_path.exists():
            raise ValueError(f"Agent '{name}' not found")
        # GET current full definition → deep-merge partial changes → check →
        # write through the store (§5.1). §5.4: normalization ran on the
        # CHANGES subtree only; whole-doc invariants are CHECKS that fail the
        # update (silent whole-doc mutation would read as collateral to the
        # store's scoped-diff proof).
        current, base_rev = _read_full_definition(name)
        merged = _deep_merge(copy.deepcopy(current), changes)
        violations = _invariant_violations(merged)
        if violations:
            raise ValueError("Refused — this change would break the agent: "
                             + "; ".join(violations))
        expected = _paths_of_changes(changes)
        result = agent_store.write_agent(
            name, merged, actor=_copilot_actor(), base_rev=base_rev,
            reason="copilot update: " + ", ".join(sorted(changes.keys()))[:180],
            owner=builder._ctx_owner(), is_admin=builder._ctx_is_admin(),
            expected_paths=expected, agents_dir=_AGENTS_DIR)
        logger.info("[copilot] updated agent '%s' (%s) rev=%s",
                    name, ", ".join(changes.keys()), result["revision"])
        res = {
            "status": "updated", "name": name, "changed_sections": sorted(changes.keys()),
            "notes": notes, "quality": _quality_warnings(merged),
            "revision": result["revision"], "effective": "next_call",
        }
        return res, f"Updated {_agent_display_name(name)} — {_friendly_sections(sorted(changes.keys()))}", True, True

    if tool == "replace_mentions":
        name = str(args["name"]).strip()
        find = str(args.get("find") or "")
        replacement = str(args.get("replace") or "")
        dry_run = bool(args.get("dry_run", False))
        if not find.strip():
            raise ValueError("'find' text is required")
        yaml_path = _AGENTS_DIR / name / "agent.yaml"
        if not yaml_path.exists():
            raise ValueError(f"Agent '{name}' not found")
        current, base_rev = _read_full_definition(name)
        pattern = re.compile(re.escape(find), re.IGNORECASE)
        # agent_id/version are internal identifiers — a persona rename must
        # never mutate them (they key the registry and document lookups).
        # revision/updated_* are server-managed.
        work = copy.deepcopy(current)
        preserved = {k: work.pop(k)
                     for k in ("agent_id", "version", "revision",
                               "updated_at", "updated_by") if k in work}
        replaced_tree, count = _replace_in_tree(work, pattern, replacement)
        replaced_tree.update(preserved)
        if count == 0:
            res = {"status": "no_matches", "name": name, "find": find, "replaced": 0}
            return res, f"'{find}' does not appear anywhere in {_agent_display_name(name)}", True, False
        match_paths = [
            d["path"] for d in _compute_diff(current, replaced_tree)
            if d["path"] not in ("revision", "updated_at", "updated_by")
        ]
        if dry_run:
            res = {"status": "dry_run", "name": name, "find": find,
                   "replace": replacement, "would_replace": count,
                   "paths": match_paths[:40]}
            return res, (f"'{find}' appears in {count} places in "
                         f"{_agent_display_name(name)} — not applied yet"), True, False
        AgentDefinition.from_dict(replaced_tree)   # validate before any write
        result = agent_store.write_agent(
            name, replaced_tree, actor=_copilot_actor(), base_rev=base_rev,
            reason=f"replace '{find[:60]}' -> '{replacement[:60]}'",
            owner=builder._ctx_owner(), is_admin=builder._ctx_is_admin(),
            agents_dir=_AGENTS_DIR)
        logger.info("[copilot] replace_mentions '%s': %r -> %r (%d)", name, find, replacement, count)
        res = {"status": "replaced", "name": name, "find": find,
               "replace": replacement, "replaced": count,
               "revision": result["revision"], "effective": "next_call"}
        return res, f"Replaced '{find}' with '{replacement}' in {count} places in {_agent_display_name(name)}", True, True

    if tool == "activate_agent":
        if guard.get("created_this_turn"):
            raise ValueError(
                "Refused: never activate in the same turn as creating an agent. "
                "Tell the user the agent was created and ASK whether to activate it "
                "(activation answers real inbound calls); activate only after their explicit yes."
            )
        if not builder._ctx_is_admin():
            raise ValueError(
                "Activation is platform-admin only. To route calls to this agent, "
                "bind your phone number to it in the Telephony panel.")
        name = str(args["name"]).strip()
        res = await builder.activate_agent(name)
        return res, f"{_agent_display_name(name)} is now answering calls", True, True

    if tool == "get_routing":
        name = str(args["name"]).strip()
        res = await builder.get_routing(name)
        return res, f"Checked call routing for {_agent_display_name(name)}", True, False

    if tool == "set_routing":
        name = str(args["name"]).strip()
        routing = _coerce_obj(args.get("routing") or {})
        if not (_AGENTS_DIR / name).exists():
            raise ValueError(f"Agent '{name}' not found")
        routing_path = _AGENTS_DIR / name / "routing.yaml"
        with open(routing_path, "w", encoding="utf-8") as f:
            yaml.dump(routing, f, allow_unicode=True, default_flow_style=False)
        logger.info("[copilot] updated routing for agent '%s'", name)
        return {"status": "updated"}, f"Updated call routing for {_agent_display_name(name)}", True, True

    # ── v2 interview engine (plan §6.2) ──────────────────────────────────────

    if tool == "update_draft":
        session = guard.get("session")
        if session is None:
            raise ValueError("no session for the draft")
        iv = _interview_state(session)
        changes = _coerce_obj(args.get("changes") or {})
        source = "derived" if str(args.get("source") or "") == "proposal" else "confirmed"
        notes: list[str] = []
        _normalize_definition(changes, notes)
        for k in [k for k, v in changes.items() if isinstance(v, dict) and not v]:
            changes.pop(k)
        if not changes:
            raise ValueError(
                "None of that mapped to real settings — nothing was recorded. " + " ".join(notes))
        _deep_merge(iv["draft"], changes)
        iv["phase"] = "interviewing"
        for p in sorted(_paths_of_changes(changes)):
            iv["provenance"][p] = source
        progress = interview_progress(iv["draft"], iv["skipped"])
        # Quality moves PRE-create: warn on the merged preview now, so thin
        # content is challenged during the interview, not after the write.
        preview = _default_definition("draft_preview", copy.deepcopy(iv["draft"]))
        _deep_merge(preview, copy.deepcopy(iv["draft"]))
        preview.pop("_ingested_docs", None)
        quality = _quality_warnings(preview)
        res = {
            "status": "draft_updated",
            "recorded_paths": sorted(_paths_of_changes(changes)),
            "notes": notes,
            "quality": quality,
            "progress": {
                "complete": progress["complete"],
                "open_layers": progress["open_layers"],
                "layers": [
                    {"id": l["id"], "title": l["title"], "done": l["done"],
                     "skipped": l["skipped"]} for l in progress["layers"]],
            },
            "next_layer_id": progress["next_layer_id"],
            "next_question": progress["next_question"],
        }
        done_n = sum(1 for l in progress["layers"] if l["done"] or l["skipped"])
        return (res, f"Noted — {done_n}/{len(progress['layers'])} topics covered",
                True, False)

    if tool == "skip_layer":
        session = guard.get("session")
        if session is None:
            raise ValueError("no session")
        iv = _interview_state(session)
        layer_id = str(args.get("layer_id") or "").strip()
        user_words = str(args.get("user_words") or "").strip()
        known = {l.id for l in INTERVIEW_LAYERS}
        if layer_id not in known:
            raise ValueError(f"No interview layer '{layer_id}' — layers: {', '.join(sorted(known))}")
        if not user_words:
            raise ValueError(
                "skip_layer needs the user's own words declining this topic "
                "(quote them) — it is auditable, not a shortcut for you.")
        if layer_id not in iv["skipped"]:
            iv["skipped"].append(layer_id)
        iv["provenance"][f"skip:{layer_id}"] = user_words[:200]
        iv["phase"] = "interviewing"
        progress = interview_progress(iv["draft"], iv["skipped"])
        res = {"status": "skipped", "layer_id": layer_id,
               "open_layers": progress["open_layers"],
               "next_question": progress["next_question"]}
        return res, f"Skipped the {layer_id.replace('_', ' ')} questions (user's call)", True, False

    if tool == "ingest_document":
        session = guard.get("session")
        sid = str(guard.get("sid") or "")
        if session is None or not sid:
            raise ValueError("no session for document ingestion")
        doc_id = str(args.get("doc_id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_\-.]{1,120}", doc_id):
            raise ValueError("invalid doc_id")
        doc_path = _UPLOADS_DIR / sid / doc_id
        if not doc_path.exists():
            raise ValueError(f"No uploaded document '{doc_id}' on this conversation")
        from backend.app.documents.extractor import extract_text
        content = doc_path.read_bytes()
        extracted = extract_text(content, doc_id)
        if not extracted.strip():
            raise ValueError("No text could be extracted from this document")
        from backend.app.documents.yaml_filler import extract_fields_from_text
        result = extract_fields_from_text(extracted)
        if result.get("status") != "ok":
            raise ValueError(result.get("error") or "extraction failed")
        fields = result.get("fields") or {}
        # Field-name → draft-path mapping (same shape update_draft expects).
        draft_changes: dict = {}
        _id_map = {"agent_name": "agent_name", "company_name": "company_name",
                   "company_tagline": "company_tagline",
                   "role_description": "role_description", "contact": "contact"}
        for src_key, id_key in _id_map.items():
            if fields.get(src_key):
                draft_changes.setdefault("identity", {})[id_key] = fields[src_key]
        kb_map = ("portfolio_prompt_text", "known_projects", "locations",
                  "objection_patterns", "catalog", "match_rules", "pricing_policy")
        for k in kb_map:
            if fields.get(k):
                draft_changes.setdefault("knowledge_base", {})[k] = fields[k]
        if fields.get("opening_greeting"):
            draft_changes.setdefault("response_bank", {})["opening_greeting"] = fields["opening_greeting"]
        if fields.get("call_direction"):
            draft_changes["call_direction"] = fields["call_direction"]
        for k in ("extra_always_rules", "extra_never_rules"):
            if fields.get(k):
                draft_changes.setdefault("llm_config", {})[k] = fields[k]
        for k in ("brand", "guardrails"):
            if isinstance(fields.get(k), dict):
                draft_changes[k] = fields[k]
        if fields.get("appointment_slots"):
            draft_changes.setdefault("calendar", {})["mock_slots"] = fields["appointment_slots"]
        notes: list[str] = []
        _normalize_definition(draft_changes, notes)
        iv = _interview_state(session)
        _deep_merge(iv["draft"], draft_changes)
        docs = iv["draft"].setdefault("_ingested_docs", [])
        if doc_id not in docs:
            docs.append(doc_id)
        for p in sorted(_paths_of_changes(draft_changes)):
            iv["provenance"][p] = "derived"
        iv["phase"] = "interviewing"
        progress = interview_progress(iv["draft"], iv["skipped"])
        res = {
            "status": "ingested", "doc_id": doc_id,
            "extracted_fields": sorted(k for k in fields if fields.get(k)),
            "missing": result.get("missing") or [],
            "notes": notes,
            "open_layers": progress["open_layers"],
            "next_question": progress["next_question"],
        }
        return res, f"Read the document and pulled {len(fields)} field(s) into the draft", True, False

    # ── v2 surgical edit engine (plan §5.3/§5.5) ─────────────────────────────

    def _write_scoped(name: str, merged: dict, base_rev: str, reason: str,
                      expected: set[str] | None) -> dict:
        return agent_store.write_agent(
            name, merged, actor=_copilot_actor(), base_rev=base_rev,
            reason=reason, owner=builder._ctx_owner(),
            is_admin=builder._ctx_is_admin(), expected_paths=expected,
            agents_dir=_AGENTS_DIR)

    if tool == "propose_update":
        name = str(args["name"]).strip()
        reason = str(args.get("reason") or "")
        notes: list[str] = []
        current, base_rev = _read_full_definition(name)
        ops = args.get("ops")
        if ops:
            if not isinstance(ops, list):
                raise ValueError("'ops' must be a list of operations")
            candidate = _apply_ops(current, ops)
            expected = sorted({str(o.get("path") or "").split("[")[0].rstrip(".")
                               for o in ops if o.get("path")})
        else:
            changes = _coerce_obj(args.get("changes") or {})
            changes.pop("name", None)
            for k in _SERVER_MANAGED_KEYS:
                changes.pop(k, None)
            _normalize_definition(changes, notes)
            for k in [k for k, v in changes.items() if isinstance(v, dict) and not v]:
                changes.pop(k)
            if not changes:
                raise ValueError("None of those changes map to real settings. " + " ".join(notes))
            candidate = _deep_merge(copy.deepcopy(current), changes)
            expected = sorted(_paths_of_changes(changes))
        violations = _invariant_violations(candidate)
        if violations:
            raise ValueError("Refused — this change would break the agent: "
                             + "; ".join(violations))
        AgentDefinition.from_dict(candidate)  # dry validation, no write
        diff = [d for d in _compute_diff(current, candidate)
                if d["path"] not in ("revision", "updated_at", "updated_by")]
        session = guard.get("session") or {}
        pending = session.setdefault("pending_changes", {})
        change_id = f"chg_{len(pending) + 1}_{abs(hash(name)) % 9999}"
        pending[change_id] = {
            "slug": name, "base_rev": base_rev, "candidate": candidate,
            "expected_paths": list(expected), "reason": reason,
            "diff_human": _render_diff(diff),
            "proposed_at_turn": guard.get("user_turns", 0),
        }
        res = {"status": "proposed", "change_id": change_id, "name": name,
               "diff": _render_diff(diff), "paths": expected,
               "notes": notes,
               "next": "Show the diff to the user; call apply_update after they confirm."}
        return res, f"Proposed a change to {_agent_display_name(name)} (awaiting confirmation)", True, False

    if tool == "apply_update":
        change_id = str(args.get("change_id") or "").strip()
        session = guard.get("session") or {}
        pending = session.get("pending_changes") or {}
        chg = pending.get(change_id)
        if not chg:
            raise ValueError(f"No pending change '{change_id}' — propose it first")
        # Server-side confirmation gate: proposed in a PRIOR turn, or the
        # triggering user message carries an explicit confirmation.
        msg = str(guard.get("user_message") or "").lower()
        confirmed_words = bool(re.search(
            r"\b(yes|yeah|apply|confirm|go ahead|do it|approve|haan|ok|okay|sure|ha)\b", msg))
        if guard.get("user_turns", 0) <= chg.get("proposed_at_turn", 0) and not confirmed_words:
            raise ValueError(
                "Refused: apply_update needs the user's confirmation — show the "
                "diff and apply only after they say yes.")
        name = chg["slug"]
        builder._assert_slug_access(name, owner)
        result = _write_scoped(name, chg["candidate"], chg["base_rev"],
                               chg.get("reason") or "copilot proposed change",
                               set(chg.get("expected_paths") or []) or None)
        pending.pop(change_id, None)
        res = {"status": "updated", "name": name, "change_id": change_id,
               "revision": result["revision"], "effective": "next_call"}
        return res, (f"Applied the change to {_agent_display_name(name)} — "
                     f"revision {result['revision']}, applies from the next call"), True, True

    if tool == "discard_update":
        change_id = str(args.get("change_id") or "").strip()
        session = guard.get("session") or {}
        pending = session.get("pending_changes") or {}
        if pending.pop(change_id, None) is None:
            raise ValueError(f"No pending change '{change_id}'")
        return ({"status": "discarded", "change_id": change_id},
                "Discarded the proposed change", True, False)

    if tool in ("update_stage", "add_stage", "remove_stage", "reorder_stages"):
        name = str(args["name"]).strip()
        current, base_rev = _read_full_definition(name)
        stages = list((current.get("call_flow") or {}).get("stages") or [])
        ids = [str(s.get("id") or "") for s in stages]
        if tool == "update_stage":
            sid = str(args.get("stage_id") or "").strip()
            if sid not in ids:
                raise ValueError(f"No stage '{sid}' — stages are: {', '.join(ids)}")
            fields = _coerce_obj(args.get("fields") or {})
            fields.pop("id", None)
            stage_keys = _fields_of(StageDefinition)
            fields = {k: v for k, v in fields.items() if k in stage_keys}
            if not fields:
                raise ValueError("No real stage fields to update")
            stages[ids.index(sid)] = {**stages[ids.index(sid)], **fields}
            reason = f"update stage {sid}: {', '.join(fields)}"
        elif tool == "add_stage":
            stage = _coerce_obj(args.get("stage") or {})
            sid = str(stage.get("id") or "").strip().upper().replace(" ", "_")
            if not sid:
                raise ValueError("stage.id is required")
            if sid in ids:
                raise ValueError(f"Stage '{sid}' already exists")
            stage["id"] = sid
            after_id = str(args.get("after_id") or "").strip()
            idx = ids.index(after_id) + 1 if after_id in ids else len(stages)
            stages.insert(idx, stage)
            reason = f"add stage {sid}"
        elif tool == "remove_stage":
            sid = str(args.get("stage_id") or "").strip()
            if sid not in ids:
                raise ValueError(f"No stage '{sid}' — stages are: {', '.join(ids)}")
            if len(stages) <= 1:
                raise ValueError("Cannot remove the last stage")
            stages.pop(ids.index(sid))
            reason = f"remove stage {sid}"
        else:  # reorder_stages
            ordered = [str(x) for x in (args.get("ordered_ids") or [])]
            if sorted(ordered) != sorted(ids):
                raise ValueError(
                    f"ordered_ids must contain exactly the existing stages: {', '.join(ids)}")
            by_id = {str(s.get("id")): s for s in stages}
            stages = [by_id[i] for i in ordered]
            reason = "reorder stages: " + " → ".join(ordered)
        for i, s in enumerate(stages):  # renumber order ints
            s["order"] = i
        candidate = copy.deepcopy(current)
        candidate.setdefault("call_flow", {})["stages"] = stages
        violations = _invariant_violations(candidate)
        if violations:
            raise ValueError("Refused: " + "; ".join(violations))
        diff = [d for d in _compute_diff(current, candidate)
                if d["path"] not in ("revision", "updated_at", "updated_by")]
        result = _write_scoped(name, candidate, base_rev, reason,
                               {"call_flow.stages"})
        res = {"status": "updated", "name": name, "diff": _render_diff(diff),
               "stages": [str(s.get("id")) for s in stages],
               "revision": result["revision"], "effective": "next_call"}
        return res, f"{reason} in {_agent_display_name(name)}", True, True

    if tool in ("update_slot", "add_slot", "remove_slot"):
        name = str(args["name"]).strip()
        current, base_rev = _read_full_definition(name)
        slots = list(current.get("slots") or [])
        names_l = [str(s.get("name") or "") for s in slots]
        if tool == "update_slot":
            sn = str(args.get("slot_name") or "").strip().lower()
            if sn not in names_l:
                raise ValueError(f"No slot '{sn}' — slots are: {', '.join(names_l) or 'none'}")
            fields = _coerce_obj(args.get("fields") or {})
            fields.pop("name", None)
            slot_keys = _fields_of(SlotDefinition)
            fields = {k: v for k, v in fields.items() if k in slot_keys}
            if not fields:
                raise ValueError("No real slot fields to update")
            slots[names_l.index(sn)] = {**slots[names_l.index(sn)], **fields}
            reason = f"update slot {sn}: {', '.join(fields)}"
        elif tool == "add_slot":
            slot = _coerce_obj(args.get("slot") or {})
            sn = str(slot.get("name") or "").strip().lower().replace(" ", "_")
            if not sn:
                raise ValueError("slot.name is required")
            if sn in names_l:
                raise ValueError(f"Slot '{sn}' already exists")
            slot["name"] = sn
            slots.append(slot)
            reason = f"add slot {sn}"
        else:
            sn = str(args.get("slot_name") or "").strip().lower()
            if sn not in names_l:
                raise ValueError(f"No slot '{sn}' — slots are: {', '.join(names_l) or 'none'}")
            slots.pop(names_l.index(sn))
            reason = f"remove slot {sn}"
        candidate = copy.deepcopy(current)
        candidate["slots"] = slots
        # Run slot-level value validation from the sanitizer on the subtree.
        notes: list[str] = []
        _normalize_definition({"slots": candidate["slots"]}, notes)
        AgentDefinition.from_dict(candidate)
        diff = [d for d in _compute_diff(current, candidate)
                if d["path"] not in ("revision", "updated_at", "updated_by")]
        result = _write_scoped(name, candidate, base_rev, reason, {"slots"})
        res = {"status": "updated", "name": name, "diff": _render_diff(diff),
               "slots": [str(s.get("name")) for s in slots], "notes": notes,
               "revision": result["revision"], "effective": "next_call"}
        return res, f"{reason} in {_agent_display_name(name)}", True, True

    if tool == "list_revisions":
        name = str(args["name"]).strip()
        revs = agent_store.list_revisions(name)
        res = {"revisions": [
            {"rev_id": r["id"], "actor": r["actor"], "reason": r["reason"],
             "created_at": str(r["created_at"])} for r in revs]}
        return res, f"Checked {_agent_display_name(name)}'s edit history", True, False

    if tool == "rollback_agent":
        name = str(args["name"]).strip()
        rev_id = int(args.get("rev_id") or 0)
        result = agent_store.rollback_agent(
            name, rev_id, actor=f"{owner}:rollback", owner=owner,
            is_admin=builder._ctx_is_admin(), agents_dir=_AGENTS_DIR)
        res = {"status": "rolled_back", "name": name, "rev_id": rev_id,
               "revision": result["revision"], "effective": "next_call"}
        return res, (f"Rolled {_agent_display_name(name)} back to revision "
                     f"{rev_id} — applies from the next call"), True, True

    raise ValueError(f"Unknown tool '{tool}'")


# ─────────────────────────────────────────────────────────────────────────────
# System prompt
# ─────────────────────────────────────────────────────────────────────────────
def _system_prompt(session: dict | None = None) -> str:
    # The global "active" agent is admin infrastructure (the platform-wide
    # inbound fallback) — never disclose it to a tenant, whose calls route by
    # their own DID binding, not this selector.
    active = (_read_active() or "none") if builder._ctx_is_admin() else "your own agents"
    # D12: when a new-agent interview is live, the server-computed INTERVIEW
    # STATE replaces the old static checklist — one owner for prompt budget.
    interview_block = ""
    if session is not None:
        iv = session.get("interview") or {}
        if iv.get("phase") == "interviewing" or iv.get("draft"):
            progress = interview_progress(iv.get("draft") or {}, iv.get("skipped") or [])
            interview_block = "\n\n" + render_interview_state(progress)
    return f"""You are the LesCall Agent Copilot — an expert voice-agent designer built into the LesCall dashboard. LesCall runs real-time AI phone agents for Indian businesses (Sarvam STT/TTS + an LLM). An agent is a YAML definition: identity, personality, languages (hi/en/gu), TTS voice, a staged call flow with branching, a knowledge base + product catalog, data-capture slots, canned responses, guardrails, and calendar slots. Inbound calls are answered by the agent bound to the dialed number (currently: {active}).

HOW YOU WORK
1. NEW AGENT — the interview is a MACHINE, not a vibe. The moment the user wants a new agent, start recording EVERY fact they give with update_draft (mine info dumps completely — never let information sit only in chat). An INTERVIEW STATE block appears below when a draft exists: it is the server's truth about which topics are done and the ONE next question to ask — follow it. Ask ONE short question per reply. Propose smart defaults instead of over-asking; after the business + objective are known, PROPOSE a tailored slot set, stage skeleton, and likely objections for that business type and record accepted proposals with update_draft(source='proposal'). Voices — female: simran, anushka, manisha, vidya; male: shubh, abhilash, karun. create_agent is REFUSED by the server until every compulsory topic is complete or the user explicitly declined it (then call skip_layer with their exact words). When all topics close, summarize the plan in 3-5 short lines and ask "shall I build it?".
2. Draft the copy YOURSELF, and make it DEEP — a thin agent is a failure. Every stage goal is 2-4 full sentences of concrete instructions (what to say, which talking point to use, what to accomplish before advancing) — never a 5-word label. The knowledge paragraph carries the real substance (it is injected on EVERY turn). Write real objection_responses. All copy is natural and phone-appropriate, in the agent's languages (greeting in the default language). For an outbound pitch, the mission and talking points MUST live in the stage goals and the knowledge paragraph — that is the only way they reach the live call.
3. Call create_agent with the customized fields — always include call_direction, a real knowledge_base.portfolio_prompt_text, substantive call_flow.stages, and (for outbound) objection_responses. The platform fills the rest (calendar, fillers, timeouts, objection patterns). Slug = lowercase with underscores.
4. RIGHT AFTER create_agent (and after any content edit): read the tool result. If it lists "quality" warnings, the agent is too shallow to run well — fix EVERY warning with update_agent in this SAME turn (richer stage goals, a fuller knowledge paragraph, real objection responses) before you say a word to the user. Only once the result comes back with no quality warnings do you tell the user it's built. Then confirm in one sentence and ASK whether to activate it (activation makes it answer REAL inbound phone calls). CRITICAL: call activate_agent ONLY after the user's explicit yes in their most recent message — never in the same turn as creation, never assumed. If they decline, that's fine — leave the current active agent alone and offer further edits.
5. EDITS — this must be BULLETPROOF:
   a. Users name agents by their spoken name ("Ananya") — pass that name as-is to tools; resolution to the right agent is automatic. If a tool says no such agent exists, tell the user plainly which agents DO exist and ask which they meant — never guess a different agent and never silently switch to the active one.
   b. Call get_agent first if you haven't read that agent THIS conversation, and re-read it before any edit you're not 100% sure about.
   c. RENAMES/SWAPS: when the user changes something that can appear in more than one place — a person (boss, owner, CEO), the company, a product, a city, a price, a phone number, or the agent's own name — you MUST use replace_mentions (find/replace across the whole agent), NOT update_agent. Example: "assistant for Mr. Ben → Mr. Umang" = replace_mentions(find="Mr. Ben", replace="Mr. Umang"). A partial edit that leaves old mentions in the greeting or responses is a FAILURE.
   d. After replace_mentions, if the replaced count seems low for how prominent the thing is, get_agent and check for other spellings/variants of the old text (e.g. "Ben", "Mr Ben", "बेन") and replace those too.
   e. Other edits: update_agent with the MINIMAL partial change (server-side merge keeps everything else). NEVER resend a whole list to edit one item: for one call stage use update_stage/add_stage/remove_stage/reorder_stages; for one collected-info question use update_slot/add_slot/remove_slot — these edit exactly that item on the server.
   e2. For bigger or riskier edits, prefer the two-step flow: propose_update (returns a diff — show it to the user in plain words) → apply_update after they confirm, or discard_update if they decline. Every change is versioned: list_revisions shows history and rollback_agent undoes a change when the user asks.
   f. After every successful edit, tell the user in one plain sentence WHAT is different now and that it applies from the next call (quote the revision number when the tool returns one). If nothing was changed in a turn, never claim it was.
   g. If the user says a change didn't take effect on a real call: do NOT speculate about caching or internal mechanisms. First list_agents to confirm the agent you've been editing IS the live one — if a different agent is live, say so plainly and ask whether to activate the right one. Then get_agent, hunt for the old wording, and fix every remaining mention. Special case: if the OLD OPENING LINE still played at connect, the greeting lives ONLY in response_bank.opening_greeting — check and fix THAT field; editing stage goals never changes what plays at connect.
6. QUESTIONS ("which agent is live?"): use list_agents / get_agent / get_routing. Do NOT call get_agent unless the user asks about that agent or you are about to edit it — during a new-agent interview no tool calls are needed until you create.

DEFINITION FIELDS you may set (all optional — deep-merged onto defaults). SHAPES ARE STRICT — get them right the first time:
- identity: agent_name, company_name, company_tagline, role_description, contact, role_line (full replacement identity sentence)
- personality: pronouns (she|he), energy (low|medium|high), tone, style, custom_instructions, traits [str], energy_mirroring (bool), ai_disclosure ("deflect"|"admit"|"admit_if_pressed"), verbosity_max_sentences (int)
- languages MUST be an OBJECT, never a list: {{"supported": ["hi","en"], "default": "hi", "allow_code_mixing": true, "stt_forced_language": "", "tts_voices": {{"hi": {{"model":"bulbul:v3","speaker":"simran","pace":1.15}}, "en": {{...}}}}}}. tts_voices lives INSIDE languages — never at the top level.
- call_direction: the STRING "inbound" or "outbound" — never a list. If the user wants BOTH, use "inbound" — the platform automatically applies the outbound approach whenever it places the call.
- call_flow: {{"call_goal": "one-sentence mission", "stages": [{{id, order, goal, turns_until_advance, role, collect: [slot names], advance_when: {{all_collect_filled|slots|min_turns|intent|appointment_captured}}, branches: [{{when_intent: affirmation|negation|question, goto: STAGE_ID, max_takes, actions: [capture_phone|promise_whatsapp_media|book_visit|schedule_callback]}}]}}], "scheduling": {{offer_count, style: exact_n|flexible}}, "callback_policy": {{enabled, capture_slot, responses: {{lang: str}}, counts_as_decline}}, "completed_when": {{required_slots, require_appointment}}}}. Stage roles: greeting|confirm_person|discovery|pitch|recommend|scheduling|confirmation|closing. Write goals in ENGLISH as instructions (the agent replies in the caller's language).
- slots (TOP-LEVEL list — the qualification schema, the heart of data capture): [{{name, priority (lower asked first), required, label, question: {{hi/en/gu: exact wording}}, type: text|number|money|enum|boolean|phone|date|time|location, enum_values, enum_synonyms, depends_on: [slot names never asked before], merge_policy: free|correction_gated|confirm, confirm_readback: ""|verbatim|digit_groups_2 (phone in 2-digit groups), max_asks (2 recommended), reask_question: {{lang}}}}]. EVERY new agent gets slots for what it must collect, with questions in EVERY supported language.
- knowledge_base: portfolio_prompt_text (plain-English paragraph — injected every turn), portfolio_section_label, locations, objection_patterns, known_projects {{"lowercase key": ["Canonical Name", "location or price"]}}, catalog [{{id, name, location, configurations, price_range, availability, positioning_tag, pitch_points, aliases, facts}}], match_rules [{{when: {{slot: value or "<=40L"}}, recommend: [ids], reason}}], pricing_policy {{mode: on_request|state_freely|defer, defer_line: {{lang}}}}, stt_corrections {{use_builtin_tables: false for non-real-estate agents}}
- brand: spoken_company_name, positioning_tagline, credibility_markers [str], trust_proofs [top 2-3 — objection ammo], regions_served, contact_phone, whatsapp_number
- response_bank: opening_greeting (ONE plain string in the default language), identity_responses {{lang: str}}, objection_responses {{lang: [str]}}, objection_playbook [{{id, patterns: [caller phrasings], answer: {{lang: str — may use {{trust_proofs}}}}, prompt_guidance}}], closing_responses {{lang: str}}, canned_phrases [{{patterns, responses: {{lang}}}}], graceful_close {{lang: str}}
- outbound (for outbound agents): identity_confirm_line/good_time_check_line/pitch/wrong_person_response/busy_response/wrong_number_close (all {{lang: str}}), wrong_number_patterns [str], opening_greeting {{lang}}
- guardrails: never_rules [str], always_rules [str], honesty {{unknown_answer_policy, forbidden_fabrications}}, compliance {{disclaimers, consent_line, dnd_phrases (["stop calling","remove my number"] recommended for new agents), restricted_topics}}, escalation {{enabled, triggers, handoff_message: {{lang}}}}
- follow_up: whatsapp_promise {{enabled, assets, promise_line: {{lang}}}}, callback {{enabled, default_delay_hours}}
- calendar: {{"type": "mock", "mock_slots": [{{"day": "Tomorrow", "time": "10:00 AM"}}], day_synonyms, booking_confirmation_line: {{lang}}}}
- post_call: outcomes [{{id, label, kind: success|progress|failure}}], success_definition {{mandatory_slots, gate_closing}}, scoring {{enabled, criteria_prompt, score_labels}}

THE OPENING LINE & PLACEHOLDERS (get this right — it burned a real user):
- The first thing spoken on EVERY call is response_bank.opening_greeting, word-for-word. Stage goals are INSTRUCTIONS to the AI (always written in English), never a script. When the user talks about the greeting / the first line / "as soon as the call connects", edit opening_greeting — editing a stage goal will NOT change what plays at connect.
- Exactly TWO placeholders exist, both single curly braces and ONLY inside opening_greeting:
  * {{contact_name}} — on outbound calls, replaced with the dialed contact's name from Outbound Calls (fallback "sir/ma'am"). When the user wants the agent to greet whoever it calls by name, write e.g. "Hello, am I speaking with {{contact_name}}?"
  * {{current_time}} — replaced at call time with morning/afternoon/evening (India time). E.g. "Good {{current_time}}, am I speaking with {{contact_name}}?" becomes "Good morning, am I speaking with Rajesh?". If the greeting is written in Hindi or Gujarati script, the word comes out in that language automatically.
- NEVER invent any other placeholder or syntax — ${{...}}, {{{{...}}}}, [name], <contact>, {{date}} do not exist and would be SPOKEN ALOUD literally. Placeholders in stage goals, knowledge text, or canned responses are never substituted (exception: {{trust_proofs}} inside objection_playbook answers).
If a tool result includes "notes", the platform auto-corrected or DROPPED some of your input — read them; if content was dropped, put it where the note says (usually a stage goal) with update_agent in the SAME turn.
If a tool result includes non-empty "quality" warnings, the agent is too shallow — you MUST resolve every one with update_agent in the SAME turn before telling the user it's ready. Do not report a hollow agent as done.
WHEN THE USER SAYS A CHANGE "isn't in the agent" or "wasn't implemented": do not just re-word the same field. get_agent, and translate their intent into the fields the CALL actually reads — the mission/talking points belong in call_flow.stages goals AND knowledge_base.portfolio_prompt_text (both are spoken), the pitch is not "implemented" if it only sits in one thin stage goal. Enrich those, then confirm what specifically changed.

TRUTHFULNESS (ABSOLUTE — violations are the worst possible failure):
- Only claim something was created/updated/activated if a tool result in THIS turn confirms it (status created/updated/activated/replaced). A tool result with status FAILED means NOTHING happened.
- After a FAILED result: fix the input and retry in the same turn if you can; otherwise tell the user plainly it didn't work. NEVER re-send an earlier success message after a failure.
- Never say "it's set up" / "it's activated" / "it will now..." unless you just did it, successfully, with a tool.
- After creating an agent it appears in the dashboard's Agents tab immediately — if the user says they don't see it, list the agents to check whether it truly exists before saying anything else.

STYLE — PLAIN LANGUAGE ONLY (the user is a business person, not a developer):
- Replies are SHORT: 1-3 sentences, at most ONE question, plain conversational text. No markdown headers, no YAML/JSON at the user, no bullet walls (a brief 3-5 line summary before creating is fine).
- NEVER mention tools or tool names (update_agent, get_agent, replace_mentions...), YAML, JSON, "definition", "partial changes", "deep-merge", "knowledge base fields", slugs, caching, or ANY internal machinery. Say "I'll update her greeting", "Done — Ananya now introduces herself as Mr. Umang's assistant", never "I'll use the update_agent tool to apply these changes".
- Never tell the user to run a tool or "check the agent definition" — YOU do the checking and report the result.
- Describe agents by what callers experience: the greeting, how she speaks, what she knows, when she books meetings.
- When the user says they're done, close warmly in one sentence — do not claim any additional update happened.
- NEVER write out an imagined transcript of a future call ("The conversation will proceed as follows: ..."). It reads as fact, goes stale instantly, and hides what actually changed. State in 1-2 sentences WHAT changed; quote at most the exact opening greeting.
- Never invent agents or facts — check with tools first, silently.{interview_block}"""


# ─────────────────────────────────────────────────────────────────────────────
# Sessions
# ─────────────────────────────────────────────────────────────────────────────
_SESSIONS: dict[str, dict] = {}  # sid -> {"messages": [...], "transcript": [...], "meta": {...}}

# Tools that tie a conversation to a specific agent. Mutating tools always
# re-point the session's agent; read tools only set it when none is known yet
# (so "also show me pramukh" mid-edit doesn't rename the chat).
_AGENT_MUTATING_TOOLS = {"create_agent", "update_agent", "activate_agent", "set_routing",
                         "replace_mentions", "apply_update", "update_stage", "add_stage",
                         "remove_stage", "reorder_stages", "update_slot", "add_slot",
                         "remove_slot", "rollback_agent"}
_AGENT_READING_TOOLS = {"get_agent", "get_routing"}


def _session_path(sid: str) -> Path:
    return _SESSIONS_DIR / f"{sid}.json"


def _session_owner(sid: str) -> str | None:
    """Owner recorded on a session's meta (None for legacy/never-saved)."""
    try:
        if sid in _SESSIONS:
            return _SESSIONS[sid].get("meta", {}).get("owner")
        path = _session_path(sid)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8")) or {}
            return data.get("meta", {}).get("owner")
    except Exception:
        pass
    return None


def _session_exists(sid: str) -> bool:
    return sid in _SESSIONS or _session_path(sid).exists()


def _tenant_sid(owner: str | None, sid: str) -> str:
    """A stable per-tenant session id derived from a stale/foreign one, so a
    client holding an inaccessible session id gets its OWN continuous chat
    instead of an error — and never reaches the original session."""
    import hashlib
    h = hashlib.sha1(f"{owner or ''}\x00{sid}".encode("utf-8")).hexdigest()[:20]
    return f"u{h}"


def _session_owner_ok(sid: str, owner: str) -> bool:
    """Who may open a session.

    * admin: any session.
    * a session that does NOT exist yet: claimable — this request creates it.
    * an existing session: ONLY its recorded owner.

    A legacy session with no owner (owner=None) is treated as the platform
    admin's — NOT visible to tenants. (The old code let owner=None be seen by
    everyone, which leaked every pre-tenancy chat to all tenants.)
    """
    import auth
    if auth.is_admin(owner):
        return True
    if not _session_exists(sid):
        return True
    return _session_owner(sid) == owner


def _load_session(sid: str) -> dict:
    if sid in _SESSIONS:
        return _SESSIONS[sid]
    session = {"messages": [], "transcript": [], "meta": {}}
    path = _session_path(sid)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8")) or {}
            session["messages"] = data.get("messages", [])
            session["transcript"] = data.get("transcript", [])
            session["meta"] = data.get("meta", {})
            if data.get("pending_changes"):
                session["pending_changes"] = data["pending_changes"]
            if data.get("interview"):
                session["interview"] = data["interview"]
            if data.get("owner"):
                session["owner"] = data["owner"]
        except Exception as exc:  # pragma: no cover - corrupt file: start fresh
            logger.warning("[copilot] could not load session %s: %r", sid, exc)
    _SESSIONS[sid] = session
    return session


def _now_iso() -> str:
    import datetime
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _agent_display_name(slug: str) -> str:
    """Pretty title for a chat tied to an agent: its spoken name from the YAML."""
    try:
        agent = agent_registry.get(slug, _AGENTS_DIR)
        return agent.identity.agent_name or slug
    except Exception:
        return slug


def _session_summary(sid: str, session: dict, mtime: float | None = None) -> dict:
    import datetime

    meta = session.get("meta") or {}
    transcript = session.get("transcript") or []
    agent = meta.get("agent") or ""
    title = meta.get("title") or ""
    if not title:
        if agent:
            title = _agent_display_name(agent)
        else:
            first_user = next((m.get("content", "") for m in transcript if m.get("role") == "user"), "")
            title = (first_user[:48] + "…") if len(first_user) > 48 else (first_user or "New chat")
    updated = meta.get("updated_at")
    if not updated and mtime:
        updated = datetime.datetime.utcfromtimestamp(mtime).isoformat(timespec="seconds") + "Z"
    return {
        "session_id": sid,
        "title": title,
        "agent": agent or None,
        "agent_name": _agent_display_name(agent) if agent else None,
        "created_at": meta.get("created_at"),
        "updated_at": updated,
        "message_count": sum(1 for m in transcript if m.get("role") == "user"),
    }


def _save_session(sid: str) -> None:
    try:
        _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        _session_path(sid).write_text(
            json.dumps(_SESSIONS[sid], ensure_ascii=False, indent=1), encoding="utf-8"
        )
    except Exception as exc:  # pragma: no cover - persistence is best effort
        logger.warning("[copilot] could not persist session %s: %r", sid, exc)


def _context_window(messages: list[dict]) -> list[dict]:
    """Trim long sessions; cut at a user-message boundary so assistant
    tool_calls are never separated from their tool results. Old bulky tool
    results (full agent definitions, ~6KB each) are compacted to keep the
    request inside Groq's tokens-per-minute budget."""
    if len(messages) > _CONTEXT_MAX_MESSAGES:
        tail = messages[-_CONTEXT_MAX_MESSAGES:]
        for i, m in enumerate(tail):
            if m.get("role") == "user":
                tail = tail[i:]
                break
        else:
            tail = list(tail)
        # No user boundary found (or boundary search left orphans): drop
        # leading tool results / tool-calling assistant turns whose parents
        # were cut off — the API rejects tool messages without their parent.
        while tail and (
            tail[0].get("role") == "tool"
            or (tail[0].get("role") == "assistant" and tail[0].get("tool_calls"))
        ):
            tail.pop(0)
    else:
        tail = list(messages)

    # Index of the current turn's user message (last user msg): tool results
    # from THIS turn stay full; older bulky tool dumps get compacted.
    last_user = max((i for i, m in enumerate(tail) if m.get("role") == "user"), default=0)
    out: list[dict] = []
    for i, m in enumerate(tail):
        content = m.get("content") or ""
        if m.get("role") == "tool" and i < last_user and len(content) > 900:
            m = {**m, "content": content[:900] + " …[truncated — call the tool again if you need the full data]"}
        out.append(m)
    return out


def _fallback_model_name() -> str:
    """Second Groq model used when the primary exhausts its daily token quota.
    Groq rate limits are PER MODEL, so a sibling model has its own fresh
    tokens-per-day bucket. Empty string disables the fallback."""
    # llama-3.3-70b shares its daily budget with live calls, but it is the only
    # account model whose per-minute limit fits a full copilot request; it is
    # only touched when the primary model's quota is already exhausted.
    return _env("LLM_FALLBACK_MODEL", "llama-3.3-70b-versatile")


_oai_client = None


def _openai_fallback() -> tuple[Any, str] | None:
    """Last-resort cross-provider fallback: OpenAI with the voice pipeline's
    existing OPENAI_API_KEY (already used for Whisper). Paid per-token, no
    daily quota — used only when every Groq bucket is dry."""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    model = _env("LLM_FALLBACK2_MODEL", "gpt-4o-mini")
    if not key or not model:
        return None
    global _oai_client
    if _oai_client is None:
        from openai import AsyncOpenAI
        _oai_client = AsyncOpenAI(api_key=key, timeout=120.0, max_retries=3)
    return _oai_client, model


async def _chat_completion(client, model: str, messages: list[dict]):
    """One LLM call with retries for Groq's `tool_use_failed` (the model emitted
    a malformed/truncated tool call — a known llama flakiness). On failure we
    re-ask with a corrective nudge appended; the nudge is never persisted.
    On a 429 (daily token quota exhausted) the call walks a fallback chain:
    sibling Groq model (separate per-model quota), then OpenAI gpt-4o-mini."""
    from openai import BadRequestError, RateLimitError

    async def _attempt(cli, mdl: str):
        nudge: list[dict] = []
        last_exc: Exception | None = None
        for _ in range(3):
            try:
                completion = await cli.chat.completions.create(
                    model=mdl,
                    messages=messages + nudge,
                    tools=TOOLS,
                    tool_choice="auto",
                    temperature=0.3,
                    max_tokens=4096,
                )
                return completion.choices[0].message
            except BadRequestError as exc:
                if "tool_use_failed" not in str(exc):
                    raise
                last_exc = exc
                nudge = [{
                    "role": "system",
                    "content": (
                        "Your previous tool call was REJECTED because its JSON was malformed or "
                        "truncated. Call the tool again with strictly valid, COMPLETE JSON. "
                        "Keep the definition compact — rely on platform defaults for anything "
                        "you don't need to customize."
                    ),
                }]
        raise last_exc  # type: ignore[misc]

    chain: list[tuple[Any, str]] = [(client, model)]
    fallback = _fallback_model_name()
    if fallback and fallback != model:
        chain.append((client, fallback))
    oai = _openai_fallback()
    if oai:
        chain.append(oai)

    last_rate_exc: Exception | None = None
    for i, (cli, mdl) in enumerate(chain):
        try:
            return await _attempt(cli, mdl)
        except RateLimitError as exc:
            last_rate_exc = exc
            if i + 1 < len(chain):
                logger.warning("[copilot] %s rate-limited — retrying on fallback %s", mdl, chain[i + 1][1])
    raise last_rate_exc  # type: ignore[misc]


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────
@router.post("/api/copilot/chat")
async def copilot_chat(request: Request) -> dict:
    """One conversational turn. Runs the agentic tool loop server-side."""
    body = await request.json()
    sid = str(body.get("session_id") or "").strip()
    message = str(body.get("message") or "").strip()
    if not _SESSION_ID_RE.match(sid):
        raise HTTPException(status_code=400, detail="invalid session_id")
    if not message:
        raise HTTPException(status_code=400, detail="'message' is required")
    # v2: attached documents ride along as doc ids; the LLM ingests them via
    # ingest_document (the note is machine-added, not user text).
    attachments = [str(a) for a in (body.get("attachments") or [])
                   if re.fullmatch(r"[A-Za-z0-9_\-.]{1,120}", str(a))]
    if attachments:
        message += "\n\n[The user attached document(s): " + \
            ", ".join(f"doc_id={a}" for a in attachments) + \
            " — call ingest_document on each to read them into the draft.]"

    # session_id arrives in the BODY, so the router dependency (which only sees
    # path params) can't gate it — enforce ownership here. Blocks continuing or
    # reading another tenant's chat.
    #
    # But a client may hold a STALE session id (a pre-tenancy chat, or one left
    # by another account on a shared browser). Instead of erroring, transparently
    # remap it to THIS tenant's own stable session derived from that id — so even
    # an old cached frontend keeps working, with full continuity, and never
    # touches the foreign/legacy chat. The response carries the real (possibly
    # remapped) session id for the client to adopt.
    owner = getattr(request.state, "owner", None)
    if not _session_owner_ok(sid, owner):
        sid = _tenant_sid(owner, sid)

    client = _get_client()
    model = _model_name()
    session = _load_session(sid)
    # Stamp the tenant on the session so it's scoped from its first turn.
    session.setdefault("meta", {}).setdefault("owner", owner)
    # Snapshot for exact rollback — popping single messages on error can
    # orphan an assistant tool_calls message and brick the session history.
    mark_messages = len(session["messages"])
    mark_transcript = len(session["transcript"])
    session["messages"].append({"role": "user", "content": message})
    session["transcript"].append({"role": "user", "content": message})

    actions: list[dict] = []
    agents_changed = False
    reply = ""
    guard = {
        "user_turns": sum(1 for m in session["messages"] if m.get("role") == "user"),
        "created_this_turn": False,
        # v2 edit engine: pending-change storage + confirmation gating.
        "session": session,
        "user_message": message,
        "sid": sid,
    }

    try:
        for _ in range(_MAX_TOOL_ITERATIONS):
            msg = await _chat_completion(
                client,
                model,
                [{"role": "system", "content": _system_prompt(session)}]
                + _context_window(session["messages"]),
            )

            if not msg.tool_calls:
                reply = (msg.content or "").strip()
                break

            session["messages"].append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ],
            })

            for tc in msg.tool_calls:
                tool_name = tc.function.name
                try:
                    args = _parse_tool_args(tc.function.arguments)
                    # Users say "Ananya", the filesystem says "dhronai_ea" —
                    # resolve BEFORE execution so every tool (and the session
                    # title below) works from the real slug.
                    if tool_name in _TOOLS_WITH_AGENT_NAME and args.get("name"):
                        args["name"] = _resolve_agent_slug(args["name"])
                    result, summary, ok, mutated = await _execute_tool(tool_name, args, guard)
                    if tool_name == "create_agent" and ok:
                        guard["created_this_turn"] = True
                    # Tie this chat to the agent it's working on (names the
                    # conversation in the sidebar, ChatGPT-style).
                    if ok and args.get("name"):
                        meta = session.setdefault("meta", {})
                        # Prefer the slug the tool actually wrote (create
                        # lower-cases/strips its input) over the raw argument.
                        slug = (result.get("name") if isinstance(result, dict) else None) \
                            or str(args["name"]).strip()
                        if tool_name in _AGENT_MUTATING_TOOLS or (
                            tool_name in _AGENT_READING_TOOLS and not meta.get("agent")
                        ):
                            meta["agent"] = slug
                            meta["title"] = _agent_display_name(slug)
                except HTTPException as exc:
                    result, summary, ok, mutated = (
                        _failure_payload(tool_name, str(exc.detail)),
                        f"Couldn't {_TOOL_VERBS.get(tool_name, tool_name)}: {exc.detail}", False, False,
                    )
                except Exception as exc:
                    result, summary, ok, mutated = (
                        _failure_payload(tool_name, str(exc)),
                        f"Couldn't {_TOOL_VERBS.get(tool_name, tool_name)}: {exc}", False, False,
                    )
                agents_changed = agents_changed or mutated
                actions.append({
                    "tool": tool_name, "summary": summary, "ok": ok,
                    "agent": str(args.get("name") or "").strip().lower(),
                })
                session["messages"].append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tool_name,
                    "content": json.dumps(result, ensure_ascii=False, default=str)[:20000],
                })
        else:
            reply = "I ran out of steps trying to do that — could you rephrase or break it into smaller requests?"
    except Exception as exc:
        ok_saves_so_far = [a for a in actions if a["ok"] and a["tool"] in _MUTATING_TOOLS]
        if ok_saves_so_far:
            # Real changes already landed before the LLM died — report them
            # truthfully instead of erasing the record.
            logger.exception("[copilot] LLM failed mid-turn after successful changes")
            done = "; ".join(a["summary"] for a in ok_saves_so_far)
            reply = (
                f"I applied this change: {done}. Then I hit a connection problem before I could finish "
                "replying — the change IS saved. Please continue when ready."
            )
        else:
            # Nothing changed — roll history back to exactly before this turn
            # so no dangling tool-call messages can brick the session.
            del session["messages"][mark_messages:]
            del session["transcript"][mark_transcript:]
            if isinstance(exc, HTTPException):
                raise
            logger.exception("[copilot] LLM turn failed")
            from openai import RateLimitError
            if isinstance(exc, RateLimitError):
                # The frontend only shows the HTTP status text, so a quota
                # outage must come back as a normal reply, not an error code.
                m = re.search(r"try again in ([0-9]+m)?([0-9.]+s)?", str(exc))
                wait = f" in about {(m.group(1) or '')}{('' if m.group(1) else m.group(2) or '')}".rstrip() if m and (m.group(1) or m.group(2)) else " later"
                reply = (
                    "The AI service's free daily token quota is used up on both the primary and "
                    f"fallback models, so I can't process this message right now. Please try again{wait} "
                    "— your agents and this conversation are safe, nothing was lost. "
                    "(To remove this limit, upgrade the Groq account tier or point COPILOT_LLM_MODEL "
                    "at a different provider.)"
                )
                # Keep the user's message visible in the saved transcript (the
                # LLM history stays rolled back so a retry re-sends it cleanly).
                session["transcript"].append({"role": "user", "content": message})
                session["transcript"].append({"role": "assistant", "content": reply, "actions": []})
                _save_session(sid)
                return {
                    "reply": reply,
                    "actions": [],
                    "agents_changed": False,
                    "session": _session_summary(sid, session),
                }
            raise HTTPException(status_code=502, detail=f"Copilot LLM error: {exc}")

    if not reply:
        reply = "Done." if actions else "Sorry, I didn't produce a reply — please try again."

    # ── Truthfulness backstop (deterministic — the LLM cannot bypass it) ────
    # If every attempted save/create/activate FAILED this turn, say so in a
    # status line regardless of what the model wrote. Redundant next to an
    # honest reply; load-bearing next to a dishonest one.
    failed_saves = [a for a in actions if not a["ok"] and a["tool"] in _MUTATING_TOOLS]
    ok_saves = [a for a in actions if a["ok"] and a["tool"] in _MUTATING_TOOLS]
    if failed_saves and not ok_saves:
        reply += "\n\n⚠️ Nothing was saved in this step — the change did not go through."
        logger.warning("[copilot] truthfulness backstop fired (sid=%s)", sid)

    # ── Hide self-corrected retries from the user-facing action list ────────
    # The LLM often gets a save rejected (e.g. invented setting name), reads
    # the error, and immediately retries correctly. A "Couldn't update …"
    # chip next to successful "Updated …" chips reads as data loss when the
    # change actually landed. Drop a failed save ONLY when a later successful
    # save on the SAME agent exists in this turn; genuine unrecovered
    # failures stay visible (and the backstop above still fires on them).
    visible_actions = [
        a for i, a in enumerate(actions)
        if a["ok"] or a["tool"] not in _MUTATING_TOOLS or not any(
            b["ok"] and b["tool"] in _MUTATING_TOOLS and b.get("agent") == a.get("agent")
            for b in actions[i + 1:]
        )
    ]

    session["messages"].append({"role": "assistant", "content": reply})
    session["transcript"].append({"role": "assistant", "content": reply, "actions": visible_actions})
    meta = session.setdefault("meta", {})
    meta.setdefault("created_at", _now_iso())
    meta["updated_at"] = _now_iso()
    _save_session(sid)
    # v2 additive API contract (old bundles ignore these):
    # - interview: server-computed progress for the progress rail + preview
    # - pending_changes: proposed diffs for Apply/Discard cards
    out: dict = {
        "reply": reply,
        "actions": visible_actions,
        "agents_changed": agents_changed,
        "session": _session_summary(sid, session),
    }
    iv = session.get("interview") or {}
    if iv.get("phase") in ("interviewing", "built") or iv.get("draft"):
        progress = interview_progress(iv.get("draft") or {}, iv.get("skipped") or [])
        out["interview"] = {
            "phase": iv.get("phase") or "idle",
            "complete": progress["complete"],
            "layers": [{"id": l["id"], "title": l["title"], "done": l["done"],
                        "skipped": l["skipped"]} for l in progress["layers"]],
            "next_layer_id": progress["next_layer_id"],
            "draft": iv.get("draft") or {},
        }
    pend = session.get("pending_changes") or {}
    if pend:
        out["pending_changes"] = [
            {"change_id": cid, "slug": c.get("slug"),
             "diff": c.get("diff_human"), "reason": c.get("reason")}
            for cid, c in pend.items()]
    return out


@router.post("/api/copilot/sessions/{session_id}/changes/{change_id}/apply")
async def copilot_apply_change(session_id: str, change_id: str, request: Request) -> dict:
    """Diff-card Apply button: the click IS the user's confirmation (§7.3)."""
    if not _SESSION_ID_RE.match(session_id):
        raise HTTPException(status_code=400, detail="invalid session_id")
    session = _load_session(session_id)
    pending = session.get("pending_changes") or {}
    chg = pending.get(change_id)
    if not chg:
        raise HTTPException(status_code=404, detail="no such pending change")
    slug = chg["slug"]
    builder._assert_slug_access(slug, builder._ctx_owner())
    try:
        result = agent_store.write_agent(
            slug, chg["candidate"], actor=_copilot_actor(),
            base_rev=chg.get("base_rev"),
            reason=chg.get("reason") or "diff-card apply",
            owner=builder._ctx_owner(), is_admin=builder._ctx_is_admin(),
            expected_paths=set(chg.get("expected_paths") or []) or None,
            agents_dir=_AGENTS_DIR)
    except agent_store.ConflictError:
        raise HTTPException(status_code=409, detail=
                            "This agent changed since the proposal — ask the "
                            "copilot to re-propose the change.")
    pending.pop(change_id, None)
    session["transcript"].append({
        "role": "assistant",
        "content": f"Applied the proposed change — revision {result['revision']}, "
                   "applies from the next call.",
        "actions": [{"tool": "apply_update", "ok": True,
                     "summary": f"Applied change to {_agent_display_name(slug)}",
                     "agent": slug}]})
    _save_session(session_id)
    return {"status": "updated", "name": slug, "revision": result["revision"],
            "effective": "next_call"}


@router.post("/api/copilot/sessions/{session_id}/changes/{change_id}/discard")
async def copilot_discard_change(session_id: str, change_id: str) -> dict:
    if not _SESSION_ID_RE.match(session_id):
        raise HTTPException(status_code=400, detail="invalid session_id")
    session = _load_session(session_id)
    pending = session.get("pending_changes") or {}
    if pending.pop(change_id, None) is None:
        raise HTTPException(status_code=404, detail="no such pending change")
    _save_session(session_id)
    return {"status": "discarded", "change_id": change_id}


@router.post("/api/copilot/upload")
async def copilot_upload(request: Request) -> dict:
    """Attach a document to a Copilot conversation (plan §6.4). Multipart:
    session_id + file. Returns {doc_id} for the chat body's `attachments`."""
    form = await request.form()
    sid = str(form.get("session_id") or "").strip()
    upload = form.get("file")
    if not _SESSION_ID_RE.match(sid):
        raise HTTPException(status_code=400, detail="invalid session_id")
    owner = getattr(request.state, "owner", None)
    if not _session_owner_ok(sid, owner):
        sid = _tenant_sid(owner, sid)
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(status_code=400, detail="'file' is required")
    content = await upload.read()
    if not content:
        raise HTTPException(status_code=400, detail="uploaded file is empty")
    if len(content) > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file too large (15 MB max)")
    import time as _time
    safe_name = re.sub(r"[^A-Za-z0-9_.\-]", "_",
                       getattr(upload, "filename", "") or "document.txt")[-80:]
    doc_id = f"{int(_time.time())}_{safe_name}"
    dest = _UPLOADS_DIR / sid
    dest.mkdir(parents=True, exist_ok=True)
    (dest / doc_id).write_bytes(content)
    logger.info("[copilot] upload %s/%s (%d bytes)", sid, doc_id, len(content))
    return {"status": "uploaded", "doc_id": doc_id, "session_id": sid,
            "note": "pass this doc_id in the chat body's `attachments` list"}


@router.get("/api/copilot/sessions")
async def copilot_sessions(request: Request) -> dict:
    """The CALLER's saved conversations, newest first (admins see all).
    Empty sessions (no user message yet) are skipped."""
    import auth
    owner = getattr(request.state, "owner", None) or auth.DEFAULT_OWNER
    show_all = auth.is_admin(owner)

    def _mine(session: dict) -> bool:
        # Admins see all; tenants see ONLY sessions they own. Legacy/unowned
        # (owner=None) sessions belong to the admin — never shown to tenants.
        return show_all or session.get("meta", {}).get("owner") == owner

    summaries: list[dict] = []
    seen: set[str] = set()
    try:
        files = sorted(_SESSIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:
        files = []
    for path in files:
        sid = path.stem
        if not _SESSION_ID_RE.match(sid):
            continue
        try:
            session = _load_session(sid)
            if not session.get("transcript") or not _mine(session):
                continue
            summaries.append(_session_summary(sid, session, mtime=path.stat().st_mtime))
            seen.add(sid)
        except Exception as exc:  # pragma: no cover - skip corrupt files
            logger.warning("[copilot] skipping session %s: %r", sid, exc)
    # In-memory sessions not yet flushed to disk (first turn in progress)
    for sid, session in _SESSIONS.items():
        if sid not in seen and session.get("transcript") and _mine(session):
            summaries.append(_session_summary(sid, session))
    summaries.sort(key=lambda s: s.get("updated_at") or "", reverse=True)
    return {"sessions": summaries}


@router.delete("/api/copilot/sessions/{session_id}")
async def copilot_delete_session(session_id: str) -> dict:
    """Delete a conversation (sidebar 'remove chat')."""
    if not _SESSION_ID_RE.match(session_id):
        raise HTTPException(status_code=400, detail="invalid session_id")
    _SESSIONS.pop(session_id, None)
    path = _session_path(session_id)
    if path.exists():
        try:
            path.unlink()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"could not delete session: {exc}")
    return {"status": "deleted", "session_id": session_id}


@router.get("/api/copilot/sessions/{session_id}")
async def copilot_session(session_id: str) -> dict:
    """Return the UI transcript for a session (page-reload restore)."""
    if not _SESSION_ID_RE.match(session_id):
        raise HTTPException(status_code=400, detail="invalid session_id")
    session = _load_session(session_id)
    return {
        "session_id": session_id,
        "transcript": session["transcript"],
        "meta": session.get("meta", {}),
    }
