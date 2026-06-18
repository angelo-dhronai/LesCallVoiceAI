"""
stt_stream_worker.py — Phase 10/11A streaming-STT validation worker.

A SEPARATE process from socket_server.py (ESL controller) and
ws_audio_receiver.py (media receiver). Listens on a localhost TCP
port for framed PCM forwarded by ws_audio_receiver.py. Pushes each
chunk into a per-session asyncio queue. A pluggable streaming-STT
adapter consumes chunks from the queue and emits partial / final
transcript events. End-of-session PHASE-10 STREAMING STT SUMMARY
mirrors the quality bar of Phase 7's SESSION SUMMARY and Phase 9's
STREAM SUMMARY. When STT_PROVIDER=sarvam, an additional PHASE-11
SARVAM SUMMARY block is emitted with provider-specific counters.

Phase 11A additions (this file only — socket_server.py and
ws_audio_receiver.py are unchanged):
  * SarvamStreamingSTTAdapter: real WebSocket streaming STT
    against wss://api.sarvam.ai/speech-to-text/ws. Owns its own
    connection lifecycle, runs a sender + receiver task pair, and
    reconnects with bounded exponential backoff on disconnect or
    provider error. STT failures NEVER propagate to the worker
    receive loop (and therefore never to the media receiver, the
    ESL controller, or the call).
  * Per-session transcript persistence to
    /tmp/stt_transcripts/<conn_id>.jsonl (append-only JSONL).
    Writes are offloaded to asyncio.to_thread so the realtime
    pipeline is never blocked on disk I/O. Disabled silently if
    the directory cannot be created.
  * Adapter selection via STT_PROVIDER env var
    (mock | sarvam, default mock).

Process boundary
----------------
ESL controller (socket_server.py)            tcp 127.0.0.1:9000  (FS -> us)
                v dispatches uuid_audio_stream
mod_audio_stream (FreeSWITCH)                ws  127.0.0.1:9001  (FS -> us)
                v binary L16 frames
ws_audio_receiver.py                         tcp 127.0.0.1:9002  (us -> us)
                v optional fan-out (env-gated)
stt_stream_worker.py                         this file

A crash in this worker cannot affect the receiver, the ESL
controller, the WAV recording, or the playback path. The forward
hook in the receiver is best-effort and disabled by default.

Wire format on the localhost TCP forward channel
------------------------------------------------
Frames are length-prefixed with a 1-byte type and 4-byte LE length:

    [1 type][4 LE length][payload(length)]

Types:
  0x01  HELLO   payload = utf-8 conn_id (and optional 'sr=NNNN' tail)
  0x02  AUDIO   payload = raw L16 PCM bytes (mono, sample_rate Hz)
  0x03  BYE     payload = empty (graceful end-of-session)

The worker treats unexpected EOF as an implicit BYE.

Adapter abstraction
-------------------
class BaseStreamingSTTAdapter:
    def __init__(self, sample_rate, on_partial, on_final, conn_id): ...
    async def feed(self, pcm_bytes: bytes) -> None: ...
    async def close(self) -> None: ...

`on_partial(text, meta)` and `on_final(text, meta)` are sync callables
provided by the worker. The adapter calls them whenever it produces
an incremental transcript event.

Phase 10 ships ONE adapter:
  * MockStreamingSTTAdapter — energy-VAD pseudo-STT. Emits a
    [STT PARTIAL] roughly every 500 ms while audio energy is above
    threshold, and a [STT FINAL] when the speech segment ends after
    700 ms of trailing silence. Validates the entire pipeline shape
    (chunk -> queue -> adapter -> partial/final -> log) without
    depending on an external STT service.

To swap in a real provider in a future phase, subclass
BaseStreamingSTTAdapter and register it via ADAPTERS below.

Phase 10 explicitly does NOT
----------------------------
* Make any LLM, TTS, or playback call.
* Hold a reply queue, response generator, or interruption logic.
* Modify socket_server.py, the dialplan, or the validated audio
  bytes flowing through ws_audio_receiver.py.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import struct
import time
from collections import deque
from typing import Any, Callable, Optional
from urllib.parse import urlencode

import websockets
import websockets.exceptions


HOST_DEFAULT = "127.0.0.1"
PORT_DEFAULT = 9002

FRAME_HELLO = 0x01
FRAME_AUDIO = 0x02
FRAME_BYE = 0x03

# Bound the per-session queue. At 50 frames/sec * 320 B = 16 kB/s, a
# depth of 200 = ~4 s of audio in flight. If a real STT lags more
# than that, we drop oldest frames so the receiver never blocks.
QUEUE_MAX_DEPTH = 200

# Periodic queue-depth sample (s). Logged into the per-session
# counters so STREAM SUMMARY can report max_queue_depth seen.
QUEUE_SAMPLE_INTERVAL_S = 0.5

# Phase-11A: per-session transcript persistence directory. Each
# call writes /tmp/stt_transcripts/<conn_id>.jsonl. Override with
# STT_TRANSCRIPT_DIR. Persistence is best-effort and entirely
# decoupled from the realtime pipeline.
TRANSCRIPT_DIR_DEFAULT = os.environ.get(
    "STT_TRANSCRIPT_DIR", "/tmp/stt_transcripts")

# Phase-12: orchestrator forward target. STT is the producer of FINAL
# transcripts; conversation_orchestrator.py consumes them. Hook is
# fire-and-forget over localhost TCP. Disable by setting
# ORCH_FORWARD_ENABLED=false (the default is on so Phase-12 works
# out of the box; rollback is a single env-var flip). Disconnects
# never propagate into the STT pipeline — see _OrchForwarder.
ORCH_FORWARD_ENABLED = (
    os.environ.get("ORCH_FORWARD_ENABLED", "true").lower()
    not in ("false", "0", "no"))
ORCH_FORWARD_HOST = os.environ.get("ORCH_FORWARD_HOST", "127.0.0.1")
ORCH_FORWARD_PORT = int(os.environ.get("ORCH_FORWARD_PORT", "9003"))
# Bound the per-session forward queue. Final transcripts are rare
# (one per spoken segment) so a small queue is plenty; if the orch
# is down we'd rather drop than grow memory.
ORCH_FORWARD_QUEUE_DEPTH = 64
# Reconnect cooldown after a failed connect. Localhost shouldn't
# need long backoffs; 1 s keeps logs from flooding when the orch is
# off entirely.
ORCH_FORWARD_RETRY_S = 1.0


logging.basicConfig(
    level=logging.INFO,
    format=("%(asctime)s.%(msecs)03d %(levelname)-5s "
            "[%(threadName)s] %(message)s"),
    datefmt="%H:%M:%S",
)
log = logging.getLogger("stt")


# ---------------------------------------------------------------------
# Adapter abstraction
# ---------------------------------------------------------------------

class BaseStreamingSTTAdapter:
    """Subclass and override feed() / close(). Use the on_partial /
    on_final callbacks to emit transcript events with metadata."""

    name = "base"

    def __init__(self, sample_rate: int, conn_id: str,
                 on_partial: Callable[[str, dict], None],
                 on_final: Callable[[str, dict], None]) -> None:
        self.sample_rate = sample_rate
        self.conn_id = conn_id
        self._on_partial = on_partial
        self._on_final = on_final

    async def feed(self, pcm_bytes: bytes) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        pass

    # Adapters MAY expose a `stats` dict for end-of-session
    # provider-specific reporting. Workers consult this attribute
    # opportunistically and do not require its presence.
    stats: dict = {}


# ---------------------------------------------------------------------
# Phase-11A: per-session transcript persistence
# ---------------------------------------------------------------------

class _TranscriptWriter:
    """Append-only JSONL transcript persistence.

    One file per session at <base_dir>/<conn_id>.jsonl. Each event is
    one JSON object on its own line. Writes are offloaded to a
    worker thread via asyncio.to_thread so the realtime adapter
    callbacks never block on disk I/O. Any failure (no permission,
    disk full, fd closed) flips the writer into a permanent
    `disabled` state for the remainder of the session — STT will
    keep running, only persistence stops."""

    def __init__(self, conn_id: str,
                 base_dir: str = TRANSCRIPT_DIR_DEFAULT) -> None:
        self.conn_id = conn_id
        self.base_dir = base_dir
        self.path = os.path.join(base_dir, f"{conn_id}.jsonl")
        self._fp = None
        self._disabled = False
        self.lines_written = 0
        self.errors = 0
        try:
            os.makedirs(base_dir, exist_ok=True)
            # line-buffered append; transcript volume is tiny so
            # per-line flush latency is irrelevant.
            self._fp = open(self.path, "a", buffering=1,
                            encoding="utf-8")
        except OSError as exc:
            self._disabled = True
            self.errors += 1
            log.warning("transcript writer setup failed conn_id=%s "
                        "path=%s err=%r — persistence disabled",
                        conn_id, self.path, exc)

    def _write_sync(self, line: str) -> None:
        if self._fp is None:
            return
        self._fp.write(line)

    async def write_event(self, event: dict) -> None:
        if self._disabled or self._fp is None:
            return
        try:
            line = json.dumps(event, ensure_ascii=False) + "\n"
            await asyncio.to_thread(self._write_sync, line)
            self.lines_written += 1
        except Exception as exc:
            if self.errors == 0:
                log.warning("transcript writer write failed "
                            "conn_id=%s err=%r — disabling for "
                            "session", self.conn_id, exc)
            self.errors += 1
            self._disabled = True

    def close(self) -> None:
        if self._fp is not None:
            try:
                self._fp.flush()
                self._fp.close()
            except Exception:
                pass
            self._fp = None


# ---------------------------------------------------------------------
# Phase-12: orchestrator forwarder (fire-and-forget, isolated)
# ---------------------------------------------------------------------

class _OrchForwarder:
    """Per-session forward channel to conversation_orchestrator.py.

    Wire format: 4-byte big-endian length + utf-8 JSON. Three event
    types are emitted: 'hello' (one per session at start), one
    'final_transcript' per FINAL emit from the STT adapter, 'bye'
    once on session close. Partials are NOT forwarded — Phase 12 is
    single-turn and the orchestrator triggers off finals only.

    Failure model
    -------------
    * Producer is realtime (the STT emit_final callback). It MUST
      NEVER block on this forwarder. Submit is via put_nowait on a
      bounded queue; on full queue we drop the oldest waiting event
      and count the drop in `dropped`.
    * A worker task drains the queue, opens a persistent TCP
      connection on demand, and writes frames. Any failure
      (connect timeout, EOF, broken pipe, JSON encode error) is
      caught, counted in `errors`, the connection is closed, and
      the worker waits ORCH_FORWARD_RETRY_S before retrying.
    * Disable end-to-end with ORCH_FORWARD_ENABLED=false.

    The forwarder DOES NOT propagate any error back to the STT
    pipeline. A crashed orchestrator therefore never affects STT
    timing, the WS receiver, the ESL controller, recording, or
    playback."""

    HELLO = "hello"
    FINAL = "final_transcript"
    BYE = "bye"

    def __init__(self, conn_id: str, host: str, port: int,
                 enabled: bool = True) -> None:
        self.conn_id = conn_id
        self.host = host
        self.port = port
        self.enabled = enabled
        self.queue: asyncio.Queue = asyncio.Queue(
            ORCH_FORWARD_QUEUE_DEPTH)
        self._writer: Optional[asyncio.StreamWriter] = None
        self._task: Optional[asyncio.Task] = None
        self._closed = False
        # Diagnostics.
        self.sent = 0
        self.dropped = 0
        self.errors = 0
        self.connects = 0
        self.disconnects = 0

    def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._worker())
        # Send a HELLO so the orchestrator can spin its session up
        # before the first final lands.
        self.submit({"type": self.HELLO, "conn_id": self.conn_id})

    def submit(self, evt: dict) -> None:
        """Realtime-safe. Never blocks. Drops oldest on full queue."""
        if not self.enabled or self._closed:
            return
        try:
            self.queue.put_nowait(evt)
        except asyncio.QueueFull:
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except asyncio.QueueEmpty:
                pass
            self.dropped += 1
            try:
                self.queue.put_nowait(evt)
            except asyncio.QueueFull:
                self.dropped += 1

    async def _connect(self) -> bool:
        try:
            coro = asyncio.open_connection(self.host, self.port)
            _r, w = await asyncio.wait_for(coro, timeout=2.0)
            self._writer = w
            self.connects += 1
            log.info("orch forwarder connected conn_id=%s -> %s:%d",
                     self.conn_id, self.host, self.port)
            return True
        except (OSError, asyncio.TimeoutError) as exc:
            self.errors += 1
            # Log at debug to avoid flooding when orch is intentionally
            # off; the per-session summary surfaces the counters.
            log.debug("orch forwarder connect failed conn_id=%s "
                      "err=%r", self.conn_id, exc)
            return False

    async def _send_one(self, evt: dict) -> bool:
        if self._writer is None:
            return False
        try:
            body = json.dumps(evt, ensure_ascii=False).encode("utf-8")
            frame = struct.pack(">I", len(body)) + body
            self._writer.write(frame)
            await asyncio.wait_for(self._writer.drain(), timeout=2.0)
            self.sent += 1
            return True
        except (OSError, asyncio.TimeoutError, ValueError) as exc:
            self.errors += 1
            log.debug("orch forwarder send failed conn_id=%s err=%r",
                      self.conn_id, exc)
            return False

    async def _close_writer(self) -> None:
        w = self._writer
        self._writer = None
        if w is None:
            return
        self.disconnects += 1
        try:
            w.close()
            await asyncio.wait_for(w.wait_closed(), timeout=1.0)
        except (OSError, asyncio.TimeoutError):
            pass

    async def _worker(self) -> None:
        try:
            while not self._closed:
                evt = await self.queue.get()
                if evt is None:
                    self.queue.task_done()
                    break
                # Connect lazily / reconnect on demand.
                if self._writer is None:
                    ok = await self._connect()
                    if not ok:
                        # Drop this event and back off so we don't
                        # hot-loop when orch is down.
                        self.dropped += 1
                        self.queue.task_done()
                        await asyncio.sleep(ORCH_FORWARD_RETRY_S)
                        continue
                ok = await self._send_one(evt)
                self.queue.task_done()
                if not ok:
                    await self._close_writer()
                    # Will reconnect on next iteration.
                    await asyncio.sleep(ORCH_FORWARD_RETRY_S)
        except asyncio.CancelledError:
            pass
        finally:
            await self._close_writer()

    async def close(self) -> None:
        if not self.enabled or self._closed:
            return
        self._closed = True
        # Best-effort BYE. Submit before flipping the closed flag is
        # not strictly necessary — we set _closed True after, then
        # push the BYE via the raw queue path bypassing the gate.
        try:
            self.queue.put_nowait({"type": self.BYE,
                                   "conn_id": self.conn_id})
        except asyncio.QueueFull:
            self.dropped += 1
        # Sentinel to terminate the worker after the BYE drains.
        try:
            self.queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except asyncio.TimeoutError:
                self._task.cancel()
                try:
                    await self._task
                except (asyncio.CancelledError, Exception):
                    pass


class MockStreamingSTTAdapter(BaseStreamingSTTAdapter):
    """Energy-VAD pseudo-STT.

    Walks the incoming PCM as int16 mono samples, computes a windowed
    RMS, and emits transcript events keyed off speech / silence
    transitions. The TEXT it produces is synthetic ("<segment 1
    partial t=0.500s rms=2841>"); the SHAPE matches what a real
    streaming STT adapter would produce — partials during speech,
    finals on segment end — so downstream code that consumes
    transcript events can be wired up and validated without an STT
    API key."""

    name = "mock"

    SPEECH_RMS_THRESHOLD = 600        # int16 RMS; ~−35 dBFS
    PARTIAL_INTERVAL_S = 0.500
    SILENCE_TAIL_S = 0.700

    def __init__(self, sample_rate: int, conn_id: str,
                 on_partial: Callable[[str, dict], None],
                 on_final: Callable[[str, dict], None]) -> None:
        super().__init__(sample_rate, conn_id, on_partial, on_final)
        self._sample_t = 0.0          # samples consumed -> seconds
        self._in_speech = False
        self._segment_idx = 0
        self._segment_start_t: Optional[float] = None
        self._last_speech_t: Optional[float] = None
        self._last_partial_t = 0.0
        self._segment_peak = 0
        self._segment_rms_sum = 0.0
        self._segment_window_count = 0

    @staticmethod
    def _rms_int16(buf: bytes) -> tuple[float, int]:
        if not buf:
            return 0.0, 0
        # Trim trailing odd byte if any (paranoia; mod_audio_stream
        # always sends even-length L16 frames).
        n = len(buf) // 2
        if n == 0:
            return 0.0, 0
        samples = struct.unpack("<" + "h" * n, buf[: n * 2])
        ssum = 0.0
        peak = 0
        for s in samples:
            a = -s if s < 0 else s
            if a > peak:
                peak = a
            ssum += s * s
        return (ssum / n) ** 0.5, peak

    async def feed(self, pcm_bytes: bytes) -> None:
        rms, peak = self._rms_int16(pcm_bytes)
        chunk_seconds = (len(pcm_bytes) // 2) / float(self.sample_rate
                                                       or 1)
        self._sample_t += chunk_seconds

        is_speech = rms > self.SPEECH_RMS_THRESHOLD
        if is_speech:
            if not self._in_speech:
                self._in_speech = True
                self._segment_idx += 1
                self._segment_start_t = self._sample_t
                self._segment_peak = peak
                self._segment_rms_sum = rms
                self._segment_window_count = 1
                self._last_partial_t = self._sample_t
            else:
                if peak > self._segment_peak:
                    self._segment_peak = peak
                self._segment_rms_sum += rms
                self._segment_window_count += 1
            self._last_speech_t = self._sample_t

            if (self._sample_t - self._last_partial_t
                    >= self.PARTIAL_INTERVAL_S):
                self._last_partial_t = self._sample_t
                avg_rms = (self._segment_rms_sum
                           / max(1, self._segment_window_count))
                seg_t = (self._sample_t
                         - (self._segment_start_t or 0.0))
                text = (f"<segment {self._segment_idx} partial "
                        f"t={seg_t:.3f}s rms={avg_rms:.0f} "
                        f"peak={self._segment_peak}>")
                meta = {
                    "segment": self._segment_idx,
                    "segment_t": seg_t,
                    "rms": avg_rms,
                    "peak": self._segment_peak,
                    "is_partial": True,
                }
                self._on_partial(text, meta)
        else:
            if (self._in_speech and self._last_speech_t is not None
                    and (self._sample_t - self._last_speech_t)
                    >= self.SILENCE_TAIL_S):
                seg_t = (self._last_speech_t
                         - (self._segment_start_t or 0.0))
                avg_rms = (self._segment_rms_sum
                           / max(1, self._segment_window_count))
                text = (f"<segment {self._segment_idx} final "
                        f"duration={seg_t:.3f}s "
                        f"avg_rms={avg_rms:.0f} "
                        f"peak={self._segment_peak}>")
                meta = {
                    "segment": self._segment_idx,
                    "duration": seg_t,
                    "rms": avg_rms,
                    "peak": self._segment_peak,
                    "is_partial": False,
                }
                self._on_final(text, meta)
                self._in_speech = False
                self._segment_start_t = None
                self._last_speech_t = None
                self._segment_peak = 0
                self._segment_rms_sum = 0.0
                self._segment_window_count = 0

    async def close(self) -> None:
        if self._in_speech and self._segment_start_t is not None:
            seg_t = self._sample_t - self._segment_start_t
            avg_rms = (self._segment_rms_sum
                       / max(1, self._segment_window_count))
            text = (f"<segment {self._segment_idx} final "
                    f"duration={seg_t:.3f}s "
                    f"avg_rms={avg_rms:.0f} "
                    f"peak={self._segment_peak} "
                    f"closed_on_session_end>")
            meta = {
                "segment": self._segment_idx,
                "duration": seg_t,
                "rms": avg_rms,
                "peak": self._segment_peak,
                "is_partial": False,
                "closed_on_session_end": True,
            }
            self._on_final(text, meta)


# ---------------------------------------------------------------------
# Phase-11A: SarvamStreamingSTTAdapter
# ---------------------------------------------------------------------

class SarvamStreamingSTTAdapter(BaseStreamingSTTAdapter):
    """Real streaming STT via Sarvam AI's WebSocket endpoint.

    Architecture
    ------------
        worker.feed(pcm)                 (this adapter, isolated)
              v
          _send_queue (drop-oldest, bounded)
              v                                  websockets client
          _sender_loop  -- json/audio frames --> wss://api.sarvam.ai
          _receiver_loop <-- json transcripts --
              v
          on_partial / on_final  (worker callbacks; sync)

    Failure isolation
    -----------------
    * Provider disconnect, rate-limit, error message, or crash:
      caught inside this adapter, logged into self.stats, and the
      connection is rebuilt with bounded exponential backoff. The
      worker's stt_loop is not affected; the receiver's WS loop is
      not affected; ESL / playback / recording are not affected.
    * If SARVAM_API_KEY is unset, the adapter logs once and drains
      the send queue silently — feed() never blocks, the call still
      runs, and the operator sees the misconfiguration in the
      Phase-11 summary block.

    Audio format
    ------------
    Sarvam's WebSocket STT supports PCM at 8000 or 16000 Hz natively
    (input_audio_codec=pcm_s16le, sample_rate query param). Our
    pipeline is mono L16 8kHz, so audio passes through unchanged.
    For any other source rate we resample to 16k inside this adapter
    using simple linear interpolation. Upstream (mod_audio_stream,
    ws_audio_receiver, FreeSWITCH codec) is never touched.

    Transcript shape
    ----------------
    Sarvam saaras:v3 emits one  {type:'data', data:{transcript:..}}
    message per VAD-detected utterance, bracketed by
    {signal_type:'START_SPEECH' | 'END_SPEECH'} when vad_signals=true.
    Each `data` message is treated as a FINAL transcript event.
    Sub-segment incremental partials are not emitted by this provider
    in the documented streaming protocol; if a future model revision
    starts emitting incrementals between START and END, they will
    surface through the same code path. The Phase-11 summary's
    partial counter therefore typically reads 0 for sarvam, which is
    the correct, honest reflection of what the provider sent.

    Endpoint reference
    ------------------
    URL:    wss://api.sarvam.ai/speech-to-text/ws
    Auth:   Api-Subscription-Key: <SARVAM_API_KEY>
    Audio:  {"audio": {"data": "<b64>", "sample_rate": "8000",
                       "encoding": "audio/wav"}}
    Flush:  {"type": "flush"}
    Recv:   {"type": "data",   "data": {transcript, request_id, ...}}
            {"type": "events", "data": {signal_type, ...}}
            {"type": "error",  "data": {error, code}}
    """

    name = "sarvam"

    SARVAM_WS_URL = "wss://api.sarvam.ai/speech-to-text/ws"
    DEFAULT_MODEL = "saaras:v3"
    DEFAULT_LANGUAGE = "unknown"
    DEFAULT_MODE = "transcribe"
    INPUT_AUDIO_CODEC = "pcm_s16le"

    SEND_QUEUE_MAX = 200          # ~4s of 8kHz mono L16 in flight
    INITIAL_BACKOFF_S = 0.5
    MAX_BACKOFF_S = 8.0
    CONNECT_TIMEOUT_S = 5.0
    SEND_FLUSH_TIMEOUT_S = 2.0
    BACKOFF_SLEEP_SLICE_S = 0.2   # interruptible sleep granularity

    def __init__(self, sample_rate: int, conn_id: str,
                 on_partial: Callable[[str, dict], None],
                 on_final: Callable[[str, dict], None]) -> None:
        super().__init__(sample_rate, conn_id, on_partial, on_final)
        self._api_key = os.environ.get("SARVAM_API_KEY", "").strip()
        self._model = os.environ.get(
            "SARVAM_MODEL", self.DEFAULT_MODEL)
        self._language = os.environ.get(
            "SARVAM_LANGUAGE", self.DEFAULT_LANGUAGE)
        self._mode = os.environ.get(
            "SARVAM_MODE", self.DEFAULT_MODE)
        self._high_vad = (
            os.environ.get("SARVAM_HIGH_VAD_SENSITIVITY",
                           "false").lower()
            in ("true", "1", "yes"))

        if sample_rate in (8000, 16000):
            self._provider_sample_rate = sample_rate
            self._needs_resample = False
        else:
            self._provider_sample_rate = 16000
            self._needs_resample = True

        self._send_queue: asyncio.Queue = asyncio.Queue(
            self.SEND_QUEUE_MAX)
        self._closed = False
        self._run_task: Optional[asyncio.Task] = None
        self._t0 = time.monotonic()
        self._segment_start_t: Optional[float] = None
        # Public stats dict consumed by log_phase11_summary().
        self.stats: dict = {
            "ws_connects": 0,
            "ws_reconnects": 0,
            "ws_connect_rtt_ms_first": None,
            "provider_errors": 0,
            "send_drops": 0,
            "bytes_sent_to_provider": 0,
            "messages_sent": 0,
            "messages_received": 0,
            "vad_start_events": 0,
            "vad_end_events": 0,
            "first_partial_latency_ms": None,
            "first_final_latency_ms": None,
            "rtt_samples": 0,
            "rtt_sum_ms": 0.0,
            "rtt_max_ms": 0.0,
            "provider_audio_duration_total_s": 0.0,
            "provider_processing_latency_total_s": 0.0,
            "last_error": None,
            "configured_model": self._model,
            "configured_language": self._language,
            "configured_mode": self._mode,
            "provider_sample_rate": self._provider_sample_rate,
            "resample_active": self._needs_resample,
            "api_key_present": bool(self._api_key),
        }

    @staticmethod
    def _resample_linear_to_16k(pcm: bytes, src_rate: int) -> bytes:
        """Tiny inline upsampler for the unusual case where the
        worker's sample_rate is not 8000/16000. The upstream pipeline
        runs at 8kHz, so this path is dormant in normal operation
        and exists only so the adapter is robust if a future phase
        renegotiates the audio fork rate."""
        if src_rate == 16000 or not pcm:
            return pcm
        n = len(pcm) // 2
        if n == 0:
            return pcm
        src = struct.unpack("<" + "h" * n, pcm[: n * 2])
        ratio = 16000.0 / float(src_rate)
        out_n = max(1, int(n * ratio))
        out = [0] * out_n
        for i in range(out_n):
            x = i / ratio
            i0 = int(x)
            i1 = i0 + 1 if i0 + 1 < n else i0
            frac = x - i0
            v = int(src[i0] * (1.0 - frac) + src[i1] * frac)
            if v > 32767:
                v = 32767
            elif v < -32768:
                v = -32768
            out[i] = v
        return struct.pack("<" + "h" * out_n, *out)

    async def feed(self, pcm_bytes: bytes) -> None:
        if self._closed or not pcm_bytes:
            return
        # Lazy-start the connection task on first feed so that the
        # adapter doesn't open a socket if the session never sends
        # audio.
        if self._run_task is None:
            self._run_task = asyncio.create_task(self._run())
        try:
            self._send_queue.put_nowait(pcm_bytes)
        except asyncio.QueueFull:
            # drop OLDEST so the worker's feed() never blocks
            try:
                self._send_queue.get_nowait()
                self._send_queue.task_done()
            except (asyncio.QueueEmpty, ValueError):
                pass
            self.stats["send_drops"] += 1
            try:
                self._send_queue.put_nowait(pcm_bytes)
            except asyncio.QueueFull:
                self.stats["send_drops"] += 1

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Wake the sender loop so it can flush + return.
        try:
            self._send_queue.put_nowait(None)
        except asyncio.QueueFull:
            try:
                self._send_queue.get_nowait()
                self._send_queue.task_done()
            except (asyncio.QueueEmpty, ValueError):
                pass
            try:
                self._send_queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        if self._run_task is not None:
            try:
                await asyncio.wait_for(
                    self._run_task,
                    timeout=self.SEND_FLUSH_TIMEOUT_S + 1.5)
            except (asyncio.TimeoutError, Exception):
                self._run_task.cancel()
                try:
                    await self._run_task
                except (asyncio.CancelledError, Exception):
                    pass

    def _build_url(self) -> str:
        params = {
            "model": self._model,
            "language-code": self._language,
            "mode": self._mode,
            "sample_rate": str(self._provider_sample_rate),
            "input_audio_codec": self.INPUT_AUDIO_CODEC,
            "vad_signals": "true",
        }
        if self._high_vad:
            params["high_vad_sensitivity"] = "true"
        return self.SARVAM_WS_URL + "?" + urlencode(params)

    async def _interruptible_sleep(self, total_s: float) -> None:
        """Sleep up to total_s in small slices, exiting early if
        close() was called. Avoids holding a zombie connection task
        in a long sleep when the session has already shut down."""
        slept = 0.0
        while slept < total_s and not self._closed:
            slice_s = min(self.BACKOFF_SLEEP_SLICE_S, total_s - slept)
            try:
                await asyncio.sleep(slice_s)
            except asyncio.CancelledError:
                return
            slept += slice_s

    async def _run(self) -> None:
        if not self._api_key:
            log.error("[sarvam] SARVAM_API_KEY env var is empty for "
                      "conn_id=%s; adapter cannot connect. Audio "
                      "will be drained from the send queue silently. "
                      "Call playback / recording / WS receiver are "
                      "unaffected.", self.conn_id)
            while not self._closed:
                try:
                    item = await asyncio.wait_for(
                        self._send_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                try:
                    if item is None:
                        return
                finally:
                    self._send_queue.task_done()
            return

        url = self._build_url()
        backoff = self.INITIAL_BACKOFF_S
        while not self._closed:
            try:
                await self._connect_once(url)
                if self._closed:
                    return
                # Server closed the socket cleanly mid-call.
                self.stats["ws_reconnects"] += 1
                log.warning("[sarvam] ws session ended for "
                            "conn_id=%s — reconnecting in %.2fs "
                            "(reconnects=%d)",
                            self.conn_id, backoff,
                            self.stats["ws_reconnects"])
            except Exception as exc:
                self.stats["provider_errors"] += 1
                self.stats["last_error"] = repr(exc)
                if self._closed:
                    return
                self.stats["ws_reconnects"] += 1
                log.error("[sarvam] connect/stream failed "
                          "conn_id=%s err=%r — reconnect in %.2fs",
                          self.conn_id, exc, backoff)
            await self._interruptible_sleep(backoff)
            backoff = min(backoff * 2.0, self.MAX_BACKOFF_S)

    async def _connect_once(self, url: str) -> None:
        connect_t0 = time.monotonic()
        ws = await asyncio.wait_for(
            websockets.connect(
                url,
                extra_headers=[
                    ("Api-Subscription-Key", self._api_key)],
                max_size=2**20,
                ping_interval=None,
            ),
            timeout=self.CONNECT_TIMEOUT_S)
        connect_ms = (time.monotonic() - connect_t0) * 1000.0
        self.stats["ws_connects"] += 1
        if self.stats["ws_connect_rtt_ms_first"] is None:
            self.stats["ws_connect_rtt_ms_first"] = connect_ms
        log.info("[sarvam] ws CONNECT conn_id=%s rtt=%.1fms "
                 "model=%s sr=%d lang=%s mode=%s vad=on",
                 self.conn_id, connect_ms, self._model,
                 self._provider_sample_rate, self._language,
                 self._mode)
        try:
            sender = asyncio.create_task(self._sender_loop(ws))
            receiver = asyncio.create_task(self._receiver_loop(ws))
            done, pending = await asyncio.wait(
                {sender, receiver},
                return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
            # Surface first non-cancellation exception so _run logs it.
            for t in done:
                exc = t.exception()
                if exc is not None and not isinstance(
                        exc, asyncio.CancelledError):
                    raise exc
        finally:
            try:
                await ws.close()
            except Exception:
                pass

    async def _sender_loop(self, ws) -> None:
        while True:
            item = await self._send_queue.get()
            try:
                if item is None:
                    # close signal: best-effort flush, then exit
                    try:
                        await asyncio.wait_for(
                            ws.send(json.dumps({"type": "flush"})),
                            timeout=self.SEND_FLUSH_TIMEOUT_S)
                    except Exception:
                        pass
                    return
                pcm = item
                if self._needs_resample:
                    pcm = self._resample_linear_to_16k(
                        pcm, self.sample_rate)
                payload = {
                    "audio": {
                        "data": base64.b64encode(pcm).decode(
                            "ascii"),
                        "sample_rate": str(
                            self._provider_sample_rate),
                        "encoding": "audio/wav",
                    }
                }
                await ws.send(json.dumps(payload))
                self.stats["messages_sent"] += 1
                self.stats["bytes_sent_to_provider"] += len(pcm)
            finally:
                self._send_queue.task_done()

    async def _receiver_loop(self, ws) -> None:
        async for raw in ws:
            self.stats["messages_received"] += 1
            if not isinstance(raw, str):
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if not isinstance(msg, dict):
                continue
            mtype = msg.get("type")
            if mtype == "data":
                self._handle_data(msg.get("data") or {})
            elif mtype == "events":
                self._handle_event(msg.get("data") or {})
            elif mtype == "error":
                self.stats["provider_errors"] += 1
                err = msg.get("data") or {}
                self.stats["last_error"] = (
                    f"{err.get('code')}: {err.get('error')}")
                log.warning("[sarvam] provider error conn_id=%s %s",
                            self.conn_id, self.stats["last_error"])
            else:
                log.debug("[sarvam] unknown msg type=%r conn_id=%s",
                          mtype, self.conn_id)

    def _handle_event(self, data: dict) -> None:
        sig = data.get("signal_type")
        if sig == "START_SPEECH":
            self._segment_start_t = time.monotonic()
            self.stats["vad_start_events"] += 1
        elif sig == "END_SPEECH":
            self.stats["vad_end_events"] += 1

    def _handle_data(self, data: dict) -> None:
        text = (data.get("transcript") or "").strip()
        if not text:
            return
        now = time.monotonic()
        if self._segment_start_t is not None:
            seg_latency_ms = (
                now - self._segment_start_t) * 1000.0
        else:
            seg_latency_ms = 0.0
        metrics = data.get("metrics") or {}
        try:
            self.stats["provider_audio_duration_total_s"] += float(
                metrics.get("audio_duration") or 0.0)
            self.stats["provider_processing_latency_total_s"] += (
                float(metrics.get("processing_latency") or 0.0))
            proc_lat_ms = float(
                metrics.get("processing_latency") or 0.0) * 1000.0
        except (TypeError, ValueError):
            proc_lat_ms = 0.0
        self.stats["rtt_samples"] += 1
        self.stats["rtt_sum_ms"] += seg_latency_ms
        if seg_latency_ms > self.stats["rtt_max_ms"]:
            self.stats["rtt_max_ms"] = seg_latency_ms
        if self.stats["first_final_latency_ms"] is None:
            self.stats["first_final_latency_ms"] = (
                (now - self._t0) * 1000.0)
        meta = {
            "provider": "sarvam",
            "request_id": data.get("request_id"),
            "language_code": data.get("language_code"),
            "language_probability": data.get("language_probability"),
            "latency_ms": seg_latency_ms,
            "provider_processing_latency_ms": proc_lat_ms,
            "is_partial": False,
        }
        # Reset segment marker so next utterance times fresh.
        self._segment_start_t = None
        try:
            self._on_final(text, meta)
        except Exception as exc:
            log.error("[sarvam] on_final callback raised "
                      "conn_id=%s err=%r — continuing",
                      self.conn_id, exc)


ADAPTERS: dict[str, type[BaseStreamingSTTAdapter]] = {
    "mock": MockStreamingSTTAdapter,
    "sarvam": SarvamStreamingSTTAdapter,
}


def build_adapter(name: str, sample_rate: int, conn_id: str,
                  on_partial: Callable[[str, dict], None],
                  on_final: Callable[[str, dict], None]
                  ) -> BaseStreamingSTTAdapter:
    cls = ADAPTERS.get(name.lower())
    if cls is None:
        raise ValueError(f"unknown STT_PROVIDER={name!r}; "
                         f"available={sorted(ADAPTERS)}")
    return cls(sample_rate, conn_id, on_partial, on_final)


# ---------------------------------------------------------------------
# Per-session state and worker pipeline
# ---------------------------------------------------------------------

class Session:
    __slots__ = (
        "conn_id", "peer", "sample_rate", "adapter", "queue",
        "t0", "cp", "counters", "latencies", "partials",
        "finals", "closed", "task", "transcript_writer",
        "orch_forwarder",
    )

    def __init__(self, conn_id: str, peer: str,
                 sample_rate: int) -> None:
        self.conn_id = conn_id
        self.peer = peer
        self.sample_rate = sample_rate
        self.adapter: Optional[BaseStreamingSTTAdapter] = None
        self.queue: asyncio.Queue = asyncio.Queue(QUEUE_MAX_DEPTH)
        self.t0 = time.monotonic()
        self.cp: dict = {"T0_session_open": 0.0}
        self.counters: dict = {
            "chunks_received": 0,
            "chunks_fed": 0,
            "chunks_dropped": 0,
            "bytes_received": 0,
            "partials_emitted": 0,
            "finals_emitted": 0,
            "max_queue_depth": 0,
            "reconnects": 0,
            "adapter_errors": 0,
        }
        self.latencies: deque = deque(maxlen=2048)
        self.partials: list = []
        self.finals: list = []
        self.closed = False
        self.task: Optional[asyncio.Task] = None
        # Phase-11A: per-session JSONL transcript writer. Created in
        # the connection handler so a setup failure can be logged
        # against the connection's peer/conn_id instead of here.
        self.transcript_writer: Optional[_TranscriptWriter] = None
        # Phase-12: per-session orchestrator forward channel. Lazy-
        # initialised in the connection handler so disable / enable
        # is config-only.
        self.orch_forwarder: Optional[_OrchForwarder] = None


def _fmt_t(cp: dict, key: str) -> str:
    v = cp.get(key)
    return f"{v:.3f}s" if v is not None else "—"


def _classify(session: Session) -> str:
    c = session.counters
    if c["chunks_received"] == 0:
        return ("CASE STT-A — connection opened but zero audio "
                "frames forwarded. Receiver is up but no audio "
                "reached the worker.")
    if c["adapter_errors"] >= 3:
        return (f"CASE STT-B — adapter raised {c['adapter_errors']} "
                f"errors. Pipeline plumbing OK, adapter "
                f"unhealthy.")
    if c["chunks_dropped"] > 0:
        return (f"CASE STT-C — pipeline ran but DROPPED "
                f"{c['chunks_dropped']} chunks (max queue depth "
                f"{c['max_queue_depth']}). STT slower than realtime.")
    if c["partials_emitted"] == 0 and c["finals_emitted"] == 0:
        return ("CASE STT-D — chunks delivered, adapter never "
                "emitted a transcript event. Check audio energy "
                "vs. adapter threshold.")
    return (f"CASE STT-E — STT PIPELINE HEALTHY  "
            f"chunks={c['chunks_received']} "
            f"partials={c['partials_emitted']} "
            f"finals={c['finals_emitted']} "
            f"drops={c['chunks_dropped']} "
            f"max_q={c['max_queue_depth']}")


def log_phase10_summary(session: Session) -> None:
    c = session.counters
    duration = ((session.cp.get("T2_session_close")
                 or (time.monotonic() - session.t0)))
    log.info("---------- PHASE-10 STREAMING STT SUMMARY -------")
    log.info("conn_id=%s  peer=%s  duration=%.3fs",
             session.conn_id, session.peer, duration)
    log.info("checkpoints:")
    for key in ("T0_session_open", "T1_first_audio",
                "T1_first_partial", "T1_first_final",
                "T1_last_audio", "T2_session_close"):
        log.info("    %-28s %s", key, _fmt_t(session.cp, key))
    log.info("counters: %s", c)
    if duration > 0.0:
        log.info("rates:")
        log.info("    %-28s %.1f", "chunks_received/sec",
                 c["chunks_received"] / duration)
        log.info("    %-28s %.0f", "bytes_received/sec",
                 c["bytes_received"] / duration)
    if session.latencies:
        avg = sum(session.latencies) / len(session.latencies)
        log.info("latency (chunk_recv -> adapter_fed):")
        log.info("    %-28s %.3fms", "avg", avg * 1000.0)
        log.info("    %-28s %.3fms", "max",
                 max(session.latencies) * 1000.0)
        log.info("    %-28s %.3fms", "min",
                 min(session.latencies) * 1000.0)
    log.info("transcript:")
    log.info("    partials_emitted          %d",
             c["partials_emitted"])
    log.info("    finals_emitted            %d",
             c["finals_emitted"])
    if session.finals:
        log.info("    final_transcripts (%d):", len(session.finals))
        for i, txt in enumerate(session.finals, 1):
            log.info("        [%d] %s", i, txt)
    # Phase-12: orchestrator forwarder counters. Logged inline so
    # PHASE-10 SUMMARY remains the single source of truth for STT-
    # side diagnostics. Failure of the forward channel never
    # affects the STT verdict above — these are isolated counters.
    fwd = session.orch_forwarder
    if fwd is not None:
        log.info("orch_forwarder:")
        log.info("    %-28s %s", "enabled", fwd.enabled)
        log.info("    %-28s %s:%d", "target", fwd.host, fwd.port)
        log.info("    %-28s %d", "events_sent", fwd.sent)
        log.info("    %-28s %d", "events_dropped", fwd.dropped)
        log.info("    %-28s %d", "errors", fwd.errors)
        log.info("    %-28s %d", "connects", fwd.connects)
        log.info("    %-28s %d", "disconnects", fwd.disconnects)
    log.info("VERDICT: %s", _classify(session))
    log.info("-------------------------------------------------")


def log_phase11_sarvam_summary(session: Session) -> None:
    """Provider-specific block for Sarvam streaming STT.

    Reads from session.adapter.stats. Emitted ONLY after the Phase-10
    summary so the existing diagnostics path is unchanged. A failure
    inside this function never short-circuits cleanup."""

    adapter = session.adapter
    stats = getattr(adapter, "stats", {}) or {}
    duration = ((session.cp.get("T2_session_close")
                 or (time.monotonic() - session.t0)))
    avg_rtt_ms = (stats["rtt_sum_ms"] / stats["rtt_samples"]
                  if stats.get("rtt_samples") else 0.0)
    avg_provider_proc_ms = 0.0
    if stats.get("rtt_samples"):
        avg_provider_proc_ms = (
            (stats.get("provider_processing_latency_total_s") or 0.0)
            / stats["rtt_samples"]) * 1000.0
    transcript_path = (session.transcript_writer.path
                       if session.transcript_writer is not None
                       else "(disabled)")
    transcript_lines = (session.transcript_writer.lines_written
                        if session.transcript_writer is not None
                        else 0)
    transcript_errors = (session.transcript_writer.errors
                         if session.transcript_writer is not None
                         else 0)
    queue_q = session.queue
    log.info("---------- PHASE-11 SARVAM SUMMARY --------------")
    log.info("conn_id=%s  peer=%s  duration=%.3fs",
             session.conn_id, session.peer, duration)
    log.info("provider config:")
    log.info("    %-28s %s", "model",
             stats.get("configured_model"))
    log.info("    %-28s %s", "language",
             stats.get("configured_language"))
    log.info("    %-28s %s", "mode",
             stats.get("configured_mode"))
    log.info("    %-28s %s", "provider_sample_rate",
             stats.get("provider_sample_rate"))
    log.info("    %-28s %s", "resample_active",
             stats.get("resample_active"))
    log.info("    %-28s %s", "api_key_present",
             stats.get("api_key_present"))
    log.info("connection:")
    log.info("    %-28s %d", "ws_connects",
             stats.get("ws_connects", 0))
    log.info("    %-28s %d", "ws_reconnects",
             stats.get("ws_reconnects", 0))
    log.info("    %-28s %s", "ws_connect_rtt_ms_first",
             (f"{stats['ws_connect_rtt_ms_first']:.1f}"
              if stats.get("ws_connect_rtt_ms_first") is not None
              else "—"))
    log.info("    %-28s %d", "provider_errors",
             stats.get("provider_errors", 0))
    log.info("    %-28s %s", "last_error",
             stats.get("last_error") or "—")
    log.info("traffic:")
    log.info("    %-28s %d", "messages_sent",
             stats.get("messages_sent", 0))
    log.info("    %-28s %d", "messages_received",
             stats.get("messages_received", 0))
    log.info("    %-28s %d", "bytes_sent_to_provider",
             stats.get("bytes_sent_to_provider", 0))
    log.info("    %-28s %d", "send_drops",
             stats.get("send_drops", 0))
    log.info("vad:")
    log.info("    %-28s %d", "vad_start_events",
             stats.get("vad_start_events", 0))
    log.info("    %-28s %d", "vad_end_events",
             stats.get("vad_end_events", 0))
    log.info("transcripts:")
    log.info("    %-28s %d", "partials_emitted",
             session.counters["partials_emitted"])
    log.info("    %-28s %d", "finals_emitted",
             session.counters["finals_emitted"])
    log.info("    %-28s %s ms", "first_partial_latency",
             (f"{stats['first_partial_latency_ms']:.1f}"
              if stats.get("first_partial_latency_ms") is not None
              else "—"))
    log.info("    %-28s %s ms", "first_final_latency",
             (f"{stats['first_final_latency_ms']:.1f}"
              if stats.get("first_final_latency_ms") is not None
              else "—"))
    log.info("    %-28s %.1f ms", "avg_segment_rtt",
             avg_rtt_ms)
    log.info("    %-28s %.1f ms", "max_segment_rtt",
             stats.get("rtt_max_ms") or 0.0)
    log.info("    %-28s %.1f ms",
             "avg_provider_processing", avg_provider_proc_ms)
    log.info("    %-28s %.3f s", "provider_audio_duration_total",
             stats.get("provider_audio_duration_total_s") or 0.0)
    log.info("queue:")
    log.info("    %-28s %d", "current_depth", queue_q.qsize())
    log.info("    %-28s %d", "max_depth_seen",
             session.counters["max_queue_depth"])
    log.info("    %-28s %d", "worker_chunks_dropped",
             session.counters["chunks_dropped"])
    log.info("persistence:")
    log.info("    %-28s %s", "transcript_file", transcript_path)
    log.info("    %-28s %d", "lines_written", transcript_lines)
    log.info("    %-28s %d", "writer_errors", transcript_errors)
    log.info("VERDICT: %s", _classify_sarvam(session, stats))
    log.info("-------------------------------------------------")


def _classify_sarvam(session: Session, stats: dict) -> str:
    if not stats.get("api_key_present"):
        return ("CASE STT-SARVAM-A — SARVAM_API_KEY env var was "
                "empty. Adapter could not connect; audio was "
                "drained silently. Set SARVAM_API_KEY and retry.")
    if stats.get("ws_connects", 0) == 0:
        return ("CASE STT-SARVAM-B — never reached a successful "
                "ws CONNECT. Check network egress to "
                "api.sarvam.ai:443 and credential validity.")
    if (session.counters["finals_emitted"] == 0
            and session.counters["partials_emitted"] == 0):
        if stats.get("vad_start_events", 0) == 0:
            return ("CASE STT-SARVAM-C — connected but provider "
                    "never reported START_SPEECH. Audio likely "
                    "below VAD threshold or codec mismatch. Try "
                    "SARVAM_HIGH_VAD_SENSITIVITY=true.")
        return ("CASE STT-SARVAM-D — VAD fired but no transcript "
                "data arrived. Provider-side issue; check "
                "provider_errors and last_error above.")
    if stats.get("send_drops", 0) > 0:
        return (f"CASE STT-SARVAM-E — STT alive but DROPPED "
                f"{stats['send_drops']} chunks at the adapter "
                f"send queue. Provider/socket slower than realtime.")
    if stats.get("ws_reconnects", 0) > 0:
        return (f"CASE STT-SARVAM-F — STT alive with "
                f"{stats['ws_reconnects']} reconnect(s). Pipeline "
                f"survived but stability of provider/network is "
                f"degraded; investigate last_error above.")
    return (f"CASE STT-SARVAM-G — SARVAM STT HEALTHY  "
            f"connects={stats.get('ws_connects', 0)} "
            f"finals={session.counters['finals_emitted']} "
            f"partials={session.counters['partials_emitted']} "
            f"errors={stats.get('provider_errors', 0)} "
            f"drops={stats.get('send_drops', 0)}")


async def _read_exactly(reader: asyncio.StreamReader, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = await reader.read(n - len(buf))
        if not chunk:
            raise asyncio.IncompleteReadError(buf, n)
        buf += chunk
    return buf


async def _read_frame(reader: asyncio.StreamReader
                      ) -> tuple[int, bytes]:
    hdr = await _read_exactly(reader, 5)
    ftype = hdr[0]
    length = struct.unpack("<I", hdr[1:5])[0]
    payload = await _read_exactly(reader, length) if length else b""
    return ftype, payload


async def stt_loop(session: Session, adapter_name: str) -> None:
    """Consumer side: pull chunks from queue, feed adapter, log."""

    def _provider_name() -> str:
        return (session.adapter.name
                if session.adapter is not None else adapter_name)

    def _persist(event_type: str, text: str, meta: dict) -> None:
        # Fire-and-forget JSONL persistence. Never blocks the
        # callback. The writer self-disables on first error.
        w = session.transcript_writer
        if w is None:
            return
        evt = {
            "timestamp": time.time(),
            "type": event_type,
            "provider": meta.get("provider") or _provider_name(),
            "text": text,
            "latency_ms": meta.get("latency_ms"),
            "session_t": time.monotonic() - session.t0,
            "meta": {k: v for k, v in meta.items()
                     if k not in ("provider", "latency_ms")},
        }
        try:
            asyncio.create_task(w.write_event(evt))
        except RuntimeError:
            # No running loop (shouldn't happen here, but harmless).
            pass

    def emit_partial(text: str, meta: dict) -> None:
        session.cp.setdefault(
            "T1_first_partial",
            time.monotonic() - session.t0)
        session.counters["partials_emitted"] += 1
        session.partials.append(text)
        log.info("[STT PARTIAL] conn_id=%s provider=%s seg=%s "
                 "t=%.3fs rms=%.0f lat=%sms text=%s",
                 session.conn_id, _provider_name(),
                 meta.get("segment"),
                 meta.get("segment_t", 0.0),
                 meta.get("rms", 0.0),
                 (f"{meta.get('latency_ms'):.1f}"
                  if meta.get("latency_ms") is not None else "—"),
                 text)
        _persist("partial", text, meta)

    def emit_final(text: str, meta: dict) -> None:
        session.cp.setdefault(
            "T1_first_final", time.monotonic() - session.t0)
        session.counters["finals_emitted"] += 1
        session.finals.append(text)
        log.info("[STT FINAL]   conn_id=%s provider=%s seg=%s "
                 "dur=%.3fs avg_rms=%.0f peak=%d lat=%sms text=%s",
                 session.conn_id, _provider_name(),
                 meta.get("segment"),
                 meta.get("duration", 0.0),
                 meta.get("rms", 0.0),
                 meta.get("peak", 0),
                 (f"{meta.get('latency_ms'):.1f}"
                  if meta.get("latency_ms") is not None else "—"),
                 text)
        _persist("final", text, meta)
        # Phase-12: forward final to conversation_orchestrator.
        # Fire-and-forget; submit is realtime-safe and never raises.
        fwd = session.orch_forwarder
        if fwd is not None:
            fwd.submit({
                "type": "final_transcript",
                "conn_id": session.conn_id,
                "timestamp": time.time(),
                "text": text,
                "language": meta.get("language") or "",
                "provider": meta.get("provider") or _provider_name(),
                "segment": meta.get("segment"),
                "session_t": time.monotonic() - session.t0,
            })

    adapter = build_adapter(adapter_name, session.sample_rate,
                            session.conn_id,
                            emit_partial, emit_final)
    session.adapter = adapter
    log.info("STT adapter=%s sr=%d conn_id=%s",
             adapter.name, session.sample_rate, session.conn_id)

    try:
        while True:
            item = await session.queue.get()
            if item is None:
                break
            recv_t, pcm = item
            t1 = time.monotonic()
            try:
                await adapter.feed(pcm)
            except Exception as exc:
                session.counters["adapter_errors"] += 1
                log.error("STT adapter feed crashed conn_id=%s "
                          "err=%r — continuing", session.conn_id,
                          exc)
            else:
                session.latencies.append(time.monotonic() - recv_t)
                session.counters["chunks_fed"] += 1
            session.queue.task_done()
    except asyncio.CancelledError:
        pass
    finally:
        try:
            await adapter.close()
        except Exception as exc:
            log.error("STT adapter close crashed conn_id=%s "
                      "err=%r", session.conn_id, exc)


async def _queue_depth_sampler(session: Session) -> None:
    try:
        while not session.closed:
            d = session.queue.qsize()
            if d > session.counters["max_queue_depth"]:
                session.counters["max_queue_depth"] = d
            await asyncio.sleep(QUEUE_SAMPLE_INTERVAL_S)
    except asyncio.CancelledError:
        pass


async def handle_forward_connection(reader: asyncio.StreamReader,
                                    writer: asyncio.StreamWriter,
                                    args) -> None:
    peer_tup = writer.get_extra_info("peername")
    peer = ("%s:%d" % peer_tup[:2]) if peer_tup else "?"
    session: Optional[Session] = None
    sampler_task: Optional[asyncio.Task] = None
    try:
        # First frame MUST be HELLO. We refuse any other.
        ftype, payload = await _read_frame(reader)
        if ftype != FRAME_HELLO:
            log.warning("forward: first frame type=%d "
                        "(expected HELLO=%d) from %s; closing",
                        ftype, FRAME_HELLO, peer)
            return
        meta = payload.decode("utf-8", "replace")
        # HELLO payload format: "conn_id=<id>;sr=<rate>"
        parts = dict(p.split("=", 1) for p in meta.split(";")
                     if "=" in p)
        conn_id = parts.get("conn_id", "unknown")
        sr = int(parts.get("sr", str(args.sample_rate)))
        log.info("=== STT FORWARD CONNECT peer=%s conn_id=%s sr=%d ===",
                 peer, conn_id, sr)

        session = Session(conn_id=conn_id, peer=peer, sample_rate=sr)
        # Phase-11A: open the per-session JSONL transcript writer
        # BEFORE the adapter is built so any emit during adapter
        # warm-up is captured. Failure here is non-fatal.
        try:
            session.transcript_writer = _TranscriptWriter(
                conn_id=conn_id, base_dir=args.transcript_dir)
            log.info("transcript persistence conn_id=%s -> %s",
                     conn_id, session.transcript_writer.path)
        except Exception as exc:
            log.warning("transcript writer init crashed conn_id=%s "
                        "err=%r — persistence disabled",
                        conn_id, exc)
            session.transcript_writer = None
        # Phase-12: spin up the orchestrator forwarder. Failure to
        # connect is non-fatal and silent here — the worker logs
        # individual connect failures at DEBUG, and the per-session
        # summary surfaces the dropped/error counters.
        try:
            session.orch_forwarder = _OrchForwarder(
                conn_id=conn_id,
                host=args.orch_host,
                port=args.orch_port,
                enabled=args.orch_forward_enabled)
            session.orch_forwarder.start()
            if args.orch_forward_enabled:
                log.info("orch forwarder started conn_id=%s -> "
                         "%s:%d", conn_id, args.orch_host,
                         args.orch_port)
            else:
                log.info("orch forwarder disabled conn_id=%s",
                         conn_id)
        except Exception as exc:
            log.warning("orch forwarder init crashed conn_id=%s "
                        "err=%r — forwarding disabled",
                        conn_id, exc)
            session.orch_forwarder = None
        session.task = asyncio.create_task(
            stt_loop(session, args.provider))
        sampler_task = asyncio.create_task(
            _queue_depth_sampler(session))

        while True:
            try:
                ftype, payload = await _read_frame(reader)
            except asyncio.IncompleteReadError:
                break
            if ftype == FRAME_AUDIO:
                now = time.monotonic() - session.t0
                if "T1_first_audio" not in session.cp:
                    session.cp["T1_first_audio"] = now
                session.cp["T1_last_audio"] = now
                session.counters["chunks_received"] += 1
                session.counters["bytes_received"] += len(payload)
                try:
                    session.queue.put_nowait(
                        (time.monotonic(), payload))
                except asyncio.QueueFull:
                    # Drop OLDEST so the producer is never blocked.
                    try:
                        session.queue.get_nowait()
                        session.queue.task_done()
                    except asyncio.QueueEmpty:
                        pass
                    session.counters["chunks_dropped"] += 1
                    try:
                        session.queue.put_nowait(
                            (time.monotonic(), payload))
                    except asyncio.QueueFull:
                        # Truly stuck; count and move on.
                        session.counters["chunks_dropped"] += 1
            elif ftype == FRAME_BYE:
                break
            elif ftype == FRAME_HELLO:
                # The receiver re-issued HELLO mid-stream — treat as
                # a forwarder reconnect (same TCP, new logical run).
                session.counters["reconnects"] += 1
                log.warning("STT FORWARD reconnect signaled "
                            "conn_id=%s reconnects=%d",
                            session.conn_id,
                            session.counters["reconnects"])
            else:
                log.warning("STT FORWARD unknown frame type=%d "
                            "len=%d", ftype, len(payload))
    except Exception as exc:
        log.error("STT FORWARD handler crashed peer=%s err=%r",
                  peer, exc)
    finally:
        if session is not None:
            session.closed = True
            session.cp["T2_session_close"] = (
                time.monotonic() - session.t0)
            # Drain queue so the consumer task can finish.
            try:
                session.queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
            if session.task is not None:
                try:
                    await asyncio.wait_for(session.task,
                                           timeout=2.0)
                except asyncio.TimeoutError:
                    log.warning("STT loop did not finish in 2s; "
                                "cancelling conn_id=%s",
                                session.conn_id)
                    session.task.cancel()
            if sampler_task is not None:
                sampler_task.cancel()
                try:
                    await sampler_task
                except asyncio.CancelledError:
                    pass
            try:
                log_phase10_summary(session)
            except Exception as exc:
                log.error("STREAMING STT SUMMARY render failed: %r",
                          exc)
            # Phase-11A: provider-specific summary if the adapter
            # exposed Sarvam stats. Wrapped so a render bug cannot
            # break the cleanup path.
            try:
                if (session.adapter is not None
                        and getattr(session.adapter, "name", "")
                        == "sarvam"):
                    log_phase11_sarvam_summary(session)
            except Exception as exc:
                log.error("PHASE-11 SARVAM SUMMARY render failed: %r",
                          exc)
            # Phase-12: close the orchestrator forwarder. Sends a
            # BYE so the orchestrator can finalise its per-call FSM
            # and emit ORCHESTRATOR SUMMARY. Bounded; any failure
            # here is swallowed.
            if session.orch_forwarder is not None:
                try:
                    await session.orch_forwarder.close()
                except Exception as exc:
                    log.error("orch forwarder close crashed "
                              "conn_id=%s err=%r",
                              session.conn_id, exc)
            # Close the transcript file last so end-of-call writes
            # from emit_final() have a chance to land.
            if session.transcript_writer is not None:
                try:
                    session.transcript_writer.close()
                except Exception:
                    pass
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def main_async(args) -> None:
    if not args.enabled:
        log.info("STT_ENABLED=false — worker exiting cleanly "
                 "without binding")
        return
    log.info("Phase-10/11A/12 stt_stream_worker listening on "
             "%s:%d  (provider=%s sample_rate=%dHz transcript_dir=%s "
             "sarvam_key=%s orch_forward=%s -> %s:%d)",
             args.host, args.port, args.provider, args.sample_rate,
             args.transcript_dir,
             "set" if os.environ.get("SARVAM_API_KEY") else "unset",
             "on" if args.orch_forward_enabled else "off",
             args.orch_host, args.orch_port)
    server = await asyncio.start_server(
        lambda r, w: handle_forward_connection(r, w, args),
        args.host, args.port)
    async with server:
        await server.serve_forever()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default=HOST_DEFAULT)
    p.add_argument("--port", type=int, default=PORT_DEFAULT)
    p.add_argument("--provider",
                   default=os.environ.get("STT_PROVIDER", "mock"))
    p.add_argument("--sample-rate", type=int,
                   default=int(os.environ.get("STT_SAMPLE_RATE",
                                              "8000")))
    p.add_argument("--transcript-dir",
                   default=TRANSCRIPT_DIR_DEFAULT,
                   help=("Directory for per-session JSONL "
                         "transcripts (Phase-11A). Created if "
                         "missing. Set STT_TRANSCRIPT_DIR to "
                         "override the default."))
    enabled_env = os.environ.get("STT_ENABLED", "true").lower()
    p.add_argument("--enabled",
                   default=(enabled_env not in ("false", "0", "no")),
                   type=lambda s: str(s).lower()
                   not in ("false", "0", "no"))
    # Phase-12: orchestrator forwarding. Disabled cleanly via env
    # ORCH_FORWARD_ENABLED=false (rollback path) without touching
    # the STT consumer code.
    p.add_argument("--orch-host", default=ORCH_FORWARD_HOST,
                   help=("Phase-12 orchestrator host. Override "
                         "with ORCH_FORWARD_HOST."))
    p.add_argument("--orch-port", type=int,
                   default=ORCH_FORWARD_PORT,
                   help=("Phase-12 orchestrator TCP port. Override "
                         "with ORCH_FORWARD_PORT."))
    p.add_argument("--orch-forward-enabled",
                   default=ORCH_FORWARD_ENABLED,
                   type=lambda s: str(s).lower()
                   not in ("false", "0", "no"),
                   help=("Phase-12 forward FINAL transcripts to "
                         "the orchestrator. Set "
                         "ORCH_FORWARD_ENABLED=false to disable."))
    args = p.parse_args()
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        log.info("shutdown requested")


if __name__ == "__main__":
    main()
