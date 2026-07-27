"""Agent-initiated hangup + close-permission confirm (platform rule, all
agents, 2026-07-27).

The supreme rule: the agent never ends a call the caller hasn't agreed to
end. hangup_pending arms ONLY on caller-consented closes — the caller's own
goodbye, or a "no, that's all" reply to the agent's permission-to-close
question — plus the involuntary terminal closes (wrong number, DND/decline
threshold). A new caller utterance after the goodbye cancels it.

The bridge side (conversation_call_bridge._maybe_agent_hangup) polls this
flag after the farewell audio drains; socket_server queues an event-locked
FreeSWITCH hangup so the goodbye is never cut mid-sentence.
"""

from __future__ import annotations

import asyncio

import pytest

from app.agent_definition.schema import AgentDefinition
from app.voice.conversation import ConversationRouter


@pytest.fixture(autouse=True)
def _no_network_llm(stub_llm):
    """Offline — see the stub_llm fixture in conftest."""


def make_router(**over) -> ConversationRouter:
    d = {
        "agent_id": "t_hangup",
        "version": "1.0.0",
        "identity": {"agent_name": "Asha", "company_name": "TestCo"},
        "languages": {"supported": ["en", "hi"], "default": "en", "tts_voices": {}},
        "call_flow": {
            "stages": [
                {"id": "S1", "order": 0, "goal": "one", "turns_until_advance": 8},
                {"id": "S2", "order": 1, "goal": "two", "turns_until_advance": 99},
            ],
        },
        "response_bank": {"opening_greeting": "hi"},
    }
    d.update(over)
    agent = AgentDefinition.from_dict(d)
    return ConversationRouter(agent_def=agent, default_language="en")


def drive(router: ConversationRouter, text: str) -> str:
    async def _one():
        out = []
        async for res in router.process_stream(text):
            out.append(res.text)
        return " ".join(t for t in out if t)

    return asyncio.run(_one())


def test_caller_goodbye_arms_hangup():
    r = make_router()
    drive(r, "okay thank you, goodbye")
    assert r.state.is_closing is True
    assert r.hangup_pending is True


def test_close_permission_confirm_arms_hangup():
    """Agent asked 'anything else?' → caller 'no, that's all' → close."""
    r = make_router()
    r.last_agent_response = "Great! Is there anything else I can help you with?"
    drive(r, "no that's all")
    assert r.state.is_closing is True
    assert r.hangup_pending is True


def test_close_permission_confirm_hinglish():
    r = make_router()
    r.last_agent_response = "Theek hai! Aur kuch madad chahiye aapko?"
    drive(r, "nahi bas")
    assert r.hangup_pending is True


def test_plain_no_without_permission_question_does_not_close():
    """A bare 'no' mid-conversation is an answer, not a goodbye."""
    r = make_router()
    r.last_agent_response = "Would you like the premium plan?"
    drive(r, "no")
    assert r.state.is_closing is False
    assert r.hangup_pending is False


def test_no_but_a_question_keeps_the_call_alive():
    r = make_router()
    r.last_agent_response = "Is there anything else I can help you with?"
    drive(r, "no but what is the price")
    assert r.hangup_pending is False


def test_caller_speaking_again_cancels_hangup():
    """'Wait, one more thing' after the goodbye must be answered, not cut."""
    r = make_router()
    drive(r, "okay bye")
    assert r.hangup_pending is True
    drive(r, "wait actually one more question about the location")
    assert r.hangup_pending is False


def test_wrong_number_close_arms_hangup():
    r = make_router(call_direction="outbound",
                    outbound={"wrong_number_patterns": ["wrong number"],
                              "wrong_number_close": {"en": "So sorry! Bye."}})
    r.call_direction = "outbound"
    drive(r, "you have the wrong number")
    assert r.state.is_closing is True
    assert r.hangup_pending is True
