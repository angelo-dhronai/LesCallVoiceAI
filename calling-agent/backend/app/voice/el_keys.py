"""
ElevenLabs key + concurrency access, mirroring key_rotator.py's contract.

ElevenLabs is one workspace key with WORKSPACE-level quota and concurrency
(per tier: Free 2 / Starter 3 / Creator 5 / Pro 10 / Scale+Business 15) —
pooling extra keys buys nothing, so there is no pool here. What Sarvam never
had and ElevenLabs does have is a real quota endpoint
(GET /v1/user/subscription), which the watchdog polls instead of inferring
credit state from 402s.

current_key() re-reads .env on every call — a key swap performed by any
actor (operator edit, watchdog) takes effect on the next request with no
process restart, exactly like Sarvam rotation.

The semaphore self-queues requests below the tier's concurrency cap so calls
degrade to a ~50ms queue wait instead of eating 429
too_many_concurrent_requests mid-utterance. Sized by EL_MAX_CONCURRENT
(default 3 = Starter tier); RAISE THIS when the workspace tier changes.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"

EL_API_BASE = os.environ.get("ELEVENLABS_API_BASE", "https://api.elevenlabs.io")

# Starter tier = 3 concurrent requests. One live call holds at most 1 TTS
# stream + occasionally 1-2 STT requests (hedge), so 3 covers roughly one
# active call with headroom — canary-sized on purpose. Bump alongside the
# subscription tier (Pro=10, Scale/Business=15).
EL_MAX_CONCURRENT = int(os.environ.get("EL_MAX_CONCURRENT", "3"))

_semaphore: asyncio.Semaphore | None = None
_semaphore_loop: asyncio.AbstractEventLoop | None = None


def current_key() -> str:
    """Live ElevenLabs API key, re-read from .env each call (see module doc)."""
    try:
        if _ENV_FILE.exists():
            m = re.search(
                r"^ELEVENLABS_API_KEY=(.*)$",
                _ENV_FILE.read_text(encoding="utf-8"),
                re.MULTILINE,
            )
            if m:
                key = m.group(1).strip()
                if key:
                    os.environ["ELEVENLABS_API_KEY"] = key
                    return key
    except Exception:  # pragma: no cover - best effort, never break a call
        pass
    return os.environ.get("ELEVENLABS_API_KEY", "")


def concurrency_gate() -> asyncio.Semaphore:
    """Process-wide semaphore capping in-flight ElevenLabs requests.

    Rebuilt if the running loop changed (CLI restarts, tests) — a semaphore
    bound to a dead loop deadlocks forever.
    """
    global _semaphore, _semaphore_loop
    loop = asyncio.get_running_loop()
    if _semaphore is None or _semaphore_loop is not loop:
        _semaphore = asyncio.Semaphore(max(EL_MAX_CONCURRENT, 1))
        _semaphore_loop = loop
    return _semaphore
