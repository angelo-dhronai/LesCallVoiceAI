"""
Sarvam saaras:v3 TRUE streaming STT provider (ENABLE_STREAMING_STT).

The batch architecture serializes STT after the endpoint: no audio reaches
Sarvam until the caller has been silent for HARD_FLUSH_SILENCE_FRAMES, then
a complete WAV is POSTed and the turn waits out the full STT round-trip.
This provider instead FEEDS audio to Sarvam's streaming WebSocket while the
caller is still speaking, so transcription runs concurrently with speech
and the transcript is (near-)ready the moment our endpoint fires.

What Sarvam streaming actually provides (verified against docs + the
installed sarvamai SDK): finalized SEGMENT transcripts, emitted when
Sarvam's server-side VAD closes a segment or when we send a flush signal.
There are NO word-level partials — so local endpointing stays silence-based
and the win is the removed post-endpoint STT wall time, not earlier
endpointing. Segment finals that arrive mid-turn are exposed via
``turn_text`` so the TurnManager can see real words instead of proxy
tokens.

Integration contract (see pipeline.py / conversation_call_bridge.py):
- The bridge installs this as STTRouter's *primary* when
  ENABLE_STREAMING_STT=true, so ALL router logic (validation, derived
  confidence, script-retry, Whisper fallback) applies unchanged.
- The pipeline calls ``feed_nowait(frame)`` for every speech frame it
  appends to its local buffer (the local buffer is kept as-is — it is the
  rescue payload and the audit recording source).
- ``transcribe(audio_bytes, ...)`` does not upload ``audio_bytes`` on the
  happy path: it flushes the stream and collects the segments for this
  turn. On ANY stream failure it falls back to a plain HTTP SarvamSTT
  batch call with the passed audio — same behavior as today, self-healing
  to batch for the remainder of the call.
- ``reset_turn()`` discards audio that was fed but whose turn was dropped
  (too-short discard, barge-in queue flush) via a generation counter, so a
  stale segment can never leak into the next turn's transcript.

Wire protocol (from sarvamai SDK source, same schema the June client used
successfully): JSON ``{"audio": {"data": <b64 WAV>, "sample_rate": N,
"encoding": "audio/wav"}}`` messages, a flush-signal message, and
``data`` / ``events`` / ``error`` responses. The SDK's AudioData model
pins encoding to audio/wav, so incremental feeding means one small
WAV-wrapped chunk per message — Sarvam parses each message's WAV and
appends the PCM server-side.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time

from .base import STTProvider, STTResult, pcm_to_wav
from .sarvam import SarvamSTT

logger = logging.getLogger(__name__)

# One audio message per ~320ms keeps message overhead negligible (~3/s)
# while the server still sees audio well before the endpoint fires.
_CHUNK_MS = int(os.environ.get("STT_STREAM_CHUNK_MS", "320"))

# How long transcribe() waits after the flush signal for the final segment.
# The batch path's STT round-trip this replaces was ~500-900ms.
_FLUSH_TIMEOUT_S = float(os.environ.get("STT_STREAM_FLUSH_TIMEOUT_S", "2.0"))

# After the first post-flush segment lands, wait briefly for stragglers
# (Sarvam may split one turn into several segments).
_SEGMENT_GRACE_S = 0.25

_LANG_CODE_MAP = {"en": "en-IN", "hi": "hi-IN", "gu": "gu-IN"}


class SarvamStreamingSTT(STTProvider):
    """Streaming saaras:v3 provider — feed during speech, flush at endpoint."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "saaras:v3",
        sample_rate: int = 16_000,
    ) -> None:
        self._api_key = api_key or os.environ.get("SARVAM_API_KEY", "")
        self._model = model
        self._sample_rate = sample_rate
        self._chunk_bytes = int(sample_rate * 2 * _CHUNK_MS / 1000.0)

        self._client = None
        self._ctx = None
        self._ws = None
        self._send_lock: asyncio.Lock = asyncio.Lock()
        self._send_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=64)
        self._sender_task: asyncio.Task | None = None
        self._recv_task: asyncio.Task | None = None

        self._pending = bytearray()
        # (generation, text) — reset_turn() bumps the generation so dropped
        # turns can never contribute text to a later one.
        self._segments: list[tuple[int, str]] = []
        self._gen = 0
        self._data_seq = 0
        self._data_event = asyncio.Event()
        self._detected_language: str | None = None
        self._broken = False
        self._drop_logged = False
        # Leftover-audio guard: segments that arrive when NO new audio has
        # been fed since the last finalize/reset are residue of an already-
        # consumed turn still draining server-side — they belong to no turn.
        self._fed_since_finalize = False

        # Rescue path: plain HTTP batch STT with the SAME semantics as the
        # production default. Any stream failure downgrades to this for the
        # rest of the call — never a dead turn.
        self._rescue = SarvamSTT(api_key=self._api_key)

    # ------------------------------------------------------------------
    # Feeding (called from the pipeline's VAD loop — must never block)
    # ------------------------------------------------------------------

    def feed_nowait(self, pcm: bytes) -> None:
        if self._broken or not pcm:
            return
        self._fed_since_finalize = True
        self._pending.extend(pcm)
        if len(self._pending) < self._chunk_bytes:
            return
        chunk = bytes(self._pending)
        self._pending.clear()
        try:
            self._send_q.put_nowait(chunk)
        except asyncio.QueueFull:
            if not self._drop_logged:
                self._drop_logged = True
                logger.warning(
                    "[STT:Stream] send queue full — dropping audio chunks "
                    "(stream will still flush; rescue batch path covers "
                    "the turn if the transcript comes back short)")
        self._ensure_tasks()

    @property
    def turn_text(self) -> str:
        """Segments finalized so far for the CURRENT turn (real words for
        the TurnManager, in place of its proxy tokens)."""
        return " ".join(t for g, t in self._segments if g == self._gen and t)

    def reset_turn(self) -> None:
        """The pipeline dropped the audio it just fed (too-short segment,
        barge-in flush): invalidate anything the server may still emit for
        it. Fire-and-forget flush purges the server-side buffer."""
        self._gen += 1
        self._pending.clear()
        self._fed_since_finalize = False
        self._segments = [s for s in self._segments if s[0] >= self._gen]
        if self._ws is not None and not self._broken:
            try:
                asyncio.get_running_loop().create_task(self._send_flush())
            except RuntimeError:
                pass

    # ------------------------------------------------------------------
    # STTProvider API (called by STTRouter as the primary provider)
    # ------------------------------------------------------------------

    async def transcribe(
        self,
        audio_bytes: bytes,
        sample_rate: int = 16_000,
        language: str = "en",
    ) -> STTResult:
        if self._broken:
            return await self._rescue.transcribe(audio_bytes, sample_rate, language)
        try:
            text, detected = await self._finalize()
            return STTResult(text=text, detected_language=detected)
        except Exception as exc:
            logger.warning(
                "[STT:Stream] finalize failed (%r) — downgrading to HTTP "
                "batch STT for the remainder of the call", exc)
            self._mark_broken()
            return await self._rescue.transcribe(audio_bytes, sample_rate, language)

    async def warmup(self) -> None:
        """Open the WS before the first turn so turn 1 doesn't pay the
        connect cost. Failure is non-fatal — first feed retries."""
        try:
            await asyncio.wait_for(self._ensure_connected(), timeout=5.0)
            logger.info("[STT:Stream] Warmup complete — WS open")
        except Exception as exc:
            logger.debug("[STT:Stream] Warmup skipped: %r", exc)

    async def close(self) -> None:
        for task in (self._sender_task, self._recv_task):
            if task is not None:
                task.cancel()
        self._sender_task = self._recv_task = None
        if self._ctx is not None:
            try:
                await self._ctx.__aexit__(None, None, None)
            except Exception:
                pass
        self._ctx = self._ws = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _mark_broken(self) -> None:
        if not self._broken:
            self._broken = True
            logger.warning("[STT:Stream] stream marked broken — batch rescue active")
        for task in (self._sender_task, self._recv_task):
            if task is not None:
                task.cancel()
        self._sender_task = self._recv_task = None

    def _ensure_tasks(self) -> None:
        if self._sender_task is None or self._sender_task.done():
            self._sender_task = asyncio.get_running_loop().create_task(
                self._sender_loop())

    async def _ensure_connected(self):
        if self._ws is not None:
            return self._ws
        if self._client is None:
            from sarvamai import AsyncSarvamAI
            self._client = AsyncSarvamAI(api_subscription_key=self._api_key)
        logger.info("[STT:Stream] Opening WS model=%s lang=unknown", self._model)
        # Single "unknown" connection for the whole call: saaras detects the
        # language per segment and reports it in data.language_code, which
        # the router/pipeline already use for language locking. Reconnecting
        # on lock would risk losing buffered server-side audio mid-turn.
        self._ctx = self._client.speech_to_text_streaming.connect(
            language_code="unknown",
            model=self._model,
            mode="transcribe",
            flush_signal="true",
            high_vad_sensitivity="true",
            vad_signals="true",
        )
        self._ws = await self._ctx.__aenter__()
        if self._recv_task is None or self._recv_task.done():
            self._recv_task = asyncio.get_running_loop().create_task(
                self._recv_loop())
        return self._ws

    async def _sender_loop(self) -> None:
        try:
            while True:
                chunk = await self._send_q.get()
                ws = await self._ensure_connected()
                b64 = base64.b64encode(
                    pcm_to_wav(chunk, sample_rate=self._sample_rate)).decode()
                async with self._send_lock:
                    await ws.transcribe(
                        audio=b64, encoding="audio/wav",
                        sample_rate=self._sample_rate)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[STT:Stream] sender failed: %r", exc)
            self._mark_broken()

    async def _recv_loop(self) -> None:
        try:
            while True:
                msg = await self._ws.recv()
                mtype = getattr(msg, "type", None)
                if mtype == "data":
                    data = getattr(msg, "data", None)
                    text = (getattr(data, "transcript", "") or "").strip()
                    detected = getattr(data, "language_code", None)
                    if detected:
                        self._detected_language = detected
                    if not self._fed_since_finalize:
                        # Residue of an already-finalized turn draining out
                        # of the server buffer between turns — discard.
                        logger.info(
                            "[STT:Stream] dropping stale inter-turn "
                            "segment %r", text)
                        continue
                    self._segments.append((self._gen, text))
                    self._data_seq += 1
                    self._data_event.set()
                    logger.info(
                        "[STT:Stream] segment #%d gen=%d %r (lang=%s)",
                        self._data_seq, self._gen, text, detected)
                elif mtype == "error":
                    logger.warning("[STT:Stream] server error: %s",
                                   getattr(msg, "data", None))
                    self._mark_broken()
                    return
                # "events" (START_SPEECH/END_SPEECH) are informational
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[STT:Stream] receiver failed: %r", exc)
            self._mark_broken()

    async def _send_flush(self) -> None:
        ws = await self._ensure_connected()
        async with self._send_lock:
            await ws.flush()

    async def _wait_for_seq(self, target: int) -> None:
        while self._data_seq < target:
            self._data_event.clear()
            await self._data_event.wait()

    async def _finalize(self) -> tuple[str, str | None]:
        """Flush the stream and collect this turn's segments."""
        t0 = time.monotonic()
        await self._ensure_connected()
        # Push any sub-chunk remainder so the flush covers ALL fed audio.
        if self._pending:
            tail = bytes(self._pending)
            self._pending.clear()
            self._send_q.put_nowait(tail)
        self._ensure_tasks()
        # Let the sender drain before flushing (bounded — the queue holds
        # at most ~64 chunks and each send is a small WS frame).
        drain_deadline = time.monotonic() + 0.5
        while not self._send_q.empty() and time.monotonic() < drain_deadline:
            await asyncio.sleep(0.01)

        gen = self._gen
        seq_at_flush = self._data_seq
        await self._send_flush()

        # Wait for the first post-flush segment; segments that finalized
        # mid-speech (server VAD) are already collected.
        try:
            await asyncio.wait_for(
                self._wait_for_seq(seq_at_flush + 1),
                timeout=_FLUSH_TIMEOUT_S)
            # Straggler grace: keep absorbing while segments keep landing.
            last_seq = self._data_seq
            while True:
                await asyncio.sleep(_SEGMENT_GRACE_S)
                if self._data_seq == last_seq:
                    break
                last_seq = self._data_seq
        except asyncio.TimeoutError:
            if not any(g == gen and t for g, t in self._segments):
                logger.info(
                    "[STT:Stream] flush produced no segment in %.1fs "
                    "(no speech server-side)", _FLUSH_TIMEOUT_S)

        parts = [t for g, t in self._segments if g == gen and t]
        # END the generation: a segment that finalizes server-side after the
        # grace window belongs to no turn — without the bump it stayed
        # tagged with the current gen and leaked into the NEXT turn's
        # transcript (observed live: turn 2 began with turn 1's tail).
        self._gen = gen + 1
        self._segments = [s for s in self._segments if s[0] >= self._gen]
        self._fed_since_finalize = False
        text = " ".join(parts).strip()
        logger.info(
            "[STT:Stream] finalize %.0fms → %r (segments=%d)",
            (time.monotonic() - t0) * 1000.0, text, len(parts))
        return text, self._detected_language
