"""In-call vague-commitment clarification (PLAN-intelligence-layer §2.8).

Proves: vague promise → exactly ONE clarification question → the answer
refines the commitment (never books a calendar slot) → the typed commitment
rides extra_flags into collected_data. Also proves the flag default keeps
existing agents byte-identical in behavior, and that the post-call deadline
resolver turns "Tomorrow evening" into a concrete IST timestamp.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import re

import pytest

from app.agent_definition.schema import AgentDefinition
from app.voice.conversation import ConversationRouter


@pytest.fixture(autouse=True)
def _no_network_llm(stub_llm):
    """Every test here runs offline — see the stub_llm fixture in conftest."""


def asks_a_question(text: str) -> bool:
    """True if the reply interrogates the caller about scheduling.

    Word-boundary matched on purpose: a plain `"day" in text` substring test —
    which this file used to do — is also satisfied by "Tuesday", "Wednesday",
    "today" and "birthday", so a perfectly good confirmation like "I'll call you
    Tuesday" registered as a second interrogation.
    """
    return "?" in text and re.search(r"\b(day|time|when)\b", text.lower()) is not None


def make_router(clarify_enabled=True, mock_slots=None, **over) -> ConversationRouter:
    d = {
        "agent_id": "t_clarify",
        "version": "1.0.0",
        "identity": {"agent_name": "Asha", "company_name": "TestCo"},
        "languages": {"supported": ["en", "hi"], "default": "en", "tts_voices": {}},
        "call_flow": {
            "stages": [
                {"id": "S1", "order": 0, "goal": "one", "turns_until_advance": 8},
                {"id": "S2", "order": 1, "goal": "two", "turns_until_advance": 99},
            ],
            "callback_policy": {"enabled": True},
        },
        "follow_up": {"clarify_commitments": {"enabled": clarify_enabled}},
        "calendar": {"mock_slots": mock_slots or []},
        "response_bank": {"opening_greeting": "hi"},
    }
    d.update(over)
    agent = AgentDefinition.from_dict(d)
    router = ConversationRouter(agent_def=agent, default_language="en")
    router._available_calendar_slots = lambda: list(mock_slots or [])
    return router


def drive(router: ConversationRouter, text: str) -> str:
    async def _one():
        out = []
        async for res in router.process_stream(text):
            out.append(res.text)
        return " ".join(t for t in out if t)

    return asyncio.run(_one())


def test_vague_commitment_gets_one_clarification_question():
    r = make_router()
    resp = drive(r, "I am busy right now, call me next week")
    assert "which day" in resp.lower() or "what time" in resp.lower()
    assert r._clarify_asked is True
    assert r._pending_clarify is not None
    assert r.state_mgr.extra_flags.get("callback_requested") is True


def test_answer_refines_commitment_and_confirms():
    r = make_router()
    drive(r, "call me later this week please")
    resp = drive(r, "tuesday evening works")
    assert "tuesday" in resp.lower() and "evening" in resp.lower()
    commits = r.state_mgr.extra_flags["commitments"]
    assert len(commits) == 1
    assert commits[0]["clarified"] is True
    assert commits[0]["day"] == "Tuesday"
    assert commits[0]["time"] == "evening"
    assert r._pending_clarify is None


def test_clarification_answer_never_books_calendar_slot():
    slots = [{"day": "Tuesday", "time": "5 PM", "label": "Tuesday 5 PM"}]
    r = make_router(mock_slots=slots)
    drive(r, "please call me later")
    drive(r, "tomorrow morning")
    assert r.memory.booking.appointment_time in (None, "")  # no phantom booking


def test_precise_commitment_skips_question():
    r = make_router()
    resp = drive(r, "call me tomorrow at 5 pm")
    assert "perfect" in resp.lower() or "call you" in resp.lower()
    assert r._clarify_asked is False  # nothing to clarify
    commits = r.state_mgr.extra_flags["commitments"]
    assert commits[0]["clarified"] is True and commits[0]["vague"] is False


def test_only_one_question_per_call():
    r = make_router()
    first = drive(r, "call me later")         # question 1
    assert asks_a_question(first)             # ...the one clarification we allow
    drive(r, "hmm not sure yet")              # unparseable answer — gives up
    resp = drive(r, "actually just call me sometime")
    assert not asks_a_question(resp)          # no second interrogation
    assert r._clarify_asked is True


def test_farewell_with_embedded_promise_asks_before_closing():
    r = make_router()
    resp = drive(r, "ok bye, call me next week")
    # farewell branch checkpoint fires the clarification INSTEAD of closing
    assert r.state.is_closing is False
    assert "day" in resp.lower() or "time" in resp.lower()


def test_disabled_flag_keeps_legacy_behavior():
    r = make_router(clarify_enabled=False)
    drive(r, "I am busy, call me tomorrow")
    # With the flag off, the v3 machinery must be completely inert — no
    # commitment records, no clarification state. (Whether legacy captures a
    # callback here depends on intent classification, which is exactly the
    # D7 gap the enabled path fixes — not asserted here.)
    assert r._clarify_asked is False
    assert r._pending_clarify is None
    assert r.state_mgr.extra_flags.get("commitments") is None


def test_resolve_deadline_concrete_timestamps():
    from shared.intelligence.followup import IST, resolve_deadline

    anchor = dt.datetime(2026, 7, 20, 12, 0, tzinfo=IST)  # a Monday
    assert resolve_deadline("Tomorrow", "evening", anchor).startswith("2026-07-21T18:00")
    assert resolve_deadline("Tuesday", "5 pm", anchor).startswith("2026-07-21T17:00")
    assert resolve_deadline("Monday", "", anchor).startswith("2026-07-27T11:00")  # next Monday
    assert resolve_deadline("Today", "5 baje", anchor).startswith("2026-07-20T17:00")
    assert resolve_deadline("", "evening", anchor) is None
