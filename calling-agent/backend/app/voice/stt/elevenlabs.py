"""
ElevenLabs Scribe Speech-to-Text provider.

Mirrors SarvamSTT's contract exactly: one batch request per finalized VAD
segment, same normalizer, same shared-client + timeout-cap + retire-client
scaffolding, same exception surface (RateLimitError on 429, RuntimeError on
out-of-credits) — so STTRouter, hedging, and the tracking wrapper behave
identically regardless of which provider is primary.

Provider-specific facts (benched from this VM, 2026-08-07):
  * p50 ~670ms / max ~1s per 2.5s segment — slower than healthy saarika
    (150-450ms) but inside the router's hedge envelope. STT_PROVIDER
    selection in the provider factory decides who is primary.
  * language pin uses ISO-639-1 short codes; the response returns ISO-639-3
    ("hin") + language_probability on EVERY response — mapped back to the
    Sarvam-style BCP-47 codes ("hi-IN") the pipeline's language mirror and
    lock were built against.
  * raw PCM upload (file_format=pcm_s16le_16) skips the WAV wrapper when the
    segment is already 16kHz mono PCM16 — which pipeline audio always is.

Requires ELEVENLABS_API_KEY (read live via el_keys.current_key()).
"""

from __future__ import annotations

import asyncio
import os
import logging

import httpx

from .base import (
    STTProvider, STTResult, RateLimitError, pcm_to_wav, normalize_pcm_audio,
)
from ..el_keys import current_key, concurrency_gate, EL_API_BASE

logger = logging.getLogger(__name__)

SCRIBE_API_URL = f"{EL_API_BASE}/v1/speech-to-text"
SCRIBE_TIMEOUT = 10.0
# Benched healthy distribution: p50 ~670ms, max ~1s. The read cap fails a
# hung request fast enough that the router's retry / Whisper hedge still
# lands inside a salvageable turn (same doctrine as SARVAM_READ_TIMEOUT_S).
EL_STT_CONNECT_TIMEOUT_S = float(os.environ.get("EL_STT_CONNECT_TIMEOUT_S", "3.0"))
EL_STT_READ_TIMEOUT_S = float(os.environ.get("EL_STT_READ_TIMEOUT_S", "4.0"))

EL_STT_MODEL = os.environ.get("ELEVENLABS_STT_MODEL", "scribe_v2").strip()

EL_HTTP_SHARED = os.environ.get("EL_HTTP_SHARED", "true").lower() == "true"
EL_KEEPALIVE_EXPIRY_S = float(os.environ.get("EL_KEEPALIVE_EXPIRY_S", "300"))

_shared_client: httpx.AsyncClient | None = None

# Scribe language codes: ISO-639-1 in requests, ISO-639-3 in responses.
# The pipeline carries Sarvam-style codes end-to-end ("hi-IN" locks, "hi"
# short codes), so both directions are mapped here and nowhere else.
_SHORT_TO_SCRIBE = {
    "en": "en", "hi": "hi", "gu": "gu", "mr": "mr", "ta": "ta", "te": "te",
    "kn": "kn", "ml": "ml", "bn": "bn", "pa": "pa", "od": "or",  # Odia = "or"
}
_ISO3_TO_BCP47 = {
    "eng": "en-IN", "hin": "hi-IN", "guj": "gu-IN", "mar": "mr-IN",
    "tam": "ta-IN", "tel": "te-IN", "kan": "kn-IN", "mal": "ml-IN",
    "ben": "bn-IN", "pan": "pa-IN", "ori": "od-IN", "ory": "od-IN",
}


def _make_client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(
            timeout,
            connect=EL_STT_CONNECT_TIMEOUT_S,
            read=EL_STT_READ_TIMEOUT_S,
        ),
        limits=httpx.Limits(
            max_keepalive_connections=5,
            keepalive_expiry=EL_KEEPALIVE_EXPIRY_S if EL_HTTP_SHARED else 60,
        ),
    )


