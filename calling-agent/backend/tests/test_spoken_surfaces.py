"""spoken_surfaces — the engine-driven "what will the caller actually hear"
resolver.

Regression source (2026-08-01, Jethalal): the copilot edited
response_bank.opening_greeting on an outbound agent and reported success,
but outbound calls open with outbound.opening_greeting — the change was
applied and inaudible. These tests pin (a) that the resolver's answer IS the
engine's answer, (b) the shadow/dormant/dead-key detection, and (c) that
LOCALIZED_KEYS_READ can never drift from what conversation.py actually
reads (the executable-manual guarantee).
"""
from __future__ import annotations

import asyncio
import copy
import re
from pathlib import Path

from app.agent_definition.schema import AgentDefinition
from app.agent_definition.spoken_surfaces import (
    LOCALIZED_KEYS_READ,
    dead_path_reason,
    reachability_findings,
    resolve_spoken_surfaces,
    touches_opening,
)

HI_OB = "नमस्ते, क्या मेरी बात {contact_name} से हो रही है?"
EN_OB = "Hello, am I speaking with {contact_name}?"

BASE = {
    "identity": {"agent_name": "Asha", "company_name": "Acme Traders"},
    "languages": {"supported": ["hi", "en"], "default": "hi"},
    "call_direction": "outbound",
    "response_bank": {"opening_greeting": "Hello?"},
    "outbound": {"opening_greeting": {"hi": HI_OB, "en": EN_OB}},
}


def _agent(**overrides) -> AgentDefinition:
    d = copy.deepcopy(BASE)
    for k, v in overrides.items():
        d[k] = v
    return AgentDefinition.from_dict(d)


def test_outbound_call_speaks_outbound_field_not_response_bank():
    r = resolve_spoken_surfaces(_agent())
    assert r["direction"] == "outbound"
    assert r["openings"]["hi"]["line"] == HI_OB
    assert r["openings"]["en"]["line"] == EN_OB
    assert r["openings"]["hi"]["source"] == "outbound.opening_greeting"
    # The exact incident: the edited field is flagged as not-spoken.
    assert [s["path"] for s in r["shadowed"]] == \
        ["response_bank.opening_greeting"]
    assert "NOT spoken" in r["shadowed"][0]["note"]


def test_outbound_without_outbound_field_falls_back():
    r = resolve_spoken_surfaces(_agent(outbound={}))
    assert r["openings"]["hi"]["line"] == "Hello?"
    assert r["openings"]["hi"]["source"] == "response_bank.opening_greeting"
    assert r["shadowed"] == []


def test_inbound_uses_response_bank_and_flags_dormant_outbound():
    r = resolve_spoken_surfaces(_agent(call_direction="inbound"))
    assert r["openings"]["hi"]["line"] == "Hello?"
    assert r["openings"]["hi"]["source"] == "response_bank.opening_greeting"
    assert [s["path"] for s in r["shadowed"]] == ["outbound.opening_greeting"]
    assert "dormant" in r["shadowed"][0]["note"]


def test_no_shadow_when_both_fields_agree():
    # Twin builders deliberately write the same opener into both homes —
    # identical values are one truth, not a trap.
    r = resolve_spoken_surfaces(
        _agent(response_bank={"opening_greeting": EN_OB}))
    assert r["shadowed"] == []


def test_resolver_line_is_the_engine_line():
    """The executable-manual anchor: the resolver never re-describes engine
    precedence — its line must be byte-identical to a real router's."""
    from app.voice.conversation import ConversationRouter

    agent = _agent()
    router = ConversationRouter(agent_def=agent, default_language="hi")
    router.call_direction = "outbound"
    router.outbound_contact_name = "Angelo"
    engine_line = asyncio.run(router.generate_opening()).text
    resolved = resolve_spoken_surfaces(agent)["openings"]["hi"]["line"]
    assert resolved.replace("{contact_name}", "Angelo") == engine_line


def test_dead_localized_opening_key_is_never_spoken_and_is_blocked():
    d = copy.deepcopy(BASE)
    d["response_bank"]["localized_responses"] = {
        "hi": {"opening_greeting": "यह पंक्ति कभी नहीं बोली जाएगी"}}
    r = resolve_spoken_surfaces(AgentDefinition.from_dict(d))
    assert all("कभी नहीं" not in o["line"] for o in r["openings"].values())
    reason = dead_path_reason(
        "response_bank.localized_responses.hi.opening_greeting")
    assert reason and "no engine code path" in reason


def test_engine_read_localized_keys_are_not_blocked():
    for key in LOCALIZED_KEYS_READ:
        assert dead_path_reason(
            f"response_bank.localized_responses.hi.{key}") is None


def test_localized_keys_read_matches_conversation_source():
    """LOCALIZED_KEYS_READ must equal the literal keys conversation.py passes
    to _localized/_localized_list — if the engine starts (or stops) reading a
    key, this fails until the resolver's set is updated."""
    import app.voice.conversation as conv

    src = Path(conv.__file__).read_text(encoding="utf-8")
    keys = set(re.findall(
        r"_localized(?:_list)?\(\s*\"([a-z_]+)\"", src))
    assert keys == set(LOCALIZED_KEYS_READ)


def test_reachability_findings_reproduce_the_incident():
    f = reachability_findings(_agent(), {"response_bank.opening_greeting"})
    assert f["blockers"] == []
    assert len(f["warnings"]) == 1
    assert "outbound.opening_greeting" in f["warnings"][0]
    assert "NOT spoken" in f["warnings"][0]


def test_reachability_inverse_direction_warning():
    f = reachability_findings(_agent(call_direction="inbound"),
                              {"outbound.opening_greeting.hi"})
    assert any("inbound calls" in w for w in f["warnings"])


def test_reachability_clean_edit_has_no_findings():
    f = reachability_findings(_agent(), {"outbound.opening_greeting.hi",
                                         "outbound.opening_greeting.en"})
    assert f == {"blockers": [], "warnings": []}


def test_dead_path_is_a_blocker():
    f = reachability_findings(
        _agent(), {"response_bank.localized_responses.hi.opening_greeting"})
    assert len(f["blockers"]) == 1


def test_touches_opening():
    assert touches_opening({"response_bank.opening_greeting"})
    assert touches_opening({"outbound.opening_greeting.hi"})
    assert touches_opening({"call_direction"})
    assert not touches_opening({"guardrails.never_rules"})
