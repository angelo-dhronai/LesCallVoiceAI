"""
Sarvam AI saaras:v3 WebSocket STT provider.

Maintains a persistent WebSocket connection per language for the call duration.
One connection handles all turns in a session; reconnects on language change
or transport error.  Uses the sarvamai SDK's async context manager pattern.

Language detection strategy
----------------------------
Pass language_code="unknown" for the first turn (before language is locked).
Sarvam's model detects the language natively and returns the detected code in
message.data.language_code.  Once locked the correct BCP-47 code is passed
directly (e.g. "gu-IN") — single connection, no heuristics.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time

from sarvamai import AsyncSarvamAI

from .base import STTProvider, STTResult, pcm_to_wav, normalize_pcm_audio

logger = logging.getLogger(__name__)

_LANG_CODE_MAP = {
    "en": "en-IN",
    "hi": "hi-IN",
    "gu": "gu-IN",
    # "unknown" is NOT in this map — passed through as-is
}

TRANSCRIBE_TIMEOUT = 10.0
WARMUP_TIMEOUT = 5.0


class SarvamWSSTT(STTProvider):
    """
    Sarvam saaras:v3 WebSocket STT — persistent connection pool per language.

    Compared with SarvamSTT (HTTP):
    - Telephony-tuned model (saaras:v3 vs saarika:v2.5)
    - No per-segment TCP/TLS handshake cost
    - flush_signal for clean segment boundaries
    - Built-in high-sensitivity VAD

    For "unknown" language (first turn before lock), passes language_code="unknown"
    to a single connection. Sarvam detects the language internally and returns
    it in message.data.language_code, which is propagated as STTResult.detected_language
    so the pipeline can lock the conversation language immediately.
    """

    _warmup_done_flag: bool = False

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "saaras:v3",
    ) -> None:
        self._api_key = api_key or os.environ.get("SARVAM_API_KEY", "")
        self._model = model
        self._sarvam_client: AsyncSarvamAI | None = None

        # Connection pool: lang_code → (ws, ctx)
        self._pool: dict[str, tuple] = {}

        if not self._api_key:
            logger.warning("[STT:SarvamWS] SARVAM_API_KEY not set — calls will fail")
        else:
            masked = self._api_key[:6] + "…" + self._api_key[-4:]
            logger.info("[STT:SarvamWS] API key loaded: %s", masked)

    def _get_client(self) -> AsyncSarvamAI:
        if self._sarvam_client is None:
            self._sarvam_client = AsyncSarvamAI(api_subscription_key=self._api_key)
        return self._sarvam_client

    async def _ensure_connected(self, lang_code: str):
        entry = self._pool.get(lang_code)
        if entry is not None and entry[0] is not None:
            return entry[0]
        await self._close_lang(lang_code)
        logger.info("[STT:SarvamWS] Opening WS lang=%s model=%s", lang_code, self._model)
        ctx = self._get_client().speech_to_text_streaming.connect(
            language_code=lang_code,
            model=self._model,
            mode="transcribe",
            flush_signal="true",
            high_vad_sensitivity="true",
        )
        ws = await ctx.__aenter__()
        self._pool[lang_code] = (ws, ctx)
        logger.info("[STT:SarvamWS] WS connection established lang=%s", lang_code)
        return ws

    async def _close_lang(self, lang_code: str) -> None:
        entry = self._pool.pop(lang_code, None)
        if entry is None:
            return
        _, ctx = entry
        if ctx is not None:
            try:
                await ctx.__aexit__(None, None, None)
            except Exception as exc:
                logger.debug("[STT:SarvamWS] Close error lang=%s: %s", lang_code, exc)

    async def _close_all(self) -> None:
        for lc in list(self._pool.keys()):
            await self._close_lang(lc)

    def _map_lang(self, language: str) -> str:
        if "-" in language:
            return language
        if language == "unknown":
            return "unknown"
        return _LANG_CODE_MAP.get(language, "hi-IN")

    async def _transcribe_lang(
        self,
        lang_code: str,
        b64: str,
        sample_rate: int,
    ) -> STTResult:
        """Transcribe pre-encoded audio with a specific language connection."""
        for attempt in range(2):
            try:
                ws = await self._ensure_connected(lang_code)
                await ws.transcribe(audio=b64, encoding="audio/wav", sample_rate=sample_rate)
                await ws.flush()

                deadline = time.monotonic() + TRANSCRIBE_TIMEOUT
                while time.monotonic() < deadline:
                    remaining = max(0.1, deadline - time.monotonic())
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
                    except asyncio.TimeoutError:
                        logger.warning("[STT:SarvamWS] Timeout lang=%s", lang_code)
                        await self._close_lang(lang_code)
                        break

                    msg_type = getattr(msg, "type", None)
                    if msg_type == "data":
                        data = getattr(msg, "data", None)
                        transcript = (getattr(data, "transcript", "") or "").strip()
                        detected = getattr(data, "language_code", None)
                        logger.info(
                            "[STT:SarvamWS] Transcript lang=%s: %r detected=%s",
                            lang_code, transcript, detected,
                        )
                        return STTResult(
                            text=transcript,
                            confidence=1.0,
                            detected_language=detected or None,
                        )
                    elif msg_type == "events":
                        continue
                    elif msg_type == "error":
                        error_data = getattr(msg, "data", None)
                        logger.warning("[STT:SarvamWS] Server error lang=%s: %s", lang_code, error_data)
                        await self._close_lang(lang_code)
                        break
                    else:
                        logger.debug("[STT:SarvamWS] Ignoring msg type=%s lang=%s", msg_type, lang_code)

                return STTResult(text="", confidence=1.0)

            except Exception as exc:
                logger.warning(
                    "[STT:SarvamWS] Error attempt %d/2 lang=%s: %s — resetting",
                    attempt + 1, lang_code, exc,
                )
                await self._close_lang(lang_code)
                if attempt == 1:
                    return STTResult(text="", confidence=0.0)

        return STTResult(text="", confidence=0.0)

    async def transcribe(
        self,
        audio_bytes: bytes,
        sample_rate: int = 16_000,
        language: str = "en",
    ) -> STTResult:
        lang_code = self._map_lang(language)
        audio_bytes = normalize_pcm_audio(audio_bytes)
        wav_data = pcm_to_wav(audio_bytes, sample_rate=sample_rate)
        b64 = base64.b64encode(wav_data).decode()
        duration_ms = len(audio_bytes) / (sample_rate * 2) * 1000
        logger.info("[STT:SarvamWS] Sending %.0fms lang=%s", duration_ms, lang_code)

        # Single connection for all cases.
        # "unknown" → Sarvam detects language natively, returns it in language_code.
        # Locked language (hi-IN, gu-IN, en-IN) → single connection, no dual.
        return await self._transcribe_lang(lang_code, b64, sample_rate)

    async def warmup(self) -> None:
        if SarvamWSSTT._warmup_done_flag:
            logger.debug("[STT:SarvamWS] Warmup already done this process")
            return
        try:
            await asyncio.wait_for(
                self._ensure_connected("unknown"),
                timeout=WARMUP_TIMEOUT,
            )
            SarvamWSSTT._warmup_done_flag = True
            logger.info("[STT:SarvamWS] Warmup complete — unknown connection open")
        except Exception as exc:
            logger.debug("[STT:SarvamWS] Warmup skipped: %s", exc)

    async def close(self) -> None:
        await self._close_all()
        logger.info("[STT:SarvamWS] Closed")
