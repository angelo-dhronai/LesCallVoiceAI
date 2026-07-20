"""
Copilot LLM request-budget tests (Groq TPM 413 incident, 2026-07-15).

A 42-message interview session produced an 18,775-token request: over the
fallback model's 12k TPM ceiling, and 2 tool-loop iterations busted even the
primary's 30k. _fit_messages must shrink any history to a per-model budget
while never dropping the system prompt or the current turn.
"""
from __future__ import annotations

import sys

import pytest

from conftest import REPO_ROOT


@pytest.fixture()
def cop():
    dash = str(REPO_ROOT / "dashboard")
    if dash not in sys.path:
        sys.path.insert(0, dash)
    import agent_copilot_api as cop
    return cop


def _fake_history(turns: int, tool_chars: int = 3000) -> list[dict]:
    msgs: list[dict] = []
    for i in range(turns):
        msgs.append({"role": "user", "content": f"answer {i}: " + "detail " * 60})
        msgs.append({"role": "assistant", "content": None,
                     "tool_calls": [{"id": f"c{i}", "type": "function",
                                     "function": {"name": "update_draft", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "x" * tool_chars})
        msgs.append({"role": "assistant", "content": "Noted. Next question: " + "words " * 40})
    return msgs


def test_under_budget_untouched(cop):
    msgs = [{"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"}]
    assert cop._fit_messages(msgs, 50_000) == msgs


def test_over_budget_fits_and_preserves_invariants(cop):
    system = {"role": "system", "content": "S" * 16_000}   # realistic prompt size
    history = _fake_history(12)                            # ~ the failing session, bigger
    current = {"role": "user", "content": "my newest answer"}
    msgs = [system] + history + [current]
    assert cop._approx_tokens(msgs) > 9_500                # genuinely over

    fitted = cop._fit_messages(msgs, 9_500)
    assert cop._approx_tokens(fitted) <= 9_500
    assert fitted[0] is system or fitted[0]["role"] == "system"
    assert fitted[-1]["content"] == "my newest answer"     # current turn survives
    # no orphaned tool results / tool-calling assistants at the front
    assert fitted[1]["role"] not in ("tool",)
    assert not (fitted[1]["role"] == "assistant" and fitted[1].get("tool_calls"))


def test_current_turn_never_dropped_even_if_over(cop):
    # A single huge current turn can't fit 2k tokens — fitting must keep it
    # (the API call may still fail upstream, but we never send a turn-less chat).
    msgs = [{"role": "system", "content": "S" * 4_000},
            {"role": "user", "content": "U" * 40_000}]
    fitted = cop._fit_messages(msgs, 2_000)
    assert [m["role"] for m in fitted] == ["system", "user"]


def test_tool_use_failed_walks_the_chain(cop, monkeypatch):
    """400 tool_use_failed on the primary (flaky small model emitting broken
    JSON) must fall through to the fallback model, not 502 the user."""
    import asyncio
    import httpx
    from openai import BadRequestError

    def _bad_request():
        req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
        resp = httpx.Response(400, request=req)
        return BadRequestError(
            "Error code: 400 - {'code': 'tool_use_failed', 'failed_generation': '...'}",
            response=resp, body={"error": {"code": "tool_use_failed"}})

    calls: list[str] = []

    class _Completions:
        async def create(self, **kw):
            calls.append(kw["model"])
            if kw["model"] == "flaky-primary":
                raise _bad_request()
            class _Msg:  # minimal choices[0].message shape
                content = "ok"
                tool_calls = None
            class _Choice:
                message = _Msg()
            class _Completion:
                choices = [_Choice()]
            return _Completion()

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    monkeypatch.setenv("LLM_FALLBACK_MODEL", "good-fallback")
    # COPILOT_-prefixed overrides win in _env(); clear any from the real .env
    monkeypatch.delenv("COPILOT_LLM_FALLBACK_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    result = asyncio.run(cop._chat_completion(_Client(), "flaky-primary", msgs))
    assert result.content == "ok"
    # 3 nudged tries on the primary, then the fallback succeeds
    assert calls == ["flaky-primary"] * 3 + ["good-fallback"]


def test_budget_math_fits_both_groq_ceilings(cop):
    # primary: half of 30k TPM minus output+margin; fallback: 12k TPM ceiling
    primary_budget = max(8000, 30_000 // 2 - 3_000 - 500)
    fb_budget = max(7000, 12_000 - 2_000 - 500)
    assert primary_budget + 3_000 <= 15_000    # two iterations fit one minute
    assert fb_budget + 2_000 <= 12_000         # a fitted request fits the 413 ceiling


def test_gemini_thought_signature_round_trip(cop):
    """Gemini 3 (OpenAI-compat) 400s when assistant functionCall history
    lacks extra_content.google.thought_signature; other providers may reject
    the unknown field. The adapter must add/strip per provider without
    mutating the stored session history."""
    class _GeminiCli:
        base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
    class _GroqCli:
        base_url = "https://api.groq.com/openai/v1"

    real_sig = {"google": {"thought_signature": "REAL_SIG_FROM_MODEL"}}
    history = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "update_draft", "arguments": "{}"}}]},   # legacy, no sig
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c2", "type": "function",
             "function": {"name": "update_draft", "arguments": "{}"},
             "extra_content": dict(real_sig)}]},                            # preserved sig
        {"role": "tool", "tool_call_id": "c2", "content": "ok"},
    ]
    import copy as _copy
    snapshot = _copy.deepcopy(history)

    gem = cop._adapt_tool_calls_for_provider(history, _GeminiCli())
    sigs = [tc["extra_content"]["google"]["thought_signature"]
            for m in gem for tc in (m.get("tool_calls") or [])]
    assert sigs == ["context_engineering_is_the_way_to_go", "REAL_SIG_FROM_MODEL"]

    groq = cop._adapt_tool_calls_for_provider(history, _GroqCli())
    assert all("extra_content" not in tc
               for m in groq for tc in (m.get("tool_calls") or []))

    assert history == snapshot  # session store never mutated
