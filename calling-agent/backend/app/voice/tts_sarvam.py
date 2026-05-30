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
import threading

import httpx
import numpy as np
import sounddevice as sd

from .tts_stream import StreamingTTS, TTSChunk
from ..audio_transport.base import AudioSink
from ..audio_transport.local_speaker_sink import LocalSpeakerSink

logger = logging.getLogger(__name__)

SARVAM_TTS_STREAM_URL = "https://api.sarvam.ai/text-to-speech/stream"
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
        self._api_key = api_key or os.environ.get("SARVAMAI_API_KEY", "")
        self._model = model
        self._speaker = speaker
        self._sample_rate = sample_rate
        self._pace = pace
        self._client: httpx.AsyncClient | None = None
        self._language = "en"
        self._output_device = output_device

        if not self._api_key:
            logger.warning("[TTS:Sarvam] SARVAMAI_API_KEY not set — calls will fail")

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

        try:
            async for chunk in self._stream_audio(text):
                if self._cancelled:
                    logger.info("[TTS:Sarvam] Cancelled mid-stream")
                    break
                
                await self._sink.write_frame(chunk.audio)
                chunk_count += 1
                if chunk_count == 1:
                    logger.info("[TTS:Sarvam] First chunk sent to sink")

        except Exception:
            logger.exception("[TTS:Sarvam] speak() failed")
        finally:
            # We don't stop the sink here because it might be shared or need to finish playing
            self._playing = False
            if hasattr(self._sink, 'flush') and not self._cancelled:
                await self._sink.flush()
            if not self._cancelled:
                logger.info("[TTS:Sarvam] Playback complete (%d chunks)", chunk_count)
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

        max_retries = 2
        for attempt in range(max_retries):
            if self._cancelled:
                return

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
