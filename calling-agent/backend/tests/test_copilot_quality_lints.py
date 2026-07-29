"""Cross-surface contradiction lints + surgical-tool quality surfacing.

Regression source (2026-07-28): a live call spoke a banned "twenty-minute
demo … Tuesday at 10 AM" AFTER four update_stage edits — call_flow.call_goal
(rendered every turn as CALL MISSION) and the exact_n scheduling default kept
ordering the old behaviour, and update_stage returned no quality warnings so
the copilot claimed success. These tests pin the three new lints, prove they
never nag the production pramukh agents, and prove the surgical tools now
surface quality + the mission string.
"""
from __future__ import annotations

import asyncio
import dataclasses
import shutil
import sys

import pytest
import yaml

from conftest import AGENTS_DIR, REPO_ROOT

from app.agent_definition.schema import AgentDefinition


@pytest.fixture()
def cop(tmp_path, monkeypatch):
    monkeypatch.setenv("LESCALL_DB_URL", f"sqlite:///{tmp_path}/test.db")
    dash = str(REPO_ROOT / "dashboard")
    if dash not in sys.path:
        sys.path.insert(0, dash)
    import agent_copilot_api as cop_mod
    return cop_mod


@pytest.fixture()
def cop_with_agents(tmp_path, monkeypatch, cop):
    import agent_builder_api as builder
    agents_dir = tmp_path / "agents"
    (agents_dir / "pramukh").mkdir(parents=True)
    shutil.copy(AGENTS_DIR / "pramukh" / "agent.yaml",
                agents_dir / "pramukh" / "agent.yaml")
    monkeypatch.setattr(builder, "_AGENTS_DIR", agents_dir)
    monkeypatch.setattr(cop, "_AGENTS_DIR", agents_dir)
    builder._claim_slug("pramukh", builder._ctx_owner(), "Pramukh")
    from app.agent_definition.registry import agent_registry
    agent_registry.invalidate_all()
    return cop, agents_dir


def _run(coro):
    return asyncio.run(coro)


# Stable marker substrings, one per lint.
MARK_CALLER_PICKS = "let the CALLER propose the time"
MARK_FIRST_BEAT = "only the FIRST beat"
MARK_NO_TIMES = "no times to offer"
ALL_MARKS = (MARK_CALLER_PICKS, MARK_FIRST_BEAT, MARK_NO_TIMES)


def maya_shape(**over) -> dict:
    """The exact definition shape that produced the live incident."""
    d = {
        "call_direction": "outbound",
        "languages": {"supported": ["en"], "default": "en"},
        "response_bank": {"opening_greeting": "Hello"},
        "calendar": {"type": "mock", "mock_slots": []},
        "call_flow": {
            "call_goal": "Qualify businesses and schedule a twenty-minute "
                         "online demonstration meeting with a specialist.",
            "scheduling": {},
            "stages": [
                {"id": "GREET", "order": 0,
                 "goal": "Once the caller responds, confirm their identity. "
                         "Once confirmed, introduce yourself from the company. "
                         "Politely ask if this is a good time to talk.",
                 "advance_when": {"intent": "affirmation", "max_turns": 3,
                                  "min_turns": 1}},
                {"id": "BOOK", "order": 1,
                 "goal": "Do NOT suggest any specific times or dates. "
                         "Instead, ask them when they would be free for a "
                         "follow-up later.",
                 "advance_when": {"max_turns": 5}},
            ],
        },
    }
    d.update(over)
    return d


def _warned(cop, merged, mark) -> bool:
    return any(mark in w for w in cop._quality_warnings(
        merged, include_wiring_lints=False))


# ── lint (i): caller-picks goals vs exact_n scheduling ──────────────────────

def test_lint_caller_picks_vs_exact_n(cop):
    assert _warned(cop, maya_shape(), MARK_CALLER_PICKS)
    fixed = maya_shape()
    fixed["call_flow"]["scheduling"] = {"style": "flexible"}
    assert not _warned(cop, fixed, MARK_CALLER_PICKS)


# ── lint (ii): unreachable beats on intent-advanced stages ──────────────────

def test_lint_unreachable_beats(cop):
    assert _warned(cop, maya_shape(), MARK_FIRST_BEAT)
    fixed = maya_shape()
    fixed["call_flow"]["stages"][0]["advance_when"]["min_turns"] = 2
    assert not _warned(cop, fixed, MARK_FIRST_BEAT)
    single_beat = maya_shape()
    single_beat["call_flow"]["stages"][0]["goal"] = \
        "Confirm you are speaking with the named contact."
    assert not _warned(cop, single_beat, MARK_FIRST_BEAT)


# ── lint (iii): booking language + empty calendar + exact_n ─────────────────

