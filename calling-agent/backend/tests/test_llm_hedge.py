"""Hedged model request (call 1230): a silent primary is raced by the
fallback; a fast primary is untouched; two losses put the fallback first."""
from __future__ import annotations

import asyncio
import types

import pytest

from app.voice import conversation as conv


class _Stream:
    def __init__(self, name): self.name, self.closed = name, False
    async def close(self): self.closed = True


def _router(primary_delay, fb_delay=0.01, primary_exc=None, fb_exc=None):
    r = types.SimpleNamespace()
    r._model, r._fallback_model, r._llm_extra = "p", "f", {}
    r._def = types.SimpleNamespace(
        llm_config=types.SimpleNamespace(max_tokens=10))
    calls = {"p": 0, "f": 0}

    async def _primary(*a, **k):
        calls["p"] += 1
        await asyncio.sleep(primary_delay)
        if primary_exc: raise primary_exc
        return _Stream("p")

    async def _fb_create(**k):
        calls["f"] += 1
        await asyncio.sleep(fb_delay)
        if fb_exc: raise fb_exc
        return _Stream("f")

    fb = types.SimpleNamespace(chat=types.SimpleNamespace(
        completions=types.SimpleNamespace(create=_fb_create)))
    r._primary_stream_create = _primary
    r._get_fallback_client = lambda: fb
    r.calls = calls
    return r


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(conv, "_VOICE_LLM_HEDGE_S", 0.05)
    monkeypatch.setattr(conv, "_PRIMARY_SLOW", {"losses": 0, "until": 0.0})


def _run(r):
    return asyncio.run(conv.ConversationRouter._hedged_stream_create(
        r, None, [], {}, {}, None))


def test_a_fast_primary_answers_and_the_fallback_is_never_called():
    r = _router(primary_delay=0.0)
    stream, used_fb = _run(r)
    assert stream.name == "p" and not used_fb and r.calls["f"] == 0


def test_a_silent_primary_is_beaten_by_the_fallback():
    r = _router(primary_delay=1.0)
    stream, used_fb = _run(r)
    assert stream.name == "f" and used_fb


def test_a_fast_primary_failure_raises_for_the_sequential_path():
    r = _router(primary_delay=0.0, primary_exc=ValueError("429"))
    with pytest.raises(ValueError):
        _run(r)


def test_two_losses_put_the_fallback_first_and_a_broken_fallback_still_answers():
    r = _router(primary_delay=0.2)
    _run(r); _run(r)
    assert conv._PRIMARY_SLOW["until"] > 0
    r2 = _router(primary_delay=0.0)
    stream, used_fb = _run(r2)
    assert stream.name == "f" and used_fb and r2.calls["p"] == 0
    r3 = _router(primary_delay=0.0, fb_exc=ValueError("down"))
    stream, used_fb = _run(r3)
    assert stream.name == "p" and not used_fb


def test_hedging_off_is_the_plain_primary(monkeypatch):
    monkeypatch.setattr(conv, "_VOICE_LLM_HEDGE_S", 0.0)
    r = _router(primary_delay=0.0)
    stream, used_fb = _run(r)
    assert stream.name == "p" and r.calls["f"] == 0


# ── Re-warm after a quiet spell (RUNBOOK §85.14) ───────────────────────────
def _warm_router(primary_delay, primary_exc=None):
    r = types.SimpleNamespace()
    r._model, r._fallback_model, r._llm_extra = "p", "f", {}
    calls = []

    def _client(name, delay, exc=None):
        async def create(**k):
            calls.append(name)
            await asyncio.sleep(delay)
            if exc: raise exc
        return types.SimpleNamespace(chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=create)))
    p, f = _client("p", primary_delay, primary_exc), _client("f", 0.0)
    r._get_client = lambda: p
    r._get_fallback_client = lambda: f
    r.calls = calls
    return r


def test_a_fast_rewarm_leaves_the_primary_first():
    r = _warm_router(0.0)
    asyncio.run(conv.ConversationRouter._rewarm_llm(r))
    assert conv._PRIMARY_SLOW["until"] == 0.0 and r.calls == ["p", "f"]


def test_a_slow_rewarm_puts_the_fallback_first_before_the_first_turn():
    r = _warm_router(0.2)          # hedge delay is 0.05 in these tests
    asyncio.run(conv.ConversationRouter._rewarm_llm(r))
    assert conv._PRIMARY_SLOW["until"] > 0.0


def test_a_failed_rewarm_puts_the_fallback_first():
    r = _warm_router(0.0, primary_exc=ValueError("429"))
    asyncio.run(conv.ConversationRouter._rewarm_llm(r))
    assert conv._PRIMARY_SLOW["until"] > 0.0
