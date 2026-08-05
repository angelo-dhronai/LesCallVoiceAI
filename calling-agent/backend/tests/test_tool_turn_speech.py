"""A tool turn must never leave the caller in silence (2026-08-04).

The prompt trailer tells the model "ALWAYS also reply with your normal 1-2
spoken sentences alongside any tool call". When the model ignores that, the
only thing the caller hears is a canned CONFIRM_LINE — and when the tool
filled nothing (the model named a property the agent does not have), there
is no canned line either, so the caller hears nothing at all.

Measured on this fleet's own transcripts: 0 canned lines in 1,206 agent
turns over 07-20..07-30, then 7.9% (08-01) / 10.3% (08-03) / 13.6% (08-04)
of agent turns; and 0 in 517 turns → 16.8% within maya_lescall_sdr alone,
an agent whose slot roster and tool wiring never changed across the step.

This suite pins the three layers of the repair:
  1. re-ask the model with the tools withdrawn — the caller gets real words
  2. the canned confirmation, if the re-ask does not land
  3. a spoken acknowledgement, if there is no canned line either
plus the junk-argument gate that produced "Done — I have noted today at now."
"""

from __future__ import annotations

import asyncio
import types

import pytest

from app.voice import conversation as conv
from app.voice.conversation import ConversationRouter
from app.voice.llm_tools import ToolProposal
from test_call_termination import drive, make_router


@pytest.fixture(autouse=True)
def _offline(stub_llm):
    """Offline — see the stub_llm fixture in conftest."""


def tool_router(**over) -> ConversationRouter:
    # A real slot roster, so record_customer_details actually fills something
    # and the canned CONFIRM_LINE path is exercised rather than skipped.
    over.setdefault("slots", [{"name": "name", "priority": 1},
                              {"name": "budget", "priority": 2}])
    r = make_router(**over)
    r._tools_mode = "on"
    r.state.turn_count = 3          # past the phantom-booking guard
    return r


def _arm(router, name: str, args: dict) -> None:
    """Make the next turn behave as if the model streamed this tool call
    and no text — the exact shape the Gemini compat shim produces."""
    real = ConversationRouter._call_llm_stream

    async def _texless(self, text, force_text=False):
        if force_text:
            async for chunk in real(self, text, force_text=True):
                yield chunk
            return
        self._pending_tool_proposals = [ToolProposal(name=name, args=args)]
        return
        yield  # pragma: no cover — makes this an async generator

    router._call_llm_stream = types.MethodType(_texless, router)


# ── layer 1: the re-ask ─────────────────────────────────────────────────────

def test_textless_tool_turn_reasks_for_words(stub_llm):
    r = tool_router()
    _arm(r, "record_customer_details", {"name": "Ramesh"})
    resp = drive(r, "my name is Ramesh")
    assert stub_llm in resp
    assert "Noted, thank you." not in resp


def test_reask_is_not_used_when_the_model_already_spoke():
    """A turn that carried text pays no extra round trip."""
    r = tool_router()
    calls: list[bool] = []
    real = ConversationRouter._call_llm_stream

    async def _with_text(self, text, force_text=False):
        calls.append(force_text)
        if force_text:
            async for c in real(self, text, force_text=True):
                yield c
            return
        self._pending_tool_proposals = [
            ToolProposal(name="record_customer_details", args={"name": "R"})]
        yield "Lovely, Ramesh."

    r._call_llm_stream = types.MethodType(_with_text, r)
    resp = drive(r, "my name is Ramesh")
    assert "Lovely, Ramesh." in resp
    assert calls == [False]          # no re-ask


def test_reask_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(conv, "_TOOL_TEXT_REISSUE", False)
    r = tool_router()
    # budget, not name: the deterministic extractors would have filled `name`
    # from the utterance already, and a fill-only merge then changes nothing.
    _arm(r, "record_customer_details", {"budget": "50 lakh"})
    resp = drive(r, "yes that works for me")
    assert "Noted, thank you." in resp     # today's behaviour, unchanged


def test_reask_timeout_falls_back_to_the_canned_line(monkeypatch):
    monkeypatch.setattr(conv, "_TOOL_TEXT_REISSUE_TIMEOUT_S", 0.01)
    r = tool_router()
    _arm(r, "record_customer_details", {"budget": "50 lakh"})

    real = r._call_llm_stream

    async def _slow(text, force_text=False):
        if force_text:
            await asyncio.sleep(5)
            yield "too late"
            return
        async for c in real(text, force_text=force_text):
            yield c

    r._call_llm_stream = _slow
    resp = drive(r, "yes that works for me")
    assert "Noted, thank you." in resp