class ScribeRateLimitError(RateLimitError):
    """HTTP 429 from ElevenLabs (too_many_concurrent_requests / system_busy)."""


class ScribeSTT(STTProvider):
    """
    ElevenLabs Scribe STT provider — one multipart POST per VAD segment.
    """

    _warmup_done_flag: bool = False

    # Hedge floors read by STTRouter (see its __init__): Scribe's HEALTHY
    # distribution is p50 ~650ms / max ~1s (benched from this VM 2026-08-07)
    # — the saarika-calibrated 0.6s retry trigger would re-race every single
    # turn. Floors sit above healthy-p95 so hedges fire only on real hangs.
    RETRY_HEDGE_FLOOR_S = 1.2
    HEDGE_DELAY_FLOOR_S = 1.6

    def __init__(
        self,
        api_key: str | None = None,
        api_url: str = SCRIBE_API_URL,
        timeout: float = SCRIBE_TIMEOUT,
        model: str | None = None,
    ) -> None:
        self._api_key = api_key or current_key()
        self._api_url = api_url
        self._timeout = timeout
        self._model = model or EL_STT_MODEL
        # Same per-call dynamic normalizer the Sarvam provider uses — the
        # gain/noise-gate adaptation is about the CALLER's line, not the API.
        from .audio_norm import DynamicNormalizer
        self._normalizer = DynamicNormalizer()
        self._client: httpx.AsyncClient | None = None

        if not self._api_key:
            logger.warning("[STT:Scribe] ELEVENLABS_API_KEY not set — calls will fail")
        else:
            masked = self._api_key[:6] + "…" + self._api_key[-4:]
            logger.info("[STT:Scribe] API key loaded: %s (model=%s)",
                        masked, self._model)

    def _get_client(self) -> httpx.AsyncClient:
        if EL_HTTP_SHARED:
            global _shared_client
            if _shared_client is None or _shared_client.is_closed:
                _shared_client = _make_client(self._timeout)
            return _shared_client
        if self._client is None:
            self._client = _make_client(self._timeout)
        return self._client

    async def transcribe(
        self,
        audio_bytes: bytes,
        sample_rate: int = 16_000,
        language: str = "en",
    ) -> STTResult:
        if getattr(self, "_normalizer", None) is not None:
            audio_bytes = self._normalizer.process(audio_bytes, sample_rate)
        else:
            audio_bytes = normalize_pcm_audio(audio_bytes)

        duration_ms = len(audio_bytes) * 1000 // (sample_rate * 2)

        # Language mapping (mirror of SarvamSTT):
        #   "unknown"      → omit; Scribe auto-detects
        #   "hi" / "hi-IN" → "hi" (Scribe wants ISO-639-1)
        short = language.split("-")[0].lower() if language else "unknown"
        lang_code = None if short == "unknown" else _SHORT_TO_SCRIBE.get(short, short)

        # Live key on every request so a key swap needs no restart. The
        # key_provider attribute keeps the per-call-lease interface shape.
        _provider = getattr(self, "key_provider", None)
        self._api_key = _provider() if _provider is not None else current_key()

        form: dict[str, str] = {
            "model_id": self._model,
            # Never let Scribe spend time labelling laughter/music — the
            # validators downstream only ever see plain text anyway.
            "tag_audio_events": "false",
            "diarize": "false",
        }
        if lang_code:
            form["language_code"] = lang_code

        # Raw-PCM fast path: pipeline audio is always 16kHz mono PCM16, which
        # is exactly file_format=pcm_s16le_16 — no WAV container to build.
        if sample_rate == 16_000:
            form["file_format"] = "pcm_s16le_16"
            upload = ("audio.pcm", audio_bytes, "application/octet-stream")
        else:
            upload = ("audio.wav", pcm_to_wav(audio_bytes, sample_rate=sample_rate),
                      "audio/wav")

        logger.info(
            "[STT:Scribe] Sending %d bytes (%.0fms) lang=%s",
            len(upload[1]), duration_ms, lang_code or "auto",
        )

        client = self._get_client()
        try:
            async with concurrency_gate():
                response = await client.post(
                    self._api_url,
                    headers={"xi-api-key": self._api_key},
                    data=form,
                    files={"file": upload},
                )
        except httpx.TimeoutException as exc:
            logger.warning(
                "[STT:Scribe] Request timed out: %s — resetting client for retry", exc
            )
            await self._retire_client()
            raise
        except (httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadError) as exc:
            logger.warning(
                "[STT:Scribe] Stale connection error: %s — resetting client for retry",
                exc,
            )
            await self._retire_client()
            raise

        if response.status_code == 401:
            # ElevenLabs signals out-of-credits as 401 detail.status
            # "quota_exceeded" — the moral equivalent of Sarvam's 402.
            detail = ""
            try:
                detail = (response.json().get("detail") or {}).get("status", "")
            except Exception:
                pass
            if detail == "quota_exceeded":
                logger.error(
                    "[EL:CREDITS] STT quota exhausted — top up the ElevenLabs "
                    "workspace (subscription endpoint has the real remaining count)."
                )
                raise RuntimeError(
                    "[EL:CREDITS] Credits exhausted — refill the ElevenLabs workspace."
                )
            logger.error("[STT:Scribe] HTTP 401 (%s) — check ELEVENLABS_API_KEY",
                         detail or "invalid key?")
            response.raise_for_status()

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            logger.warning(
                "[STT:Scribe] Rate limited (HTTP 429) Retry-After=%s", retry_after
            )
            raise ScribeRateLimitError(retry_after=retry_after)

        if response.status_code != 200:
            logger.error(
                "[STT:Scribe] HTTP %d — body: %s",
                response.status_code,
                response.text[:500],
            )
            response.raise_for_status()

        data = response.json()
        transcript = (data.get("text") or "").strip()
        iso3 = (data.get("language_code") or "").lower() or None
        detected = _ISO3_TO_BCP47.get(iso3) if iso3 else None
        lang_prob = data.get("language_probability")
        logger.info(
            "[STT:Scribe] Transcript: %r (lang=%s%s)",
            transcript,
            detected or iso3,
            "" if lang_prob is None else f" p={lang_prob:.2f}",
        )
        # Scribe has word-level logprob confidence, but no calibrated overall
        # field — keep confidence=None like Sarvam (the router derives one).
        return STTResult(
            text=transcript,
            confidence=None,
            detected_language=detected,
            language_probability=(
                float(lang_prob) if lang_prob is not None else None),
        )

    async def warmup(self) -> None:
        """Pre-open TLS to api.elevenlabs.io (mirror of SarvamSTT.warmup)."""
        if ScribeSTT._warmup_done_flag:
            logger.debug("[STT:Scribe] Warmup skipped — already done this process")
            return
        try:
            client = self._get_client()
            await asyncio.wait_for(client.head(self._api_url), timeout=3.0)
            ScribeSTT._warmup_done_flag = True
            logger.info("[STT:Scribe] Connection warmed (first time this process)")
        except Exception as exc:
            logger.debug("[STT:Scribe] Warmup skipped: %s", exc)

    async def _retire_client(self) -> None:
        """Detach the current client and close it in the background —
        identical rationale to SarvamSTT._retire_client (never aclose() a
        shared client synchronously mid-error)."""
        global _shared_client
        if EL_HTTP_SHARED:
            client, _shared_client = _shared_client, None
        else:
            client, self._client = self._client, None
        if client is None:
            return

        async def _drain() -> None:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001 — teardown of a dead client
                pass

        try:
            asyncio.get_running_loop().create_task(_drain())
        except RuntimeError:
            await _drain()

    async def close(self) -> None:
        global _shared_client
        if EL_HTTP_SHARED:
            client, _shared_client = _shared_client, None
        else:
            client, self._client = self._client, None
        if client:
            await client.aclose()
