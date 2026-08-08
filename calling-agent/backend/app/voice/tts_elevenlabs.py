"""
ElevenLabs streaming TTS via the HTTP stream endpoint.

Subclasses SarvamTTS and overrides ONLY the provider boundary
(request/stream/keys/cache-key/voice validation). Everything load-bearing —
utterance splitting, early first-flush, the 4s flush ceiling, canned-audio
cache semantics, barge-in billed-chars accounting, the double-speak guard,
echo-mute duration reporting — is inherited from tts_sarvam.py, so the two
providers can never drift apart on pipeline behaviour.

Provider facts (benched from this VM, 2026-08-07):
  * eleven_flash_v2_5 + output_format=pcm_8000 + optimize_streaming_latency=3
    → TTFB p50 ~176ms (vs ~350ms healthy Sarvam first chunk). pcm_8000 means
    TelephonySink keeps skipping the ffmpeg downsample, same as Sarvam at
    TTS_OUTPUT_SR=8000.
  * Gujarati is NOT supported by flash/turbo/multilingual — only eleven_v3,
    which is not a real-time model. Per-language routing to Sarvam for such
    languages lives in tts_hybrid.py, not here.
  * Utterance parts are stitched with previous_request_ids so clause-chunked
    synthesis keeps one prosodic line — something Sarvam could not do.

"speaker" carries the ElevenLabs voice_id (mapped from bulbul speaker names
by shared/voice_map.py — agent YAMLs are never rewritten for the switch).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from pathlib import Path

import httpx

from .tts_stream import StreamingTTS, TTSChunk
from .tts_sarvam import (
    SarvamTTS,
    TTS_CACHE_DIR,
    STREAM_TIMEOUT,
    STREAM_TIMEOUT_FIRST_ATTEMPT,
    CONNECT_TIMEOUT,
    CHUNK_READ_SIZE,
)
from .el_keys import current_key, concurrency_gate, EL_API_BASE
from ..audio_transport.base import AudioSink
from ..audio_transport.local_speaker_sink import LocalSpeakerSink

logger = logging.getLogger(__name__)

EL_VALID_MODELS = (
    "eleven_flash_v2_5",
    "eleven_turbo_v2_5",
    "eleven_multilingual_v2",
    "eleven_v3",
)
EL_DEFAULT_MODEL = os.environ.get("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5")
# Shivank — conversational hi-IN male agent voice (workspace-added 2026-08-07).
EL_DEFAULT_VOICE = os.environ.get(
    "ELEVENLABS_TTS_VOICE", "JvXnN4vCCagzhSTjMMjl")

# Models that accept an explicit language_code (ISO-639-1). multilingual_v2
# rejects the parameter outright; v3 ignores enforcement gaps differently —
# only the v2.5 family gets the pin.
_LANG_CODE_MODELS = ("eleven_flash_v2_5", "eleven_turbo_v2_5")

# 0-4; 3 = max latency optimizations without text-normalizer sacrifice.
# eleven_v3 rejects the parameter entirely (HTTP 400 unsupported_model).
EL_STREAM_LATENCY_MODE = os.environ.get("EL_STREAM_LATENCY_MODE", "3")

# eleven_v3 read cap. v3 is not a realtime model: warm TTFB ~1.4s, and a
# COLD voice (first v3 generation for that voice this while) can stall past
# the flash-calibrated 3.5s STREAM_TIMEOUT — the timeout+retry then doubles
# the wait (8.3s observed 2026-08-07). One roomier wait beats two stacked
# ones; only v3 requests pay it.
EL_V3_STREAM_TIMEOUT_S = float(os.environ.get("EL_V3_STREAM_TIMEOUT_S", "8.0"))

# Sarvam pace → ElevenLabs voice_settings.speed. The fleet's agent.yaml pace
# values (1.0-1.15) were tuned against Bulbul's slow baseline delivery;
# ElevenLabs voices speak naturally at 1.0, so carrying 1.15 over verbatim
# sounds rushed. speed = 1.0 + (pace - 1.0) * SCALE, clamped to EL's
# supported [0.7, 1.2]. SCALE=0.5 halves the exaggeration (1.15 → 1.075);
# EL_SPEED_FROM_PACE_SCALE=0 pins every voice to its natural 1.0.
EL_SPEED_FROM_PACE_SCALE = float(
    os.environ.get("EL_SPEED_FROM_PACE_SCALE", "0.5"))
_EL_SPEED_MIN, _EL_SPEED_MAX = 0.7, 1.2

# Optional fixed voice-settings knobs (unset = the voice's stored defaults).
_EL_STABILITY = os.environ.get("EL_TTS_STABILITY", "").strip()
_EL_SIMILARITY = os.environ.get("EL_TTS_SIMILARITY", "").strip()

_PCM_FORMATS = {
    8000: "pcm_8000",
    16000: "pcm_16000",
    22050: "pcm_22050",
    24000: "pcm_24000",
    44100: "pcm_44100",
}

EL_HTTP_SHARED = os.environ.get("EL_HTTP_SHARED", "true").lower() == "true"
EL_KEEPALIVE_EXPIRY_S = float(os.environ.get("EL_KEEPALIVE_EXPIRY_S", "300"))

_shared_client: httpx.AsyncClient | None = None


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(STREAM_TIMEOUT, connect=CONNECT_TIMEOUT),
        limits=httpx.Limits(
            max_keepalive_connections=5,
            keepalive_expiry=EL_KEEPALIVE_EXPIRY_S if EL_HTTP_SHARED else 60,
        ),
    )


def speed_from_pace(pace: float) -> float:
    """Map a Sarvam-scale pace knob onto EL's speed range (see module doc)."""
    pace = pace if pace else 1.0
    speed = 1.0 + (pace - 1.0) * EL_SPEED_FROM_PACE_SCALE
    return round(min(max(speed, _EL_SPEED_MIN), _EL_SPEED_MAX), 3)


