"""
Sarvam AI Speech-to-Text provider.

Uses the Sarvam REST API with multipart WAV upload.
Requires SARVAM_API_KEY environment variable.
"""

from __future__ import annotations

import os
import logging

import httpx

from .base import STTProvider, STTResult, pcm_to_wav, normalize_pcm_audio

logger = logging.getLogger(__name__)

SARVAM_API_URL = "https://api.sarvam.ai/speech-to-text"
SARVAM_TIMEOUT = 10.0


class SarvamRateLimitError(Exception):
    """Raised when Sarvam API returns HTTP 429 (Too Many Requests).

    The caller (STTRouter) uses this to apply backoff and optionally
    route to the fallback provider rather than retrying immediately.
    """
    def __init__(self, retry_after: str | None = None) -> None:
        self.retry_after = retry_after
        super().__init__(f"Sarvam rate limited (Retry-After: {retry_after})")


class SarvamSTT(STTProvider):
    """
    Sarvam AI STT provider.

    Converts PCM audio to WAV, uploads via multipart POST, and extracts
    the transcript from the JSON response.

    Uses a persistent httpx client for connection reuse.
    """

    # Class-level flag: warmup runs at most once per process regardless of
    # how many calls / pipeline instances are created (Issue 6).
    _warmup_done_flag: bool = False

    def __init__(
        self,
        api_key: str | None = None,
        api_url: str = SARVAM_API_URL,
        timeout: float = SARVAM_TIMEOUT,
        model: str = "saaras:v3",
        mode: str = "codemix",
    ) -> None:
        self._api_key = api_key or os.environ.get("SARVAM_API_KEY", "")
        self._api_url = api_url
        self._timeout = timeout
        self._model = model
        self._mode = mode
        self._client: httpx.AsyncClient | None = None

        if not self._api_key:
            logger.warning("[STT:Sarvam] SARVAM_API_KEY not set — calls will fail")
        else:
            masked = self._api_key[:6] + "…" + self._api_key[-4:]
            logger.info("[STT:Sarvam] API key loaded: %s", masked)

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            # keepalive_expiry=60: idle connections older than 60s are never
            # reused, so a call after a long pause always gets a fresh TLS
            # handshake instead of dying on a silently-dropped server socket.
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                limits=httpx.Limits(max_keepalive_connections=5, keepalive_expiry=60),
            )
        return self._client

    async def transcribe(
        self,
        audio_bytes: bytes,
        sample_rate: int = 16_000,
        language: str = "en",
    ) -> STTResult:
        # Normalize amplitude: telephone audio is often quiet; boosting it
        # before upload significantly improves STT accuracy on quiet callers.
        audio_bytes = normalize_pcm_audio(audio_bytes)
        wav_data = pcm_to_wav(audio_bytes, sample_rate=sample_rate)
        duration_ms = len(audio_bytes) // (sample_rate * 2) * 1000
        logger.info(
            "[STT:Sarvam] Sending %d bytes (%.0fms) lang=%s mode=%s model=%s",
            len(wav_data), duration_ms, language, self._mode, self._model,
        )

        # Language mapping:
        # - "unknown" → pass through, Sarvam auto-detects
        # - "en" → "en-IN", "hi" → "hi-IN"
        # - already-suffixed codes pass through
        if language == "unknown":
            lang_code = "unknown"
        elif "-" in language:
            lang_code = language
        else:
            lang_code = f"{language}-IN"

        client = self._get_client()

        try:
            response = await client.post(
                self._api_url,
                headers={"api-subscription-key": self._api_key},
                data={
                    "language_code": lang_code,
                    "model": self._model,
                    "mode": self._mode,
                },
                files={"file": ("audio.wav", wav_data, "audio/wav")},
            )
        except httpx.TimeoutException as exc:
            logger.warning("[STT:Sarvam] Request timed out: %s", exc)
            raise
        except (httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadError) as exc:
            # Stale or closed connection — discard the client so the next
            # attempt (STT router retry or next turn) opens a fresh one.
            logger.warning(
                "[STT:Sarvam] Stale connection error: %s — resetting client for retry", exc
            )
            await self.close()
            raise

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            logger.warning(
                "[STT:Sarvam] Rate limited (HTTP 429) Retry-After=%s", retry_after
            )
            raise SarvamRateLimitError(retry_after=retry_after)

        if response.status_code != 200:
            logger.error(
                "[STT:Sarvam] HTTP %d — body: %s",
                response.status_code,
                response.text[:500],
            )
            response.raise_for_status()

        data = response.json()
        transcript = data.get("transcript", "").strip()
        confidence = float(data.get("confidence", 1.0))
        detected_lang = data.get("language_code") or None
        lang_prob = data.get("language_probability")
        lang_prob = float(lang_prob) if lang_prob is not None else None
        logger.info(
            "[STT:Sarvam] RAW=%r (confidence=%.2f lang=%s lang_prob=%s)",
            transcript, confidence, detected_lang,
            f"{lang_prob:.2f}" if lang_prob is not None else "n/a",
        )
        return STTResult(
            text=transcript,
            confidence=confidence,
            detected_language=detected_lang,
            language_probability=lang_prob,
        )

    async def warmup(self) -> None:
        """Pre-open a TLS connection to the Sarvam host so the first real
        transcribe() call doesn't pay DNS+TCP+TLS cost (~300ms cold start).

        We fire a cheap HEAD request — Sarvam returns 404/405 for it, but the
        connection lands in httpx's per-host pool and the next POST reuses it.
        Errors are silently ignored.

        Runs at most once per process (class-level flag) — Issue 6.
        """
        if SarvamSTT._warmup_done_flag:
            logger.debug("[STT:Sarvam] Warmup skipped — already done this process")
            return
        try:
            import asyncio
            client = self._get_client()
            await asyncio.wait_for(client.head(self._api_url), timeout=3.0)
            SarvamSTT._warmup_done_flag = True
            logger.info("[STT:Sarvam] Connection warmed (first time this process)")
        except Exception as exc:
            logger.debug("[STT:Sarvam] Warmup skipped: %s", exc)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
