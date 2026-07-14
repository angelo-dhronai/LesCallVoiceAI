"""
Sarvam AI Bulbul streaming TTS via HTTP stream endpoint.

Uses the /text-to-speech/stream endpoint for low-latency chunked audio
delivery. Plays audio through the system speaker using sounddevice with
true streaming playback — chunks are written to the output as they arrive,
minimizing time-to-first-audio.
"""

from __future__ import annotations

import asyncio
import logging
import os
import queue as _queue
import re
import threading

import httpx
import numpy as np
import sounddevice as sd

from .tts_stream import StreamingTTS, TTSChunk
from .key_rotator import current_key
from ..audio_transport.base import AudioSink
from ..audio_transport.local_speaker_sink import LocalSpeakerSink

logger = logging.getLogger(__name__)

SARVAM_TTS_STREAM_URL = "https://api.sarvam.ai/text-to-speech/stream"
# Anything outside this set gets HTTP 400 from Sarvam on every utterance —
# the call connects but is silent. Guarded here as the last line of defense;
# agent configs are also validated at load time (agent_definition/schema.py).
VALID_MODELS = ("bulbul:v2", "bulbul:v3-beta", "bulbul:v3")
DEFAULT_MODEL = "bulbul:v3"
DEFAULT_SPEAKER = "shubh"
DEFAULT_SAMPLE_RATE = 16_000
STREAM_TIMEOUT = 10.0
CONNECT_TIMEOUT = 5.0
CHUNK_READ_SIZE = 4096

_LANG_CODE_MAP = {
    "en": "en-IN",
    "hi": "hi-IN",
    "gu": "gu-IN",
}

# Incremental-playback chunking. Long utterances are synthesized in parts and
# flushed to the sink part-by-part, so telephony playback of part 1 starts
# while part 2 is still being synthesized. Without this, TelephonySink holds
# the entire utterance until the full Sarvam stream completes — a 14s reply
# adds ~8s of dead air before the caller hears anything.
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?।])\s+")
_CLAUSE_BOUNDARY_RE = re.compile(r"(?<=[,;:])\s+")
_FIRST_CHUNK_MAX_CHARS = 90    # small first part → fastest speech onset
_CHUNK_MAX_CHARS = 180
# Matches the +0.1s inter-file pacing gap in conversation_call_bridge.py's
# _playback_loop; used when reporting total utterance duration for echo-mute.
_INTER_WAV_PACING_S = 0.1


def _split_for_streaming(text: str) -> list[str]:
    """Split TTS text into parts synthesized and flushed incrementally.

    Splits at sentence enders first; LLM replies are often comma-run-ons with
    no sentence-ending punctuation until the very end, so any "sentence"
    longer than the chunk cap is further split at clause punctuation. Pieces
    are then greedily packed: a small first part for fast onset, larger
    follow-ups so pacing gaps stay rare.
    """
    text = text.strip()
    if not text:
        return []

    pieces: list[str] = []
    for sentence in _SENTENCE_BOUNDARY_RE.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) > _CHUNK_MAX_CHARS:
            pieces.extend(
                p.strip() for p in _CLAUSE_BOUNDARY_RE.split(sentence) if p.strip()
            )
        else:
            pieces.append(sentence)

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        limit = _FIRST_CHUNK_MAX_CHARS if not chunks else _CHUNK_MAX_CHARS
        if current and len(current) + 1 + len(piece) > limit:
            chunks.append(current)
            current = piece
        else:
            current = f"{current} {piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


