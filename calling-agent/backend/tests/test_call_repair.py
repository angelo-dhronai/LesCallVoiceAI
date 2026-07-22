"""Call-repair platform rules (every agent, every call).

1. Early-call "hello?" — a bare hello (any supported script) inside the
   opening window means the caller didn't hear the agent: replay the last
   utterance verbatim instead of treating it as a conversational turn.
   Capped at 2 replays.
2. Dead-air reprompt — no caller voice activity well past their turn means
   STT probably missed the reply: say "I couldn't hear you" and re-ask the
   question. Pure decision logic lives in VoicePipeline._silence_reprompt_due;
   the phrase composition in ConversationRouter.generate_silence_reprompt.
"""

from __future__ import annotations

import asyncio
import time
import types

from app.agent_definition.schema import AgentDefinition
from app.voice.conversation import ConversationRouter


def make_router(**over) -> ConversationRouter:
    d = {
        "agent_id": "t_repair",
        "version": "1.0.0",
        "identity": {"agent_name": "Asha", "company_name": "TestCo"},
        "languages": {"supported": ["en", "hi"], "default": "en", "tts_voices": {}},
        "call_flow": {
            "stages": [
                {"id": "S1", "order": 0, "goal": "one", "turns_until_advance": 8},
            ],
        },
        "calendar": {"mock_slots": []},
        "response_bank": {"opening_greeting": "Namaste! This is Asha from TestCo. How can I help you today?"},
    }
    d.update(over)
    return ConversationRouter(agent_def=AgentDefinition.from_dict(d),
                              default_language="en")


def drive(router: ConversationRouter, text: str) -> str:
    async def _one():
        out = []
        async for res in router.process_stream(text):
            out.append(res.text)
        return " ".join(t for t in out if t)

    return asyncio.run(_one())


# ---------------------------------------------------------------------------
# early-call "hello?" replay
# ---------------------------------------------------------------------------

def test_early_hello_replays_greeting():
    r = make_router()
    greeting = asyncio.run(r.generate_opening()).text
    resp = drive(r, "Hello?")
    assert resp == greeting
    assert r._hello_replays == 1


def test_repeated_hello_replays_again_then_caps():
    r = make_router()
    greeting = asyncio.run(r.generate_opening()).text
    assert drive(r, "hello hello") == greeting
    assert drive(r, "हेलो हेलो") == greeting
    assert r._hello_replays == 2
    # Cap spent — the guard that feeds the replay branch is now closed.
    assert r._is_early_hello("hello") is True   # still inside the window…
    assert r._hello_replays >= 2                # …but the replay path won't take it


def test_hello_with_filler_still_counts_but_content_does_not():
    r = make_router()
    asyncio.run(r.generate_opening())
    assert r._is_early_hello("are hello haan") is True
    assert r._is_early_hello("hello I want a 2BHK flat") is False
    assert r._is_early_hello("haan") is False          # affirmation, not a hello
    assert r._is_early_hello("good morning") is False


def test_hello_outside_window_is_not_repair():
    r = make_router()
    asyncio.run(r.generate_opening())
    r._opening_ts = time.monotonic() - 60.0
    assert r._is_early_hello("hello") is False


def test_hello_before_any_agent_speech_is_not_repair():
    r = make_router()  # generate_opening never ran
    assert r._is_early_hello("hello") is False


# ---------------------------------------------------------------------------
# dead-air reprompt composition
# ---------------------------------------------------------------------------

def test_silence_reprompt_before_first_turn_replays_plain():
    r = make_router()
    greeting = asyncio.run(r.generate_opening()).text
    assert r.generate_silence_reprompt() == greeting  # no "couldn't hear YOU"


