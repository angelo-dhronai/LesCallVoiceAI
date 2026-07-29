"""
Native LLM tool calling for live calls — deploy/PLAN-llm-tool-calling.md.

Three modes, resolved per agent (`llm_config.enable_tools`) with a global
env kill switch (`LLM_TOOLS_MODE=off|shadow|on` overrides every agent):

- off:    nothing changes.
- shadow: the SPOKEN path is byte-identical (no tools on the main call —
          the P0 probe showed Gemini's compat shim returns tool calls
          INSTEAD of text, which would create silent turns). Instead the
          background slot extractor declares the tool roster: its
          record_customer_details proposals merge fill-only exactly like
          the old json_object extractor, and ACTION proposals are logged
          (`[TOOLS] shadow …`) for the P1 agreement metric.
- on:     tools are declared on the main streaming call; text still
          streams first; proposals execute at end-of-stream through the
          deterministic gates in ConversationRouter._execute_tool_proposals
          (fill-only slots, stage legality, turn-transaction deferral).

Compat quirk handled here (verified live 2026-07-29): the Gemini
OpenAI-compat shim streams `tool_calls` deltas with `index=None`, so
parallel calls fuse under index-keyed accumulation. ToolCallAccumulator
starts a new call whenever a delta carries a function name instead.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MODE_OFF = "off"
MODE_SHADOW = "shadow"
MODE_ON = "on"
_MODES = (MODE_OFF, MODE_SHADOW, MODE_ON)


def resolve_tools_mode(agent_def) -> str:
    """Env kill switch first, then the per-agent YAML flag."""
    env = os.environ.get("LLM_TOOLS_MODE", "").strip().lower()
    if env in _MODES:
        return env
    mode = str(getattr(agent_def.llm_config, "enable_tools", MODE_SHADOW) or "").lower()
    return mode if mode in _MODES else MODE_SHADOW


def build_tool_declarations(slot_names: list[str]) -> list[dict]:
    """OpenAI tools schema. Minimal on purpose — every description line is
    prompt tokens on every turn (P0 measured ~flat TTFT with this roster).

    record_customer_details takes the AGENT'S slot names, so the runtime
    stays domain-free: a real-estate agent exposes budget/bhk, a clinic
    exposes symptom/insurer, purely from its YAML slots.
    """
    slot_props = {name: {"type": "string"} for name in slot_names[:12]}
    return [
        {"type": "function", "function": {
            "name": "record_customer_details",
            "description": "Record details the customer just stated.",
            "parameters": {"type": "object", "properties": slot_props},
        }},
        {"type": "function", "function": {
            "name": "book_appointment",
            "description": "Book the visit/appointment the customer agreed to.",
            "parameters": {"type": "object", "properties": {
                "date": {"type": "string"},
                "time": {"type": "string"}},
                "required": ["date", "time"]},
        }},
        {"type": "function", "function": {
            "name": "request_callback",
            "description": "Customer asked to be called back later.",
            "parameters": {"type": "object", "properties": {
                "when": {"type": "string"},
                "reason": {"type": "string"}},
                "required": ["when"]},
        }},
        {"type": "function", "function": {
            "name": "flag_do_not_call",
            "description": "Customer asked never to be called again.",
            "parameters": {"type": "object", "properties": {}},
        }},
        {"type": "function", "function": {
            "name": "end_call",
            "description": "The conversation is complete and should end.",
            "parameters": {"type": "object", "properties": {
                "reason": {"type": "string"}}},
        }},
    ]


@dataclass
class ToolProposal:
    name: str
    args: dict
    raw_args: str = ""
    valid: bool = True


@dataclass
class ToolCallAccumulator:
    """Reassembles streamed tool_calls deltas into proposals.

    Handles both spec-compliant streams (distinct `index` per call) and the
    Gemini shim (`index=None` on every delta): a delta carrying a function
    NAME always begins a new call; argument fragments append to the most
    recent call.
    """
    _calls: list[dict] = field(default_factory=list)

    def add_delta(self, tool_calls) -> None:
        for tc in tool_calls or []:
            fn = getattr(tc, "function", None)
            name = getattr(fn, "name", None) if fn else None
            args = getattr(fn, "arguments", None) if fn else None
            idx = getattr(tc, "index", None)
            if name:
                self._calls.append({"name": name, "args": args or "", "index": idx})
                continue
            target = None
            if idx is not None:
                for c in reversed(self._calls):
                    if c["index"] == idx:
                        target = c
                        break
            if target is None and self._calls:
                target = self._calls[-1]
            if target is not None and args:
                target["args"] += args

    @property
    def has_calls(self) -> bool:
        return bool(self._calls)

    def finish(self) -> list[ToolProposal]:
        out: list[ToolProposal] = []
        for c in self._calls:
            raw = c["args"].strip() or "{}"
            try:
                parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    raise ValueError("arguments not an object")
                out.append(ToolProposal(name=c["name"], args=parsed, raw_args=raw))
            except (ValueError, json.JSONDecodeError) as exc:
                # Visible failure — the whole point of tools over regex.
                logger.warning("[TOOLS] malformed arguments for %s: %r (%s)",
                               c["name"], raw[:200], exc)
                out.append(ToolProposal(name=c["name"], args={}, raw_args=raw,
                                        valid=False))
        self._calls.clear()
        return out


def clean_slot_args(args: dict) -> dict:
    """Same null-filtering discipline as the legacy json extractor."""
    return {k: str(v).strip() for k, v in args.items()
            if isinstance(v, (str, int, float)) and str(v).strip()
            and str(v).strip().lower() not in ("null", "none", "unknown", "n/a")}


# Deterministic spoken confirmations for tool-only turns ("on" mode). The
# P0 probe showed the shim returns tool calls INSTEAD of text — without
# these, an executed action would be followed by dead air. Kept canned on
# purpose (platform doctrine: determinism beats generation for
# confirmations; these also hit the TTS PCM cache).
CONFIRM_LINES: dict[str, dict[str, str]] = {
    "book_appointment": {
        "en": "Done — I have noted {date} at {time}.",
        "hi": "जी, {date} को {time} नोट कर लिया।",
        "gu": "સરસ, {date} ના રોજ {time} નોંધી લીધું.",
    },
    "request_callback": {
        "en": "Sure — we will call you back {when}.",
        "hi": "ठीक है, हम आपको {when} कॉल करेंगे।",
        "gu": "ચોક્કસ, અમે તમને {when} કૉલ કરીશું.",
    },
    "flag_do_not_call": {
        "en": "Understood — we will not call you again.",
        "hi": "समझ गया, हम आपको दोबारा कॉल नहीं करेंगे।",
        "gu": "સમજાઈ ગયું, અમે તમને ફરી કૉલ નહીં કરીએ.",
    },
    "record_customer_details": {
        "en": "Noted, thank you.",
        "hi": "जी, नोट कर लिया।",
        "gu": "જી, નોંધી લીધું.",
    },
}


def confirm_line(name: str, lang: str, args: dict) -> str:
    lines = CONFIRM_LINES.get(name) or {}
    template = lines.get((lang or "en")[:2]) or lines.get("en") or ""
    try:
        return template.format(**{k: str(v) for k, v in args.items()})
    except (KeyError, IndexError):
        return template.replace("{date}", "").replace("{time}", "").replace(
            "{when}", "").strip(" —-")