class SarvamTTS(StreamingTTS):
    """
    Sarvam AI Bulbul TTS — streams audio via HTTP and plays through an AudioSink.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        speaker: str = DEFAULT_SPEAKER,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        pace: float = 1.0,
        output_device: int | str | None = None,
        audio_sink: AudioSink | None = None,
    ) -> None:
        super().__init__(audio_sink=audio_sink or LocalSpeakerSink(
            sample_rate=sample_rate,
            output_device=output_device
        ))
        self._api_key = api_key or os.environ.get("SARVAM_API_KEY", "")
        if model not in VALID_MODELS:
            logger.warning("[TTS:Sarvam] invalid model %r — using %s "
                           "(valid: %s)", model, DEFAULT_MODEL,
                           ", ".join(VALID_MODELS))
            model = DEFAULT_MODEL
        self._model = model
        self._speaker = speaker
        self._sample_rate = sample_rate
        self._pace = pace
        self._client: httpx.AsyncClient | None = None
        self._language = "en"
        self._output_device = output_device

        if not self._api_key:
            logger.warning("[TTS:Sarvam] SARVAM_API_KEY not set — calls will fail")
        else:
            masked = self._api_key[:6] + "…" + self._api_key[-4:]
            logger.info("[TTS:Sarvam] API key loaded: %s", masked)

    def set_language(self, lang: str) -> None:
        self._language = lang

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            # keepalive_expiry=60: idle connections older than 60s are never
            # reused, so a call after a long pause always gets a fresh TLS
            # handshake instead of dying on a silently-dropped server socket.
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(STREAM_TIMEOUT, connect=CONNECT_TIMEOUT),
                limits=httpx.Limits(max_keepalive_connections=5, keepalive_expiry=60),
            )
        return self._client

    async def speak(self, text: str) -> None:
        self._reset()
        self._playing = True

        if not self._api_key:
            logger.warning("[TTS:Sarvam] No API key — skipping")
            self._playing = False
            return

        chunk_count = 0

        # Ensure sink is started
        await self._sink.start()

        parts = _split_for_streaming(text)
        if len(parts) > 1:
            logger.info(
                "[TTS:Sarvam] Utterance split into %d parts for incremental playback",
                len(parts),
            )
        total_duration = 0.0
        flushed_parts = 0

        try:
            for part in parts:
                if self._cancelled:
                    break
                async for chunk in self._stream_audio(part):
                    if self._cancelled:
                        logger.info("[TTS:Sarvam] Cancelled mid-stream")
                        break

                    await self._sink.write_frame(chunk.audio)
                    chunk_count += 1
                    if chunk_count == 1:
                        logger.info("[TTS:Sarvam] First chunk sent to sink")
                if self._cancelled:
                    break
                # Incremental flush: hand this part's audio to the transport
                # now so playback starts before later parts are synthesized.
                if hasattr(self._sink, 'flush'):
                    await self._sink.flush()
                    total_duration += getattr(self._sink, 'last_flush_duration', 0.0)
                    flushed_parts += 1

        except Exception:
            logger.exception("[TTS:Sarvam] speak() failed")
        finally:
            # We don't stop the sink here because it might be shared or need to finish playing
            self._playing = False
            if hasattr(self._sink, 'flush') and not self._cancelled:
                await self._sink.flush()
                # The pipeline reads last_flush_duration after speak() returns
                # to size its echo-mute window for the WHOLE utterance. With
                # incremental flushes it only holds the last part, so restore
                # the utterance total (plus the bridge's pacing gap per extra
                # WAV file).
                if flushed_parts > 1 and hasattr(self._sink, 'last_flush_duration'):
                    self._sink.last_flush_duration = (
                        total_duration + _INTER_WAV_PACING_S * (flushed_parts - 1)
                    )
            if not self._cancelled:
                logger.info(
                    "[TTS:Sarvam] Playback complete (%d chunks, %d parts)",
                    chunk_count, flushed_parts or 1,
                )
            self._reset()

    async def _stream_audio(self, text: str):
        lang_code = _LANG_CODE_MAP.get(self._language, "en-IN")

        payload = {
            "text": text,
            "target_language_code": lang_code,
            "speaker": self._speaker,
            "model": self._model,
            "pace": self._pace,
            "speech_sample_rate": self._sample_rate,
            "output_audio_codec": "linear16",
        }

        # Budget covers both connection retries AND hot-swapping through the
        # backup key pool when a key returns 402 (out of credits).
        max_retries = 8
        for attempt in range(max_retries):
            if self._cancelled:
                return

            # Always use the live key so a rotation (ours or the watchdog's)
            # is picked up on the very next attempt without a restart.
            self._api_key = current_key()
            client = self._get_client()
            try:
                async with client.stream(
                    "POST",
                    SARVAM_TTS_STREAM_URL,
                    headers={
                        "api-subscription-key": self._api_key,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                ) as response:
                    if response.status_code == 402:
                        body = await response.aread()
                        logger.error(
                            "[SARVAM:CREDITS] TTS HTTP 402 — %s — rotating key from pool.",
                            body[:200],
                        )
                        from .key_rotator import rotate as _rotate_key
                        new_key = _rotate_key()
                        if new_key:
                            self._api_key = new_key
                            self._client = None  # force fresh TLS with new key
                            logger.info(
                                "[TTS:Sarvam] Key rotated — retrying speak() with new key"
                            )
                            continue
                        logger.error(
                            "[TTS:Sarvam] No backup key available — cannot speak"
                        )
                        return
                    if response.status_code != 200:
                        body = await response.aread()
                        logger.error(
                            "[TTS:Sarvam] HTTP %d — %s",
                            response.status_code, body[:500],
                        )
                        return

                    logger.info("[TTS:Sarvam] Streaming started for %r (%s, %s)",
                                text[:50], lang_code, self._speaker)

                    async for raw_chunk in response.aiter_bytes(CHUNK_READ_SIZE):
                        if self._cancelled:
                            return
                        yield TTSChunk(
                            audio=raw_chunk,
                            text_segment=text,
                            is_last=False,
                        )

                    logger.info("[TTS:Sarvam] Stream complete")
                    return

            except httpx.TimeoutException:
                logger.warning("[TTS:Sarvam] Stream timeout (attempt %d/%d) for %r",
                               attempt + 1, max_retries, text[:50])
                # Force new client on next attempt — timeout may mean the
                # connection is in a bad state (partially sent request).
                self._client = None
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError) as exc:
                # Stale or dropped connection — always reset so the retry
                # opens a fresh TLS socket.  ConnectError was previously
                # swallowed by the generic handler below and caused no retry,
                # leading to silent failure (no audio / elongated first word
                # from a corrupted partial stream).
                logger.warning("[TTS:Sarvam] Stale/dropped connection (attempt %d/%d): %s — resetting client",
                               attempt + 1, max_retries, exc)
                self._client = None
            except Exception:
                logger.exception("[TTS:Sarvam] Stream error (attempt %d/%d)",
                                 attempt + 1, max_retries)
                break

            if attempt < max_retries - 1:
                await asyncio.sleep(0.2)

    async def stop(self) -> None:
        await super().stop()
        await self._sink.stop()

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.info("[TTS:Sarvam] Client closed")