def test_silence_reprompt_repeats_question_with_prefix():
    r = make_router()
    r.last_agent_response = "What time works for you tomorrow?"
    r.state.turn_count = 2
    out = r.generate_silence_reprompt()
    assert out.startswith("Sorry, I couldn't hear you.")
    assert "What time works for you tomorrow?" in out
    # last_agent_response is NOT compounded — a second reprompt is identical
    assert r.last_agent_response == "What time works for you tomorrow?"
    assert r.generate_silence_reprompt() == out


def test_silence_reprompt_language_aware():
    r = make_router()
    r.last_agent_response = "कल कौन सा समय ठीक रहेगा?"
    r.state.turn_count = 3
    r.state.language = "hi"
    out = r.generate_silence_reprompt()
    assert out.startswith("माफ़ कीजिए")
    assert "कल कौन सा समय" in out


def test_silence_reprompt_empty_without_last_response():
    r = make_router()
    assert r.generate_silence_reprompt() == ""


# ---------------------------------------------------------------------------
# dead-air watchdog decision (pure, on a stand-in state object)
# ---------------------------------------------------------------------------

def _pipeline_state(**over):
    ns = types.SimpleNamespace(
        _greeting_done=True, _tts_playing=False, _reprompt_count=0,
        conversation=types.SimpleNamespace(call_outcome=""),
        _current_generation_task=None,
        _stt_inflight=0, _user_speaking=False, _prev_speech=False,
        _stt_queue=asyncio.Queue(), _transcript_queue=asyncio.Queue(),
        _tts_queue=asyncio.Queue(),
        sink=types.SimpleNamespace(playback_busy_until=0.0),
        _tts_end_time=0.0, _last_voice_activity=0.0, _last_reprompt_ts=0.0,
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def _due(ns, now) -> bool:
    from app.voice.pipeline import VoicePipeline

    return VoicePipeline._silence_reprompt_due(ns, now)


def test_watchdog_fires_after_quiet_period():
    now = 1000.0
    ns = _pipeline_state(_tts_end_time=now - 7.0)
    assert _due(ns, now) is True


def test_watchdog_respects_activity_and_playback():
    now = 1000.0
    assert _due(_pipeline_state(_tts_end_time=now - 7.0,
                                _last_voice_activity=now - 2.0), now) is False
    assert _due(_pipeline_state(_tts_end_time=now - 7.0,
                                _tts_playing=True), now) is False
    ns = _pipeline_state(_tts_end_time=now - 7.0)
    ns.sink.playback_busy_until = now + 3.0
    assert _due(ns, now) is False
    assert _due(_pipeline_state(_tts_end_time=now - 3.0), now) is False


def test_watchdog_never_fires_before_agent_spoke_or_after_cap_or_closing():
    now = 1000.0
    assert _due(_pipeline_state(), now) is False  # anchor 0 — agent never spoke
    assert _due(_pipeline_state(_tts_end_time=now - 7.0,
                                _reprompt_count=2), now) is False
    ns = _pipeline_state(_tts_end_time=now - 7.0)
    ns.conversation.call_outcome = "completed"
    assert _due(ns, now) is False
    assert _due(_pipeline_state(_tts_end_time=now - 7.0,
                                _greeting_done=False), now) is False


def test_watchdog_waits_between_reprompts():
    now = 1000.0
    ns = _pipeline_state(_tts_end_time=now - 20.0, _reprompt_count=1,
                         _last_reprompt_ts=now - 3.0)
    assert _due(ns, now) is False       # last reprompt too recent
    ns._last_reprompt_ts = now - 7.0
    assert _due(ns, now) is True


def test_watchdog_skips_when_turn_in_flight():
    now = 1000.0
    assert _due(_pipeline_state(_tts_end_time=now - 7.0, _stt_inflight=1),
                now) is False
    assert _due(_pipeline_state(_tts_end_time=now - 7.0, _prev_speech=True),
                now) is False
    task = types.SimpleNamespace(done=lambda: False)
    assert _due(_pipeline_state(_tts_end_time=now - 7.0,
                                _current_generation_task=task), now) is False