def test_reask_never_rearms_the_hangup():
    r = tool_router()
    _arm(r, "record_customer_details", {"name": "Ramesh"})
    r._llm_end_call_seen = False
    drive(r, "my name is Ramesh")
    assert r.hangup_pending is False


# ── layer 3: zero-fill must not be dead air ─────────────────────────────────

def test_zero_fill_tool_still_speaks(monkeypatch):
    """The 08-03 signature: record_customer_details naming a property the
    agent does not have → 0 slots filled → no canned line → dead air."""
    monkeypatch.setattr(conv, "_TOOL_TEXT_REISSUE", False)
    r = tool_router()
    _arm(r, "record_customer_details", {"current_insurance_status": "none"})
    resp = drive(r, "I don't have insurance right now")
    assert resp.strip()               # the whole point: not silence


def test_zero_fill_prefers_the_agents_own_wording():
    r = tool_router(response_bank={
        "opening_greeting": "hi",
        "tool_ack_fallbacks": {"en": "Understood — anything else?"}})
    r._tools_mode = "on"
    r.state.turn_count = 3
    _arm(r, "record_customer_details", {"nope": "x"})

    async def _empty(text, force_text=False):
        if force_text:
            return
            yield  # pragma: no cover
        r._pending_tool_proposals = [
            ToolProposal(name="record_customer_details", args={"nope": "x"})]
        return
        yield  # pragma: no cover

    r._call_llm_stream = _empty
    assert "Understood — anything else?" in drive(r, "nothing useful")


# ── junk temporal arguments ─────────────────────────────────────────────────

@pytest.mark.parametrize("date,time_,ok", [
    ("today", "now", False),
    ("tomorrow", "asap", False),
    ("2026-08-06", "", False),
    ("tomorrow", "3pm", True),
    ("Monday", "15:30", True),
    ("tomorrow", "4 o'clock", True),
    ("कल", "शाम 5 बजे", True),
])
def test_bookable_when(date, time_, ok):
    assert conv._is_bookable_when(date, time_) is ok


def test_junk_booking_is_not_confirmed():
    """Live 08-03: [TOOLS] book_appointment executed today at now →
    the caller heard "Done — I have noted today at now."."""
    r = tool_router()
    _arm(r, "book_appointment", {"date": "today", "time": "now"})
    resp = drive(r, "sure, sounds good")
    assert "today at now" not in resp
    assert r.memory.call_state.appointment_time in (None, "")


def test_real_booking_still_confirms(monkeypatch):
    monkeypatch.setattr(conv, "_TOOL_TEXT_REISSUE", False)
    r = tool_router()
    _arm(r, "book_appointment", {"date": "tomorrow", "time": "4pm"})
    resp = drive(r, "tomorrow at four works")
    assert "tomorrow" in resp and "4pm" in resp
    assert r.memory.call_state.appointment_time == "4pm"


def test_real_booking_is_recorded_even_when_the_reask_speaks(stub_llm):
    """The action commits on the first pass; the re-ask only buys words."""
    r = tool_router()
    _arm(r, "book_appointment", {"date": "tomorrow", "time": "4pm"})
    resp = drive(r, "tomorrow at four works")
    assert stub_llm in resp
    assert r.memory.call_state.appointment_time == "4pm"


# ── dead-air repair no longer replays an acknowledgement ────────────────────

def test_reprompt_does_not_replay_an_acknowledgement():
    """Live 08-03: "Sorry, I couldn't hear you. Noted, thank you."."""
    r = make_router()
    r.state.turn_count = 3
    r.memory.add_user("I want health insurance")
    r.memory.add_assistant("And what cover amount are you looking for?")
    r.memory.add_user("hmm")
    r._store_last_response("Noted, thank you.")
    out = r.generate_silence_reprompt()
    assert "Noted, thank you." not in out
    assert "cover amount" in out          # the real question came back


def test_reprompt_still_replays_a_question():
    r = make_router()
    r.state.turn_count = 3
    r._store_last_response("What is your budget?")
    assert "What is your budget?" in r.generate_silence_reprompt()


def test_reprompt_with_nothing_to_re_ask_asks_plainly():
    r = make_router()
    r.state.turn_count = 3
    r._store_last_response("Noted, thank you.")
    out = r.generate_silence_reprompt()
    assert out and "Noted, thank you." not in out
