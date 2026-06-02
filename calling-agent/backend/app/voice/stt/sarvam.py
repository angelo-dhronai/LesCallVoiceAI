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

    def __init__(
        self,
        api_key: str | None = None,
        api_url: str = SARVAM_API_URL,
        timeout: float = SARVAM_TIMEOUT,
        model: str = "saarika:v2.5",
    ) -> None:
        self._api_key = api_key or os.environ.get("SARVAM_API_KEY") or os.environ.get("SARVAMAI_API_KEY", "")
        self._api_url = api_url
        self._timeout = timeout
        self._model = model
        self._client: httpx.AsyncClient | None = None

        if not self._api_key:
            logger.warning("[STT:Sarvam] SARVAM_API_KEY not set — calls will fail")

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
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
            "[STT:Sarvam] Sending %d bytes (%.0fms) lang=%s",
            len(wav_data), duration_ms, language,
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

        logger.info(
            "[SARVAM_STT_REQUEST] lang=%s bytes=%d duration_ms=%d",
            lang_code, len(wav_data), duration_ms,
        )

        try:
            response = await client.post(
                self._api_url,
                headers={"api-subscription-key": self._api_key},
                data={
                    "language_code": lang_code,
                    "model": self._model,
                },
                files={"file": ("audio.wav", wav_data, "audio/wav")},
            )
        except httpx.TimeoutException as exc:
            logger.warning("[STT:Sarvam] Request timed out: %s", exc)
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
        logger.info("[STT:Sarvam] Transcript: %r (confidence=%.2f)", transcript, confidence)
        return STTResult(text=transcript, confidence=confidence)

    async def warmup(self) -> None:
        """Pre-open a TLS connection to the Sarvam host so the first real
        transcribe() call doesn't pay DNS+TCP+TLS cost (~300ms cold start).

        We fire a cheap HEAD request — Sarvam returns 404/405 for it, but the
        connection lands in httpx's per-host pool and the next POST reuses it.
        Errors are silently ignored.
        """
        try:
            import asyncio
            logger.info("[SARVAM_WARMUP] Firing HEAD warmup to %s (no credits consumed)", self._api_url)
            client = self._get_client()
            await asyncio.wait_for(client.head(self._api_url), timeout=3.0)
            logger.info("[STT:Sarvam] Connection warmed")
        except Exception as exc:
            logger.debug("[STT:Sarvam] Warmup skipped: %s", exc)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