class ElevenLabsTTS(SarvamTTS):
    """ElevenLabs TTS riding SarvamTTS's streaming/caching machinery."""

    # Independent warmup flag — warming Sarvam must not skip warming EL.
    _warmup_done_flag: bool = False

    def __init__(
        self,
        api_key: str | None = None,
        model: str = EL_DEFAULT_MODEL,
        speaker: str = EL_DEFAULT_VOICE,   # ElevenLabs voice_id
        sample_rate: int = 16_000,
        pace: float = 1.0,
        output_device: int | str | None = None,
        audio_sink: AudioSink | None = None,
    ) -> None:
        # Bypass SarvamTTS.__init__ (it validates against bulbul model names);
        # set up the grandparent + the attribute surface the inherited
        # speak()/speak_cached()/_check_stream_rate() methods rely on.
        StreamingTTS.__init__(self, audio_sink=audio_sink or LocalSpeakerSink(
            sample_rate=sample_rate,
            output_device=output_device,
        ))
        self._api_key = api_key or current_key()
        if model not in EL_VALID_MODELS:
            logger.warning("[TTS:EL] invalid model %r — using %s (valid: %s)",
                           model, EL_DEFAULT_MODEL, ", ".join(EL_VALID_MODELS))
            model = EL_DEFAULT_MODEL
        self._model = model
        self._speaker = speaker
        self._sample_rate = sample_rate
        self._pace = pace
        self._client = None
        self._language = "en"
        self._output_device = output_device
        self._capture_buf = None
        self._last_speak_clean = False
        self._last_speak_billed_chars = 0
        self._stream_truncated = False
        # request-id stitching context: the ids of this utterance's already-
        # synthesized parts (max 3 sent). Cleared by _reset() → per-utterance.
        self._req_ids: list[str] = []

        if not self._api_key:
            logger.warning("[TTS:EL] ELEVENLABS_API_KEY not set — calls will fail")
        else:
            masked = self._api_key[:6] + "…" + self._api_key[-4:]
            logger.info("[TTS:EL] API key loaded: %s (model=%s voice=%s)",
                        masked, self._model, self._speaker)

    # ------------------------------------------------------------------
    # Provider boundary overrides
    # ------------------------------------------------------------------

    def _reset(self) -> None:
        super()._reset()
        # New utterance (or barge-in) — stitching across it would be wrong.
        self._req_ids = []

    def set_language(self, lang: str) -> None:
        """Track language AND pick a model that can actually speak it.

        flash/turbo cover 32 languages; everything else (gu, mr, te, …) is
        eleven_v3-only. Sending v3-only text through flash produces multi-
        second garbage (benched 2026-08-07), so a single EL instance swaps to
        eleven_v3 for those languages and back to its configured realtime
        model when the conversation returns to a covered one. The voice stays
        the same — one speaker per agent across languages (accent shift beats
        a person swap, RECOMMENDED_SPEAKERS doctrine).
        """
        super().set_language(lang)
        self._apply_model_for_language()

    def _apply_model_for_language(self) -> None:
        short = (self._language or "en").split("-")[0].lower()
        if not hasattr(self, "_configured_model"):
            self._configured_model = self._model
        try:
            from ..agent_definition.voice_catalog import EL_REALTIME_LANGUAGES
            realtime_ok = short in EL_REALTIME_LANGUAGES
        except Exception:  # noqa: BLE001 — catalog must never kill a call
            realtime_ok = True
        if not realtime_ok and self._model != "eleven_v3":
            logger.info("[TTS:EL] language %r not in the realtime set — "
                        "switching model %s → eleven_v3", short, self._model)
            self._model = "eleven_v3"
        elif realtime_ok and self._model == "eleven_v3" \
                and self._configured_model != "eleven_v3":
            logger.info("[TTS:EL] language %r back in the realtime set — "
                        "restoring model %s", short, self._configured_model)
            self._model = self._configured_model

    def set_voice(self, model: str | None = None, speaker: str | None = None,
                  pace: float | None = None) -> None:
        """Per-language voice on mid-call language switch (EL flavour).

        Self-defending against bulbul-era configs: agent YAMLs still carry
        (bulbul:v3, shubh) and the pipeline passes them through verbatim on
        a language switch — a bulbul model keeps the current EL model, and a
        bulbul speaker name is shim-mapped to its EL voice_id (an unmapped
        voice_id here would 404 every utterance = silent call).
        """
        if model:
            if model in EL_VALID_MODELS:
                self._model = model
                self._configured_model = model
                # A model the current language can't ride is corrected back
                # (e.g. flash configured while the conversation is in gu).
                self._apply_model_for_language()
            elif model.startswith("bulbul"):
                logger.debug("[TTS:EL] set_voice: bulbul model %r — keeping %s",
                             model, self._model)
            else:
                logger.warning("[TTS:EL] set_voice: invalid model %r ignored", model)
        if speaker:
            try:
                from ..agent_definition.voice_catalog import el_voice_for_speaker
                self._speaker = el_voice_for_speaker(speaker)
            except Exception:  # noqa: BLE001 — catalog must never kill a call
                self._speaker = speaker
        if pace:
            try:
                p = float(pace)
                if 0.5 <= p <= 2.0:
                    self._pace = p
            except (TypeError, ValueError):
                pass

    def _get_client(self) -> httpx.AsyncClient:
        if EL_HTTP_SHARED:
            global _shared_client
            if _shared_client is None or _shared_client.is_closed:
                _shared_client = _make_client()
            return _shared_client
        if self._client is None:
            self._client = _make_client()
        return self._client

    def _reset_client(self) -> None:
        global _shared_client
        if EL_HTTP_SHARED:
            _shared_client = None
        else:
            self._client = None

    async def warmup(self) -> None:
        if ElevenLabsTTS._warmup_done_flag:
            logger.debug("[TTS:EL] Warmup skipped — already done this process")
            return
        try:
            client = self._get_client()
            await asyncio.wait_for(
                client.head(f"{EL_API_BASE}/v1/models"), timeout=3.0)
            ElevenLabsTTS._warmup_done_flag = True
            logger.info("[TTS:EL] Connection warmed (first time this process)")
        except Exception as exc:
            logger.debug("[TTS:EL] Warmup skipped: %s", exc)

    def _cache_path(self, text: str) -> Path | None:
        """Provider-tagged cache key — an EL entry can never replay for a
        Sarvam voice or vice versa, and retuning speed mapping misses the
        cache (stale-pace replay trap, same doctrine as _PACE_CFG_TAG)."""
        from .tts_sarvam import TTS_CANNED_CACHE
        if not TTS_CANNED_CACHE:
            return None
        key = (
            f"el|{self._model}|{self._speaker}|{speed_from_pace(self._pace)}|"
            f"{self._language}|{self._sample_rate}|"
            f"{_EL_STABILITY}|{_EL_SIMILARITY}|{text}"
        )
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return TTS_CACHE_DIR / f"{digest}.pcm"

    async def _stream_audio(self, text: str):
        speed = speed_from_pace(self._pace)
        lang_short = (self._language or "en").split("-")[0]

        voice_settings: dict = {"speed": speed}
        if _EL_STABILITY:
            voice_settings["stability"] = float(_EL_STABILITY)
        if _EL_SIMILARITY:
            voice_settings["similarity_boost"] = float(_EL_SIMILARITY)

        payload: dict = {
            "text": text,
            "model_id": self._model,
            "voice_settings": voice_settings,
        }
        if self._model in _LANG_CODE_MODELS:
            payload["language_code"] = lang_short
        if self._req_ids:
            payload["previous_request_ids"] = self._req_ids[-3:]

        fmt = _PCM_FORMATS.get(self._sample_rate, "pcm_16000")
        url = (
            f"{EL_API_BASE}/v1/text-to-speech/{self._speaker}/stream"
            f"?output_format={fmt}"
        )
        if self._model != "eleven_v3":
            url += f"&optimize_streaming_latency={EL_STREAM_LATENCY_MODE}"

        max_retries = 8
        # Same double-speak rule as Sarvam: once any audio reached the sink,
        # a retry would restart the utterance — truncate instead.
        yielded_any = False
        self._stream_truncated = False
        for attempt in range(max_retries):
            if self._cancelled:
                return

            _provider = getattr(self, "key_provider", None)
            self._api_key = _provider() if _provider is not None \
                else current_key()
            client = self._get_client()
            try:
                async with concurrency_gate():
                    async with client.stream(
                        "POST",
                        url,
                        headers={
                            "xi-api-key": self._api_key,
                            "Content-Type": "application/json",
                        },
                        json=payload,
                        # eleven_v3 is not a realtime model — it gets its
                        # own roomier cap (see EL_V3_STREAM_TIMEOUT_S);
                        # realtime models keep the tight flash-calibrated
                        # first-attempt fail-fast.
                        timeout=httpx.Timeout(
                            EL_V3_STREAM_TIMEOUT_S
                            if self._model == "eleven_v3"
                            else (STREAM_TIMEOUT_FIRST_ATTEMPT
                                  if attempt == 0 else STREAM_TIMEOUT),
                            connect=CONNECT_TIMEOUT,
                        ),
                    ) as response:
                        if response.status_code == 401:
                            body = await response.aread()
                            if b"quota_exceeded" in body:
                                logger.error(
                                    "[EL:CREDITS] TTS quota exhausted — %s — "
                                    "top up the ElevenLabs workspace.",
                                    body[:200],
                                )
                            else:
                                logger.error(
                                    "[TTS:EL] HTTP 401 — %s — check "
                                    "ELEVENLABS_API_KEY", body[:200])
                            return
                        if response.status_code == 429:
                            body = await response.aread()
                            logger.warning(
                                "[TTS:EL] HTTP 429 (attempt %d/%d) — %s",
                                attempt + 1, max_retries, body[:150],
                            )
                            if yielded_any:
                                self._stream_truncated = True
                                return
                            await asyncio.sleep(0.3 * (attempt + 1))
                            continue
                        if response.status_code != 200:
                            body = await response.aread()
                            logger.error(
                                "[TTS:EL] HTTP %d — %s",
                                response.status_code, body[:500],
                            )
                            return

                        logger.info(
                            "[TTS:EL] Streaming started for %r (%s, %s, %s, "
                            "speed=%.2f)", text[:50], self._model,
                            self._speaker, lang_short, speed)

                        first_chunk = True
                        async for raw_chunk in response.aiter_bytes(CHUNK_READ_SIZE):
                            if self._cancelled:
                                return
                            if first_chunk:
                                first_chunk = False
                                self._check_stream_rate(raw_chunk)
                            yielded_any = True
                            yield TTSChunk(
                                audio=raw_chunk,
                                text_segment=text,
                                is_last=False,
                            )

                        # Remember this part's request id so the NEXT part of
                        # the same utterance continues its prosody (max 3).
                        rid = response.headers.get("request-id")
                        if rid:
                            self._req_ids.append(rid)

                        logger.info("[TTS:EL] Stream complete")
                        return

            except httpx.TimeoutException:
                logger.warning("[TTS:EL] Stream timeout (attempt %d/%d) for %r",
                               attempt + 1, max_retries, text[:50])
                self._reset_client()
                if yielded_any:
                    logger.error("[TTS:EL] Mid-stream stall after audio was "
                                 "delivered — dropping remainder of %r to avoid "
                                 "double-speak", text[:50])
                    self._stream_truncated = True
                    return
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError) as exc:
                logger.warning(
                    "[TTS:EL] Stale/dropped connection (attempt %d/%d): %s — "
                    "resetting client", attempt + 1, max_retries, exc)
                self._reset_client()
                if yielded_any:
                    logger.error("[TTS:EL] Connection dropped after audio was "
                                 "delivered — dropping remainder of %r to avoid "
                                 "double-speak", text[:50])
                    self._stream_truncated = True
                    return
            except Exception:
                logger.exception("[TTS:EL] Stream error (attempt %d/%d)",
                                 attempt + 1, max_retries)
                break

            if attempt < max_retries - 1:
                await asyncio.sleep(0.2)

    async def close(self) -> None:
        global _shared_client
        if EL_HTTP_SHARED:
            client, _shared_client = _shared_client, None
        else:
            client, self._client = self._client, None
        if client:
            await client.aclose()
            logger.info("[TTS:EL] Client closed")
