"""
VoiceAgent — the entire conversation brain.

External interface (used by pipeline.py):
    VoiceAgent.generate_opening()      → EngineResult (coroutine)
    VoiceAgent.process_stream(text)    → AsyncIterator[EngineResult]
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass

from openai import AsyncOpenAI

from .memory import ConversationMemory
from .prompts import build_system_prompt
from .extractor import extract_lead_update

logger = logging.getLogger(__name__)

# ─── Config ──────────────────────────────────────────────────────────────────

MODEL       = os.environ.get("AGENT_MODEL", "gpt-4o")
MAX_TOKENS  = int(os.environ.get("AGENT_MAX_TOKENS", "300"))
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0.75"))

_CALL_START_TRIGGER = "[CALL_START] The phone just connected. Say your opening line."

_FALLBACK = {
    "hi": "एक second — कुछ technical issue आया। थोड़ा repeat कर सकते हैं?",
    "gu": "એક second — technical issue આવ્યો. થોડું repeat કરી શકો?",
    "en": "One moment — could you say that again please?",
}

# ─── Helpers ─────────────────────────────────────────────────────────────────

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?।])\s+")
_MARKDOWN           = re.compile(r"[*#`~_\[\]()>|]")
_EMOJI              = re.compile(
    r"[\U0001f600-\U0001f64f\U0001f300-\U0001f5ff\U0001f680-\U0001f6ff"
    r"\U0001f1e0-\U0001f1ff\U00002702-\U000027b0\U0001fa00-\U0001fa6f]+",
    re.UNICODE,
)


def _clean(text: str) -> str:
    text = _MARKDOWN.sub("", text)
    text = _EMOJI.sub("", text)
    return " ".join(text.split())


def _script_lang(text: str) -> str:
    for ch in text:
        cp = ord(ch)
        if 0x0A80 <= cp <= 0x0AFF:
            return "gu"
        if 0x0900 <= cp <= 0x097F:
            return "hi"
    return "en"


def _fallback_for(user_text: str, history_text: str = "") -> str:
    lang = _script_lang(history_text) if history_text else _script_lang(user_text)
    return _FALLBACK.get(lang, _FALLBACK["en"])


def _split_sentences(buf: str) -> tuple[list[str], str]:
    parts = _SENTENCE_BOUNDARY.split(buf)
    if len(parts) <= 1:
        return [], buf
    return [s for s in parts[:-1] if s.strip()], parts[-1]


# ─── Result dataclass ────────────────────────────────────────────────────────

@dataclass
class EngineResult:
    text:        str
    is_shortcut: bool = False
    intent:      str  = "general"


# ─── Main agent class ─────────────────────────────────────────────────────────

class VoiceAgent:
    def __init__(self) -> None:
        self.memory  = ConversationMemory()
        self._client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        self._turn   = 0
        self._greeted = False
        logger.info("[AGENT] model=%s", MODEL)

    # ── Public API ────────────────────────────────────────────────────────────

    async def generate_opening(self) -> EngineResult:
        if self._greeted:
            logger.warning("[AGENT] generate_opening called twice — suppressed")
            return EngineResult(text="", is_shortcut=True)
        self._greeted = True

        system = build_system_prompt(self.memory)
        full_response = ""
        try:
            resp = await self._client.chat.completions.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": _CALL_START_TRIGGER},
                ],
            )
            full_response = _clean(resp.choices[0].message.content or "")
        except Exception as exc:
            logger.error("[AGENT] Opening generation failed: %s", exc)
            full_response = "Hello! Pramukh Group, Vishal here. How are you doing?"

        self.memory.add_assistant(full_response)
        logger.info("[AGENT] Opening: %r", full_response[:100])
        return EngineResult(text=full_response)

    async def process_stream(self, user_text: str) -> AsyncIterator[EngineResult]:
        self._turn += 1
        self.memory.add_user(user_text)

        system   = build_system_prompt(self.memory)
        messages = self.memory.get_messages()

        logger.info("[AGENT] Turn %d | user: %r", self._turn, user_text[:100])

        full_response = ""
        buffer = ""

        try:
            stream = await self._client.chat.completions.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
                stream=True,
                messages=[{"role": "system", "content": system}] + messages,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content or ""
                buffer += delta
                full_response += delta
                sentences, buffer = _split_sentences(buffer)
                for sentence in sentences:
                    cleaned = _clean(sentence)
                    if cleaned:
                        yield EngineResult(text=cleaned)

            if buffer.strip():
                cleaned = _clean(buffer)
                if cleaned:
                    yield EngineResult(text=cleaned)

        except Exception as exc:
            logger.error("[AGENT] API error on turn %d: %s", self._turn, exc)
            fallback = _fallback_for(user_text, self.memory.last_assistant_text())
            yield EngineResult(text=fallback, is_shortcut=True)
            full_response = fallback

        if not full_response.strip():
            full_response = _fallback_for(user_text, self.memory.last_assistant_text())

        self.memory.add_assistant(full_response)
        logger.info("[AGENT] Turn %d | agent: %r", self._turn, full_response[:120])

        asyncio.create_task(
            self._background_extract(user_text, full_response),
            name=f"lead_extract_t{self._turn}",
        )

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _background_extract(self, user_text: str, agent_text: str) -> None:
        try:
            updates = await extract_lead_update(
                client=self._client,
                user_text=user_text,
                agent_text=agent_text,
                current_lead=self.memory.lead,
            )
            if updates:
                self.memory.update_lead(updates)
                logger.info("[AGENT] Lead update: %s", updates)
        except Exception:
            logger.debug("[AGENT] Lead extraction failed (non-fatal)", exc_info=True)
