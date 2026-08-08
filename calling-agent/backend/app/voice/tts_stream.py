"""
Streaming Text-to-Speech abstraction.

Simulates chunk-by-chunk audio delivery for low-latency playback.
The real implementation would connect to Cartesia (or similar) and
yield PCM chunks as they arrive over the wire.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

logger = logging.getLogger(__name__)


from ..audio_transport.base import AudioSink

@dataclass
class TTSChunk:
    """A single chunk of synthesised audio."""
    audio: bytes
    text_segment: str
    is_last: bool = False


class StreamingTTS(ABC):
    """Async streaming TTS interface with interruption support."""

    def __init__(self, audio_sink: AudioSink | None = None) -> None:
        self._playing = False
        self._cancelled = False
        self._sink = audio_sink

    @property
    def is_playing(self) -> bool:
        return self._playing

    def set_sink(self, sink: AudioSink) -> None:
        self._sink = sink

    @abstractmethod
    async def speak(self, text: str) -> None:
        """Stream-synthesise and play `text`. Must be cancellable via stop()."""
        ...

    async def stop(self) -> None:
        """Interrupt playback immediately."""
        self._cancelled = True
        self._stop_seen = True
        self._playing = False
        logger.info("[TTS] Playback interrupted")

    # True once any audio for the current utterance reached the sink.
    # HybridTTS reads this after speak()/speak_cached() to detect a dead-air
    # provider failure (zero chunks) and fail over. Deliberately NOT cleared
    # by _reset(): speak() ends its finally with _reset(), so a per-reset
    # flag is wiped before HybridTTS can read it (live false-failover +
    # double greeting, 2026-08-07). Cleared only at utterance START.
    _produced_audio = False
    # True once stop() fired for the current utterance. Same survival rule
    # as _produced_audio — _cancelled is gone by the time the failover check
    # runs, and a barged-in zero-chunk utterance must NOT be re-spoken.
    _stop_seen = False

    def _reset(self) -> None:
        self._cancelled = False
        self._playing = False


class MockStreamingTTS(StreamingTTS):
    """
    Simulates streaming TTS by splitting text into word-groups and
    "playing" each chunk with a small async delay.

    In production, replace with a Cartesia/ElevenLabs client that
    yields real PCM chunks over a streaming connection.
    """

    def __init__(self, chunk_size_words: int = 3, chunk_delay_ms: int = 120) -> None:
        super().__init__()
        self._chunk_size = chunk_size_words
        self._chunk_delay = chunk_delay_ms / 1000

    async def speak(self, text: str) -> None:
        self._reset()
        self._playing = True
        words = text.split()
        chunks = [
            words[i : i + self._chunk_size]
            for i in range(0, len(words), self._chunk_size)
        ]

        logger.info("[TTS] Starting playback: %r (%d chunks)", text, len(chunks))

        for idx, word_group in enumerate(chunks):
            if self._cancelled:
                logger.info("[TTS] Cancelled mid-stream at chunk %d/%d", idx, len(chunks))
                break

            segment = " ".join(word_group)
            is_last = idx == len(chunks) - 1
            logger.info("[TTS] Playing chunk %d/%d: %r", idx + 1, len(chunks), segment)

            # Simulate network + decode latency
            await asyncio.sleep(self._chunk_delay)

            if self._cancelled:
                break

        self._playing = False
        if not self._cancelled:
            logger.info("[TTS] Playback complete")
        self._reset()
