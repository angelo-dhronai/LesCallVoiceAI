"""Test bootstrap — make `app.*` importable, and offer the shared LLM stub.

Run with the calling-agent venv:
    cd calling-agent/backend && ../venv/bin/python -m pytest tests/
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]      # calling-agent/backend
CALLING_AGENT_DIR = BACKEND_DIR.parent                 # calling-agent
REPO_ROOT = CALLING_AGENT_DIR.parent                   # lescall

for p in (str(BACKEND_DIR), str(CALLING_AGENT_DIR), str(REPO_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

AGENTS_DIR = CALLING_AGENT_DIR / "agents"


# ---------------------------------------------------------------------------
# Shared LLM stub
#
# Any test that drives ConversationRouter.process_stream will otherwise open a
# REAL connection to the configured LLM. Whether that call carries credentials
# is decided by collection order, not by the test: pytest imports every test
# module before running anything, and test_contact_hub_api imports
# dashboard/intel_api, which calls load_dotenv() at import time. So such a test
# passes alone (no key -> 401 -> deterministic canned fallback) and fails at
# random in the full suite (key present -> live model prose, different every
# run). That is what made test_commitment_clarify block deploys intermittently.
#
# Opt a module in with:
#     @pytest.fixture(autouse=True)
#     def _stub(stub_llm): ...
#
# Deliberately NOT autouse across the whole suite: a test that wants to assert
# on the real payload should say so explicitly.
# ---------------------------------------------------------------------------

STUB_LLM_REPLY = "Understood, I have made a note of that."


class _StubStream:
    """Minimal async iterator shaped like an OpenAI streaming response."""

    def __init__(self, text: str):
        self._chunks = [text]

    def __aiter__(self):
        async def gen():
            for c in self._chunks:
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content=c))])
        return gen()


class _StubClient:
    def __init__(self, text: str = STUB_LLM_REPLY):
        async def _create(**_kw):
            return _StubStream(text)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=_create))


@pytest.fixture
def stub_llm(monkeypatch):
    """Replace the router's LLM clients with an offline, fixed-reply stub.

    The reply carries no question mark and no weekday, so a test asserting on
    what the ROUTER decided is never measuring model wording.
    """
    from app.voice.conversation import ConversationRouter

    monkeypatch.setattr(ConversationRouter, "_get_client",
                        lambda self: _StubClient())
    monkeypatch.setattr(ConversationRouter, "_get_fallback_client",
                        lambda self: None)
    return STUB_LLM_REPLY


# ---------------------------------------------------------------------------
# Ledger isolation
#
# The document-RAG pipeline reports embedding tokens into the real AI ledger
# (credit_log/<date>.jsonl) through a module-level singleton. Tests embed
# constantly with a fake provider, so without this they write thousands of
# fabricated `embed_event` rows into the file the cost report reads — which is
# exactly how a cost report becomes confidently wrong. Observed: 3,457 rows of
# `fake-embed-v1` usage in a production day's ledger.
#
# The log directory is resolved at import time, so the module attribute is
# patched rather than the environment variable.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_credit_ledger(tmp_path_factory, monkeypatch):
    try:
        from app.voice import credit_tracker
    except Exception:
        return
    monkeypatch.setattr(credit_tracker, "_CREDIT_LOG_DIR",
                        tmp_path_factory.mktemp("credit_log"), raising=False)