def test_lint_invented_times_empty_calendar(cop):
    assert _warned(cop, maya_shape(), MARK_NO_TIMES)
    with_slots = maya_shape(
        calendar={"type": "mock",
                  "mock_slots": [{"day": "Tomorrow", "time": "11:00 AM"}]})
    assert not _warned(cop, with_slots, MARK_NO_TIMES)
    flexible = maya_shape()
    flexible["call_flow"]["scheduling"] = {"style": "flexible"}
    assert not _warned(cop, flexible, MARK_NO_TIMES)


# ── production-nag guard: pramukh agents trigger NONE of the new lints ──────

@pytest.mark.parametrize("slug", ["pramukh", "pramukh_agent"])
@pytest.mark.parametrize("wiring", [True, False])
def test_production_agents_trigger_no_new_lints(cop, slug, wiring):
    raw = yaml.safe_load(
        (AGENTS_DIR / slug / "agent.yaml").read_text(encoding="utf-8"))
    merged = dataclasses.asdict(AgentDefinition.from_dict(raw))
    warnings = cop._quality_warnings(merged, include_wiring_lints=wiring)
    for mark in ALL_MARKS:
        assert not any(mark in w for w in warnings), (slug, mark, warnings)


# ── surgical tools now surface quality + the mission ────────────────────────

def test_update_stage_result_has_quality_and_call_goal(cop_with_agents):
    cop, agents_dir = cop_with_agents
    guard = {"user_turns": 1, "created_this_turn": False,
             "session": {"messages": []}, "user_message": "edit stage"}
    stage_id = yaml.safe_load(
        (agents_dir / "pramukh" / "agent.yaml").read_text(
            encoding="utf-8"))["call_flow"]["stages"][0]["id"]
    res, _s, ok, mutated = _run(cop._execute_tool("update_stage", {
        "name": "pramukh", "stage_id": stage_id,
        "fields": {"goal": "Greet the caller warmly, confirm how you can "
                           "help them today, and steer toward their need."},
    }, guard))
    assert ok and mutated
    assert isinstance(res.get("quality"), list)
    expected_goal = yaml.safe_load(
        (AGENTS_DIR / "pramukh" / "agent.yaml").read_text(
            encoding="utf-8"))["call_flow"].get("call_goal") or ""
    assert res.get("call_goal") == expected_goal[:300]
    assert "CALL MISSION" in res.get("call_goal_note", "")


def test_update_slot_result_has_quality(cop_with_agents):
    cop, _agents_dir = cop_with_agents
    guard = {"user_turns": 1, "created_this_turn": False,
             "session": {"messages": []}, "user_message": "edit slot"}
    res, _s, ok, mutated = _run(cop._execute_tool("update_slot", {
        "name": "pramukh", "slot_name": "name",
        "fields": {"max_asks": 2},
    }, guard))
    assert ok and mutated
    assert isinstance(res.get("quality"), list)


# ── the system prompt teaches the injection model ───────────────────────────

def test_system_prompt_teaches_injection_model(cop):
    p = cop._system_prompt()
    for pin in ("CALL MISSION", "never_rules", '"style": "flexible"',
                "find_text", "BANNED-PHRASE SWEEP",
                "TOP of the NEXT stage's goal"):
        assert pin in p, pin


def test_system_prompt_pace_report_then_ask(cop):
    """Speed complaints: report current pace + the 0.5–2.0 scale FIRST, then
    ask the user's preference — never silently adjust."""
    p = cop._system_prompt()
    assert "SPEED COMPLAINTS" in p
    assert "0.5–2.0 scale" in p
    assert "spoken_surfaces.pace" in p
    assert "ask what they'd prefer" in p


def test_get_agent_digest_has_pace(cop_with_agents):
    cop, agents_dir = cop_with_agents
    guard = {"user_turns": 1, "created_this_turn": False,
             "session": {"messages": []}, "user_message": "look"}
    res, _s, ok, mutated = _run(cop._execute_tool(
        "get_agent", {"name": "pramukh"}, guard))
    assert ok and mutated is False
    digest = res.get("spoken_surfaces") or {}
    pace = digest.get("pace") or {}
    # The digest reports the EFFECTIVE pace — the canonical definition fills
    # the 1.0 default when the raw YAML omits the key.
    raw = yaml.safe_load((agents_dir / "pramukh" / "agent.yaml").read_text(
        encoding="utf-8"))
    canonical = dataclasses.asdict(AgentDefinition.from_dict(raw))
    expected = canonical["languages"]["tts_voices"]
    assert set(pace) == set(expected) and pace
    for lg, v in expected.items():
        assert pace[lg] == v.get("pace"), lg
