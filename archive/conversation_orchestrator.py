"""
conversation_orchestrator.py — Phase 12 single-turn AI conversation
====================================================================

A SEPARATE process from socket_server.py (ESL controller),
ws_audio_receiver.py (media receiver) and stt_stream_worker.py
(streaming STT). Listens on a localhost TCP port for FINAL transcript
events forwarded by stt_stream_worker.py. Per call it drives the
single-turn loop:

    final transcript
        v
    LLM adapter   (mock | openai)        bounded timeout
        v
    TTS adapter   (mock | sarvam)        bounded timeout
        v
    /tmp/ai_responses/<conn_id>/<ts>.wav
        v
    playback_request   (length-prefixed JSON)
        v
    socket_server.py  (Phase-12 P5 listener)
        v
    FreeSWITCH playback  (validated Phase-2 path)

Strict process boundary
-----------------------
A crash in this orchestrator MUST NOT affect the ESL controller, the
media receiver, the STT worker, the WAV recording, or the validated
playback path. The forward hook on the STT side and the playback
listener on the socket_server side are both fire-and-forget over
localhost TCP — disconnection of the orchestrator is logged but never
propagated.

Wire format on the inbound forward channel (STT -> orchestrator)
----------------------------------------------------------------
Length-prefixed JSON. 4-byte big-endian length, then utf-8 JSON:

    [4 BE length][payload(length)]

Accepted event shapes (all fields except `type` and `conn_id` are
optional; unknown fields are ignored):

    {"type": "hello",            "conn_id": "<id>"}
    {"type": "final_transcript", "conn_id": "<id>",
     "timestamp": <unix>, "text": "<utf-8>",
     "language": "<bcp47>", "provider": "<name>"}
    {"type": "bye",              "conn_id": "<id>"}

Wire format on the outbound playback channel (orchestrator -> ESL)
------------------------------------------------------------------
Identical 4-byte BE length + utf-8 JSON:

    {"type": "playback_request", "conn_id": "<id>",
     "wav_path": "/tmp/ai_responses/<id>/<ts>.wav"}

Per-call session state machine
------------------------------
    IDLE -> LISTENING -> PROCESSING_LLM -> GENERATING_TTS
        -> PLAYING_RESPONSE -> COMPLETED
                            \-> DISCONNECTED   (any state, on bye/EOF)

Single-turn this phase: while in PROCESSING_LLM / GENERATING_TTS /
PLAYING_RESPONSE, additional final transcripts are SUPPRESSED (counted
in `dropped_during_turn`). No interruption, no barge-in, no streaming
TTS, no memory, no RAG, no agents.

Adapter abstraction
-------------------
class BaseLLMAdapter:
    async def generate(text, language, conn_id) -> str
class BaseTTSAdapter:
    async def synthesize(text, language, conn_id, out_dir) -> str   # wav path

Implementations:
    MockLLMAdapter      — deterministic templates, immediate
    OpenAILLMAdapter    — chat.completions scaffold (urllib in thread)
    MockTTSAdapter      — 8kHz mono PCM tone WAV (length follows text)
    SarvamTTSAdapter    — scaffold; falls back to MockTTSAdapter if
                          SARVAM_API_KEY is unset or call fails

Environment
-----------
    ORCH_HOST                default 127.0.0.1
    ORCH_PORT                default 9003
    PLAYBACK_HOST            default 127.0.0.1
    PLAYBACK_PORT            default 9004
    LLM_PROVIDER             mock | openai     (default mock)
    OPENAI_API_KEY           required if LLM_PROVIDER=openai
    OPENAI_MODEL             default gpt-4o-mini
    LLM_TIMEOUT_S            default 8.0
    TTS_PROVIDER             mock | sarvam     (default mock)
    SARVAM_API_KEY           required if TTS_PROVIDER=sarvam
    SARVAM_TTS_VOICE         default 'meera'
    SARVAM_TTS_MODEL         default 'bulbul:v2'
    TTS_TIMEOUT_S            default 8.0
    AI_RESPONSE_DIR          default /tmp/ai_responses
    ORCH_TRANSCRIPT_DIR      default /tmp/orch_transcripts
    ORCH_DEBUG               1 to enable DEBUG logs (default INFO)

Phase 12 does NOT implement
---------------------------
    interruption, barge-in, streaming TTS playback, duplex audio,
    overlapping speech, conversational memory, vector DB, LangGraph,
    multi-agent routing, workflow engine.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import math
import os
import struct
import sys
import time
import wave
from collections import deque
from typing import Any, Callable, Optional


# ---------------------------------------------------------------------
# Constants and environment
# ---------------------------------------------------------------------

ORCH_HOST_DEFAULT = os.environ.get("ORCH_HOST", "127.0.0.1")
ORCH_PORT_DEFAULT = int(os.environ.get("ORCH_PORT", "9003"))

PLAYBACK_HOST_DEFAULT = os.environ.get("PLAYBACK_HOST", "127.0.0.1")
PLAYBACK_PORT_DEFAULT = int(os.environ.get("PLAYBACK_PORT", "9004"))

LLM_PROVIDER_DEFAULT = os.environ.get("LLM_PROVIDER", "mock").lower()
LLM_TIMEOUT_S = float(os.environ.get("LLM_TIMEOUT_S", "8.0"))
OPENAI_MODEL_DEFAULT = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

TTS_PROVIDER_DEFAULT = os.environ.get("TTS_PROVIDER", "mock").lower()
TTS_TIMEOUT_S = float(os.environ.get("TTS_TIMEOUT_S", "8.0"))
SARVAM_TTS_VOICE_DEFAULT = os.environ.get("SARVAM_TTS_VOICE", "meera")
SARVAM_TTS_MODEL_DEFAULT = os.environ.get("SARVAM_TTS_MODEL",
                                          "bulbul:v2")

AI_RESPONSE_DIR_DEFAULT = os.environ.get("AI_RESPONSE_DIR",
                                         "/tmp/ai_responses")
ORCH_TRANSCRIPT_DIR_DEFAULT = os.environ.get("ORCH_TRANSCRIPT_DIR",
                                             "/tmp/orch_transcripts")

# Bounded per-call event queue. We never expect more than a handful
# of events per call this phase, but bounding keeps a misbehaving
# producer from growing memory unbounded.
PER_CALL_QUEUE_DEPTH = 32

# Wire-frame upper bound. JSON for one transcript event is well under
# 64 KB; we cap larger payloads as a defensive measure.
MAX_FRAME_BYTES = 1 << 20   # 1 MiB

# Mock-TTS WAV parameters. 8 kHz mono 16-bit matches the call leg so
# FS playback never resamples on the way out. Duration is derived
# from the response length so the operator hears a tone whose length
# tracks the response (sanity check that the right text reached TTS).
MOCK_TTS_SR = 8000
MOCK_TTS_FREQ_BASE = 440.0
MOCK_TTS_AMP = 6000           # int16 amplitude (~−15 dBFS)


logging.basicConfig(
    level=(logging.DEBUG if os.environ.get("ORCH_DEBUG") == "1"
           else logging.INFO),
    format=("%(asctime)s.%(msecs)03d %(levelname)-5s "
            "[%(threadName)s] %(message)s"),
    datefmt="%H:%M:%S",
)
log = logging.getLogger("orch")


# ---------------------------------------------------------------------
# Frame I/O — length-prefixed JSON
# ---------------------------------------------------------------------

async def read_frame(reader: asyncio.StreamReader) -> Optional[dict]:
    """Read one length-prefixed JSON frame.

    Returns the decoded dict, or None on clean EOF. On a partial
    header / malformed JSON / oversized frame, raises ValueError so
    the caller can decide whether to log-and-close or log-and-continue.
    The reader is fed by the inbound STT forwarder, so EOF is the
    normal end of a per-connection session."""
    try:
        hdr = await reader.readexactly(4)
    except asyncio.IncompleteReadError as exc:
        if not exc.partial:
            return None
        raise ValueError(
            f"short header: got {len(exc.partial)} of 4 bytes")
    (length,) = struct.unpack(">I", hdr)
    if length == 0:
        return {}
    if length > MAX_FRAME_BYTES:
        raise ValueError(f"frame too large: {length} bytes")
    payload = await reader.readexactly(length)
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"json decode failed: {exc!r}")


def encode_frame(obj: dict) -> bytes:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return struct.pack(">I", len(body)) + body


async def send_frame_localhost(host: str, port: int,
                               obj: dict, timeout: float = 2.0
                               ) -> bool:
    """Open a one-shot TCP connection to (host,port), send one frame,
    close. Returns True on success. Used for the playback_request
    fire-and-forget hop. Bounded by `timeout`. NEVER raises out — the
    orchestrator's stability does not depend on the listener being
    reachable, and the call leg keeps running even if the listener is
    down (the audio simply will not play this turn)."""
    try:
        coro = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(coro, timeout=timeout)
    except (OSError, asyncio.TimeoutError) as exc:
        log.error("playback connect failed host=%s port=%d err=%r",
                  host, port, exc)
        return False
    try:
        writer.write(encode_frame(obj))
        await asyncio.wait_for(writer.drain(), timeout=timeout)
        return True
    except (OSError, asyncio.TimeoutError) as exc:
        log.error("playback send failed host=%s port=%d err=%r",
                  host, port, exc)
        return False
    finally:
        try:
            writer.close()
            await asyncio.wait_for(writer.wait_closed(),
                                   timeout=timeout)
        except (OSError, asyncio.TimeoutError):
            pass


# ---------------------------------------------------------------------
# LLM adapters
# ---------------------------------------------------------------------

class BaseLLMAdapter:
    """Subclass and override `generate`. Generate must return the AI
    response text within LLM_TIMEOUT_S; if not, the orchestrator will
    cancel and emit a synthetic fallback. Implementations should
    handle their own retries internally — the orchestrator does not."""

    name = "base"

    async def generate(self, text: str, language: str,
                       conn_id: str) -> str:
        raise NotImplementedError


class MockLLMAdapter(BaseLLMAdapter):
    """Deterministic templates keyed off simple keywords. Immediate
    return — no network, no retries, no failure modes. The shape of
    the call (await + return str) matches a real LLM adapter so
    swapping providers is a config change, not a code change."""

    name = "mock"

    TEMPLATES = (
        # (substring, reply) — first match wins. ASCII fold + lower
        # is applied to the input before matching.
        ("hello", "Hello! How can I help you today?"),
        ("hi ",   "Hi there! What can I do for you?"),
        ("hi!",   "Hi there! What can I do for you?"),
        ("namaste", "Namaste! Aap kaise hain?"),
        ("kem cho", "Majama! Hu tamne madad kari shaku?"),
        ("how are you",
         "I'm doing great, thanks for asking. How about you?"),
        ("thank",
         "You're welcome. Is there anything else I can help with?"),
        ("bye",
         "Goodbye! Have a wonderful day."),
        ("name",
         "I'm an AI voice assistant. What's yours?"),
        ("help",
         "Sure — tell me what you'd like help with."),
    )

    DEFAULT_REPLY = ("I heard you say: {snippet}. "
                     "How can I help with that?")

    async def generate(self, text: str, language: str,
                       conn_id: str) -> str:
        key = (text or "").strip().lower()
        for needle, reply in self.TEMPLATES:
            if needle in key:
                return reply
        snippet = (text or "").strip()
        if len(snippet) > 80:
            snippet = snippet[:77] + "..."
        if not snippet:
            snippet = "(silence)"
        return self.DEFAULT_REPLY.format(snippet=snippet)


class OpenAILLMAdapter(BaseLLMAdapter):
    """Scaffold for OpenAI / OpenAI-compatible chat completions.

    Uses urllib via asyncio.to_thread to avoid a hard runtime
    dependency on httpx/aiohttp. The base URL defaults to OpenAI's
    public endpoint and can be overridden with OPENAI_BASE_URL for
    Sarvam-OAI-compatible or any other compatible provider.
    Failures (auth, network, decode) bubble up as exceptions so the
    orchestrator's per-turn try/except records them as `llm_failures`
    and falls back to a canned response."""

    name = "openai"

    def __init__(self, model: str, api_key: str,
                 base_url: str = "https://api.openai.com/v1") -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def _call_sync(self, body: bytes, timeout: float) -> bytes:
        import urllib.request
        import urllib.error
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=body,
            headers={
                "Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            err_body = b""
            try:
                err_body = exc.read() or b""
            except Exception:
                pass
            raise RuntimeError(
                f"OpenAI HTTP {exc.code}: {err_body[:400]!r}") from exc

    async def generate(self, text: str, language: str,
                       conn_id: str) -> str:
        sys_prompt = (
            "You are a concise, friendly voice assistant on a phone "
            "call. Reply in one or two short sentences. Use the same "
            "language the user used. Do not output emojis or markdown."
        )
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": text or ""},
            ],
            "temperature": 0.5,
            "max_tokens": 160,
        }).encode("utf-8")
        # Per-call urllib timeout slightly under the orchestrator-
        # level timeout so we surface a clean RuntimeError rather
        # than a CancelledError if the upstream stalls.
        raw = await asyncio.to_thread(
            self._call_sync, body, max(LLM_TIMEOUT_S - 0.5, 1.0))
        try:
            obj = json.loads(raw.decode("utf-8"))
            return obj["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, ValueError) as exc:
            raise RuntimeError(
                f"OpenAI response parse failed: {exc!r} "
                f"raw={raw[:400]!r}") from exc


def build_llm_adapter(provider: str) -> BaseLLMAdapter:
    p = (provider or "mock").lower()
    if p == "openai":
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            log.warning("LLM_PROVIDER=openai but OPENAI_API_KEY is "
                        "unset — falling back to MockLLMAdapter")
            return MockLLMAdapter()
        base = os.environ.get("OPENAI_BASE_URL",
                              "https://api.openai.com/v1")
        log.info("LLM provider=openai model=%s base=%s",
                 OPENAI_MODEL_DEFAULT, base)
        return OpenAILLMAdapter(model=OPENAI_MODEL_DEFAULT,
                                api_key=api_key,
                                base_url=base)
    log.info("LLM provider=mock")
    return MockLLMAdapter()


# ---------------------------------------------------------------------
# TTS adapters
# ---------------------------------------------------------------------

class BaseTTSAdapter:
    """Subclass and override `synthesize`. Returns the absolute path
    to a finalized WAV on disk. The orchestrator never streams TTS
    this phase — full WAV first, then playback request. Failures
    (network, codec, write) raise so the orchestrator can fall back
    to MockTTSAdapter for that turn."""

    name = "base"

    async def synthesize(self, text: str, language: str,
                         conn_id: str, out_dir: str) -> str:
        raise NotImplementedError


def _ensure_call_dir(base_dir: str, conn_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_." else "_"
                   for c in (conn_id or "unknown"))
    target = os.path.join(base_dir, safe)
    os.makedirs(target, exist_ok=True)
    return target


def _write_pcm_wav(path: str, sample_rate: int,
                   pcm_int16_bytes: bytes) -> None:
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_int16_bytes)


def _synthesize_mock_pcm(text: str, sample_rate: int,
                         max_seconds: float = 1.6) -> bytes:
    """Generate a short 'speaking-tone' PCM whose duration tracks the
    response length and whose pitch jitters per word so the operator
    can audibly distinguish different replies even without real TTS.
    Pure stdlib math; never fails. Duration capped so a long LLM reply
    doesn't produce a 30-second tone the caller has to sit through."""
    words = (text or "").split()
    n_words = max(1, min(len(words), 8))
    seconds = max(0.4, min(max_seconds, 0.18 * n_words + 0.3))
    n = int(seconds * sample_rate)
    samples = bytearray(2 * n)
    # Linear envelope to avoid the click at start/end.
    edge = max(int(0.02 * sample_rate), 32)
    for i in range(n):
        # Pitch jitter: small modulation tied to the byte values of
        # the text so identical inputs produce identical audio.
        mod = 0.0
        if words:
            tag = words[i % len(words)]
            mod = (sum(ord(c) for c in tag[:6]) % 31) - 15  # ±15 Hz
        freq = MOCK_TTS_FREQ_BASE + mod
        v = MOCK_TTS_AMP * math.sin(2.0 * math.pi * freq * i
                                    / sample_rate)
        # Envelope (fade in/out).
        if i < edge:
            v *= i / edge
        elif i > n - edge:
            v *= (n - i) / edge
        s = int(v)
        if s > 32767:
            s = 32767
        elif s < -32768:
            s = -32768
        struct.pack_into("<h", samples, 2 * i, s)
    return bytes(samples)


class MockTTSAdapter(BaseTTSAdapter):
    """Deterministic synthetic-tone synthesizer.

    Output is 8 kHz mono 16-bit PCM, length proportional to text
    length, pitch derived from the text content. Caller hears a
    short 'beep-melody' rather than English speech — sufficient for
    end-to-end pipeline validation when no real TTS is configured."""

    name = "mock"

    async def synthesize(self, text: str, language: str,
                         conn_id: str, out_dir: str) -> str:
        call_dir = _ensure_call_dir(out_dir, conn_id)
        ts = int(time.time() * 1000)
        path = os.path.join(call_dir, f"{ts}.wav")
        pcm = await asyncio.to_thread(
            _synthesize_mock_pcm, text, MOCK_TTS_SR)
        await asyncio.to_thread(
            _write_pcm_wav, path, MOCK_TTS_SR, pcm)
        return path


class SarvamTTSAdapter(BaseTTSAdapter):
    """Scaffold for Sarvam Bulbul TTS. Hits the REST text-to-speech
    endpoint and returns a WAV path. Sarvam's TTS API returns base64-
    encoded PCM/WAV chunks under `audios`; we concatenate them and
    write a single WAV. If the request fails or the API key is
    rejected, the orchestrator catches the exception and falls back
    to MockTTSAdapter for that turn."""

    name = "sarvam"

    DEFAULT_BASE_URL = "https://api.sarvam.ai"

    def __init__(self, api_key: str, voice: str, model: str,
                 base_url: Optional[str] = None,
                 sample_rate: int = 8000) -> None:
        self.api_key = api_key
        self.voice = voice
        self.model = model
        self.base_url = (base_url
                         or os.environ.get("SARVAM_BASE_URL")
                         or self.DEFAULT_BASE_URL).rstrip("/")
        self.sample_rate = sample_rate

    def _call_sync(self, body: bytes, timeout: float) -> bytes:
        import urllib.request
        import urllib.error
        req = urllib.request.Request(
            self.base_url + "/text-to-speech",
            data=body,
            headers={
                "api-subscription-key": self.api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            err_body = b""
            try:
                err_body = exc.read() or b""
            except Exception:
                pass
            raise RuntimeError(
                f"Sarvam TTS HTTP {exc.code}: "
                f"{err_body[:400]!r}") from exc

    async def synthesize(self, text: str, language: str,
                         conn_id: str, out_dir: str) -> str:
        call_dir = _ensure_call_dir(out_dir, conn_id)
        ts = int(time.time() * 1000)
        path = os.path.join(call_dir, f"{ts}.wav")
        body = json.dumps({
            "inputs": [text or ""],
            "target_language_code": language or "en-IN",
            "speaker": self.voice,
            "model": self.model,
            "speech_sample_rate": int(self.sample_rate),
        }).encode("utf-8")
        raw = await asyncio.to_thread(
            self._call_sync, body, max(TTS_TIMEOUT_S - 0.5, 1.0))
        try:
            obj = json.loads(raw.decode("utf-8"))
            audios = obj.get("audios") or []
            if not audios:
                raise RuntimeError(
                    f"Sarvam TTS returned no audios; "
                    f"raw={raw[:400]!r}")
            pcm = bytearray()
            for chunk in audios:
                # Sarvam returns base64-encoded WAV (with header) per
                # input. Strip the WAV header and concatenate raw
                # PCM frames so a multi-input response composes into
                # a single WAV at the requested sample rate.
                wav_bytes = base64.b64decode(chunk)
                pcm += _strip_wav_header(wav_bytes)
            await asyncio.to_thread(
                _write_pcm_wav, path, self.sample_rate, bytes(pcm))
            return path
        except (KeyError, ValueError) as exc:
            raise RuntimeError(
                f"Sarvam TTS response parse failed: {exc!r} "
                f"raw={raw[:400]!r}") from exc


def _strip_wav_header(wav_bytes: bytes) -> bytes:
    """Best-effort WAV header strip. Returns the PCM data chunk
    payload. If the input does not look like a RIFF/WAVE file we
    return it verbatim — the caller writes the result into a fresh
    WAV container so a malformed strip cannot corrupt the output."""
    if len(wav_bytes) < 44 or wav_bytes[:4] != b"RIFF":
        return wav_bytes
    # Walk subchunks to find 'data'.
    i = 12
    while i + 8 <= len(wav_bytes):
        cid = wav_bytes[i:i + 4]
        (csize,) = struct.unpack("<I", wav_bytes[i + 4:i + 8])
        if cid == b"data":
            return wav_bytes[i + 8: i + 8 + csize]
        i += 8 + csize
    return wav_bytes[44:]


def build_tts_adapter(provider: str) -> BaseTTSAdapter:
    p = (provider or "mock").lower()
    if p == "sarvam":
        api_key = os.environ.get("SARVAM_API_KEY", "").strip()
        if not api_key:
            log.warning("TTS_PROVIDER=sarvam but SARVAM_API_KEY is "
                        "unset — falling back to MockTTSAdapter")
            return MockTTSAdapter()
        log.info("TTS provider=sarvam voice=%s model=%s",
                 SARVAM_TTS_VOICE_DEFAULT, SARVAM_TTS_MODEL_DEFAULT)
        return SarvamTTSAdapter(api_key=api_key,
                                voice=SARVAM_TTS_VOICE_DEFAULT,
                                model=SARVAM_TTS_MODEL_DEFAULT)
    log.info("TTS provider=mock")
    return MockTTSAdapter()


# ---------------------------------------------------------------------
# Per-call session
# ---------------------------------------------------------------------

# FSM states. String constants (not Enum) so they appear verbatim in
# log lines without an Enum.NAME prefix.
ST_IDLE = "IDLE"
ST_LISTENING = "LISTENING"
ST_PROCESSING_LLM = "PROCESSING_LLM"
ST_GENERATING_TTS = "GENERATING_TTS"
ST_PLAYING_RESPONSE = "PLAYING_RESPONSE"
ST_COMPLETED = "COMPLETED"
ST_DISCONNECTED = "DISCONNECTED"


BUSY_STATES = (ST_PROCESSING_LLM, ST_GENERATING_TTS,
               ST_PLAYING_RESPONSE)


class CallSession:
    """One per logical call (conn_id). Lives inside the orchestrator
    asyncio loop. Owns the per-call FSM, counters, transcript log,
    and an in-flight task that runs the LLM->TTS->playback chain.
    Multiple inbound TCP connections may forward events for the same
    conn_id over the call's lifetime; SessionRegistry de-dupes."""

    __slots__ = (
        "conn_id", "t0", "state", "state_t", "state_durations",
        "transcripts", "in_flight", "counters",
        "llm_latencies", "tts_latencies", "turn_latencies",
        "language", "provider",
    )

    def __init__(self, conn_id: str) -> None:
        self.conn_id = conn_id
        self.t0 = time.monotonic()
        self.state = ST_IDLE
        self.state_t = self.t0
        self.state_durations: dict = {}
        self.transcripts: list = []
        self.in_flight: Optional[asyncio.Task] = None
        self.counters: dict = {
            "transcripts_received": 0,
            "transcripts_dropped_during_turn": 0,
            "llm_requests": 0,
            "llm_failures": 0,
            "llm_timeouts": 0,
            "tts_requests": 0,
            "tts_failures": 0,
            "tts_timeouts": 0,
            "tts_fallbacks": 0,
            "playback_requests": 0,
            "playback_success": 0,
            "playback_failures": 0,
        }
        self.llm_latencies: deque = deque(maxlen=128)
        self.tts_latencies: deque = deque(maxlen=128)
        self.turn_latencies: deque = deque(maxlen=128)
        self.language: str = ""
        self.provider: str = ""

    def transition(self, new_state: str) -> None:
        now = time.monotonic()
        delta = now - self.state_t
        prev = self.state
        self.state_durations[prev] = (
            self.state_durations.get(prev, 0.0) + delta)
        self.state = new_state
        self.state_t = now
        log.info("[FSM] conn_id=%s %s -> %s (spent %.3fs in %s)",
                 self.conn_id, prev, new_state, delta, prev)

    def is_busy(self) -> bool:
        return self.state in BUSY_STATES


# ---------------------------------------------------------------------
# Session registry (orchestrator-side, asyncio-loop-local)
# ---------------------------------------------------------------------

class SessionRegistry:
    """Maps conn_id -> CallSession. Lives on the orchestrator's
    asyncio loop; no cross-thread access. Provides idempotent
    creation so repeated HELLOs from a flapping STT forwarder don't
    spawn duplicate sessions, and lookup for incoming finals."""

    def __init__(self) -> None:
        self._by_id: dict = {}

    def get_or_create(self, conn_id: str) -> CallSession:
        s = self._by_id.get(conn_id)
        if s is not None:
            return s
        s = CallSession(conn_id)
        self._by_id[conn_id] = s
        log.info("[SESSION OPEN] conn_id=%s", conn_id)
        return s

    def get(self, conn_id: str) -> Optional[CallSession]:
        return self._by_id.get(conn_id)

    def close(self, conn_id: str) -> Optional[CallSession]:
        return self._by_id.pop(conn_id, None)

    def all(self) -> list:
        return list(self._by_id.values())


# ---------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------

def _avg(d: deque) -> Optional[float]:
    if not d:
        return None
    return sum(d) / len(d)


def _verdict(s: CallSession) -> str:
    c = s.counters
    if c["transcripts_received"] == 0:
        return ("CASE ORCH-Z — no transcripts received this session. "
                "STT forward path may be cold.")
    if c["llm_failures"] + c["llm_timeouts"] >= max(
            c["llm_requests"], 1):
        return ("CASE ORCH-B — every LLM request failed or timed out "
                "(failures=%d timeouts=%d requests=%d)." % (
                    c["llm_failures"], c["llm_timeouts"],
                    c["llm_requests"]))
    if (c["tts_failures"] + c["tts_timeouts"] > 0
            and c["playback_success"] == 0):
        return ("CASE ORCH-C — TTS failed (failures=%d timeouts=%d) "
                "and no playback ever succeeded." % (
                    c["tts_failures"], c["tts_timeouts"]))
    if (c["playback_requests"] > 0
            and c["playback_success"] == 0):
        return ("CASE ORCH-D — playback injection failed "
                "(requests=%d failures=%d). Caller heard nothing." % (
                    c["playback_requests"], c["playback_failures"]))
    if c["playback_success"] > 0:
        return ("CASE ORCH-A — orchestrator healthy. "
                "transcripts=%d turns=%d playbacks=%d" % (
                    c["transcripts_received"],
                    len(s.turn_latencies),
                    c["playback_success"]))
    return ("CASE ORCH-INC — transcripts received but no completed "
            "turn. Inspect counters.")


def log_orch_summary(s: CallSession) -> None:
    # Roll the time spent in the current state into state_durations
    # so the report covers the full session.
    now = time.monotonic()
    s.state_durations[s.state] = (
        s.state_durations.get(s.state, 0.0) + (now - s.state_t))
    duration = now - s.t0
    c = s.counters
    log.info("---------- PHASE-12 ORCHESTRATOR SUMMARY -------")
    log.info("conn_id=%s  duration=%.3fs  language=%s  "
             "stt_provider=%s",
             s.conn_id, duration, s.language or "?",
             s.provider or "?")
    log.info("counters: %s", c)
    avg_llm = _avg(s.llm_latencies)
    avg_tts = _avg(s.tts_latencies)
    avg_turn = _avg(s.turn_latencies)
    log.info("latency:")
    log.info("    %-28s %s", "avg_llm_latency",
             ("%.0fms" % (avg_llm * 1000)) if avg_llm else "—")
    log.info("    %-28s %s", "avg_tts_latency",
             ("%.0fms" % (avg_tts * 1000)) if avg_tts else "—")
    log.info("    %-28s %s", "avg_end_to_end_turn_latency",
             ("%.0fms" % (avg_turn * 1000)) if avg_turn else "—")
    log.info("state_durations:")
    for st in (ST_IDLE, ST_LISTENING, ST_PROCESSING_LLM,
               ST_GENERATING_TTS, ST_PLAYING_RESPONSE,
               ST_COMPLETED, ST_DISCONNECTED):
        v = s.state_durations.get(st)
        log.info("    %-28s %s", st,
                 ("%.3fs" % v) if v is not None else "—")
    if s.transcripts:
        log.info("transcripts (%d):", len(s.transcripts))
        for i, t in enumerate(s.transcripts, 1):
            log.info("    [%d] (%s) %s",
                     i, t.get("language") or "?",
                     (t.get("text") or "")[:200])
    log.info("VERDICT: %s", _verdict(s))
    log.info("------------------------------------------------")


# ---------------------------------------------------------------------
# Per-call transcript persistence
# ---------------------------------------------------------------------

class _OrchTranscriptWriter:
    """Append-only JSONL of orchestrator-side events: incoming
    finals, LLM responses, TTS results, playback dispatches, errors.
    Mirrors the spirit of stt_stream_worker's persistence so a single
    grep across both files reconstructs the full caller -> AI ->
    caller turn timeline. Entirely best-effort; disables itself on
    first error."""

    def __init__(self, conn_id: str, base_dir: str) -> None:
        self.conn_id = conn_id
        self.path = os.path.join(base_dir, f"{conn_id}.jsonl")
        self._fp = None
        self._disabled = False
        self.lines_written = 0
        try:
            os.makedirs(base_dir, exist_ok=True)
            self._fp = open(self.path, "a", buffering=1,
                            encoding="utf-8")
        except OSError as exc:
            self._disabled = True
            log.warning("orch transcript writer setup failed "
                        "conn_id=%s err=%r — persistence disabled",
                        conn_id, exc)

    async def write(self, evt: dict) -> None:
        if self._disabled or self._fp is None:
            return
        try:
            line = json.dumps(evt, ensure_ascii=False) + "\n"
            await asyncio.to_thread(self._fp.write, line)
            self.lines_written += 1
        except Exception as exc:
            log.warning("orch transcript writer write failed "
                        "conn_id=%s err=%r — disabling",
                        self.conn_id, exc)
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
# Turn driver: LLM -> TTS -> playback request
# ---------------------------------------------------------------------

class TurnDriver:
    """Owns the adapters and the playback dispatch endpoint. Stateless
    per turn; CallSession holds per-call state. One TurnDriver
    instance per orchestrator process."""

    def __init__(self, llm: BaseLLMAdapter, tts: BaseTTSAdapter,
                 mock_tts_fallback: BaseTTSAdapter,
                 ai_response_dir: str,
                 playback_host: str, playback_port: int,
                 transcript_writer_factory: Callable[[str], _OrchTranscriptWriter]
                 ) -> None:
        self.llm = llm
        self.tts = tts
        self.mock_tts_fallback = mock_tts_fallback
        self.ai_response_dir = ai_response_dir
        self.playback_host = playback_host
        self.playback_port = playback_port
        self._writers: dict = {}
        self._writer_factory = transcript_writer_factory
        os.makedirs(ai_response_dir, exist_ok=True)

    def writer_for(self, conn_id: str) -> _OrchTranscriptWriter:
        w = self._writers.get(conn_id)
        if w is None:
            w = self._writer_factory(conn_id)
            self._writers[conn_id] = w
        return w

    def close_writer(self, conn_id: str) -> None:
        w = self._writers.pop(conn_id, None)
        if w is not None:
            w.close()

    async def _call_llm(self, session: CallSession,
                        text: str) -> Optional[str]:
        session.counters["llm_requests"] += 1
        t0 = time.monotonic()
        try:
            reply = await asyncio.wait_for(
                self.llm.generate(text, session.language,
                                  session.conn_id),
                timeout=LLM_TIMEOUT_S)
        except asyncio.TimeoutError:
            session.counters["llm_timeouts"] += 1
            log.error("[LLM TIMEOUT] conn_id=%s after %.2fs",
                      session.conn_id, LLM_TIMEOUT_S)
            return None
        except Exception as exc:
            session.counters["llm_failures"] += 1
            log.error("[LLM ERROR] conn_id=%s err=%r",
                      session.conn_id, exc)
            return None
        dt = time.monotonic() - t0
        session.llm_latencies.append(dt)
        log.info("[LLM] conn_id=%s lat=%.0fms text=%r",
                 session.conn_id, dt * 1000.0, reply[:200])
        await self.writer_for(session.conn_id).write({
            "timestamp": time.time(), "type": "llm_response",
            "provider": self.llm.name, "latency_ms": dt * 1000.0,
            "text": reply,
        })
        return reply

    async def _call_tts(self, session: CallSession,
                        text: str) -> Optional[str]:
        session.counters["tts_requests"] += 1
        t0 = time.monotonic()
        try:
            wav_path = await asyncio.wait_for(
                self.tts.synthesize(text, session.language,
                                    session.conn_id,
                                    self.ai_response_dir),
                timeout=TTS_TIMEOUT_S)
            dt = time.monotonic() - t0
            session.tts_latencies.append(dt)
            log.info("[TTS] conn_id=%s provider=%s lat=%.0fms "
                     "path=%s", session.conn_id, self.tts.name,
                     dt * 1000.0, wav_path)
            await self.writer_for(session.conn_id).write({
                "timestamp": time.time(), "type": "tts_result",
                "provider": self.tts.name,
                "latency_ms": dt * 1000.0, "wav_path": wav_path,
            })
            return wav_path
        except asyncio.TimeoutError:
            session.counters["tts_timeouts"] += 1
            log.error("[TTS TIMEOUT] conn_id=%s after %.2fs — "
                      "falling back to mock TTS",
                      session.conn_id, TTS_TIMEOUT_S)
        except Exception as exc:
            session.counters["tts_failures"] += 1
            log.error("[TTS ERROR] conn_id=%s err=%r — falling "
                      "back to mock TTS", session.conn_id, exc)
        # Fallback: same shape, never the network. If the primary
        # adapter IS the mock adapter we don't double-count the
        # fallback (the same call would just fail again).
        if self.tts is self.mock_tts_fallback:
            return None
        session.counters["tts_fallbacks"] += 1
        try:
            wav_path = await asyncio.wait_for(
                self.mock_tts_fallback.synthesize(
                    text, session.language, session.conn_id,
                    self.ai_response_dir),
                timeout=TTS_TIMEOUT_S)
            log.info("[TTS FALLBACK OK] conn_id=%s path=%s",
                     session.conn_id, wav_path)
            return wav_path
        except Exception as exc:
            log.error("[TTS FALLBACK FAILED] conn_id=%s err=%r",
                      session.conn_id, exc)
            return None

    async def _send_playback(self, session: CallSession,
                             wav_path: str) -> bool:
        session.counters["playback_requests"] += 1
        evt = {
            "type": "playback_request",
            "conn_id": session.conn_id,
            "wav_path": wav_path,
        }
        ok = await send_frame_localhost(self.playback_host,
                                        self.playback_port, evt)
        if ok:
            session.counters["playback_success"] += 1
            log.info("[PLAYBACK SENT] conn_id=%s path=%s",
                     session.conn_id, wav_path)
        else:
            session.counters["playback_failures"] += 1
            log.error("[PLAYBACK SEND FAILED] conn_id=%s path=%s",
                      session.conn_id, wav_path)
        await self.writer_for(session.conn_id).write({
            "timestamp": time.time(), "type": "playback_request",
            "wav_path": wav_path, "ok": ok,
        })
        return ok

    async def run_turn(self, session: CallSession,
                       text: str) -> None:
        """LLM -> TTS -> playback for one final transcript. All
        exceptions are swallowed here so a misbehaving adapter cannot
        cancel the orchestrator's serve loop, and so the per-call
        state machine always returns to a sane terminal state. The
        outer caller (`maybe_start_turn`) is responsible for setting
        in_flight to None and clearing the busy state.

        Disconnect-aware: each transition checks for a prior
        DISCONNECTED state (set by the BYE handler) and bails out
        cleanly, so the FSM log stays consistent even in the rare
        race where BYE arrives mid-turn."""
        turn_t0 = time.monotonic()

        def _abort_if_disconnected() -> bool:
            return session.state == ST_DISCONNECTED

        try:
            if _abort_if_disconnected():
                return
            session.transition(ST_PROCESSING_LLM)
            reply = await self._call_llm(session, text)
            if reply is None or _abort_if_disconnected():
                return
            session.transition(ST_GENERATING_TTS)
            wav_path = await self._call_tts(session, reply)
            if wav_path is None or _abort_if_disconnected():
                return
            session.transition(ST_PLAYING_RESPONSE)
            await self._send_playback(session, wav_path)
        except asyncio.CancelledError:
            log.warning("[TURN CANCELLED] conn_id=%s", session.conn_id)
            raise
        except Exception as exc:
            log.exception("[TURN CRASHED] conn_id=%s err=%r",
                          session.conn_id, exc)
        finally:
            session.turn_latencies.append(time.monotonic() - turn_t0)
            # Phase-12 single-turn: after one playback dispatch we
            # return to LISTENING and wait for the next final. The
            # session is not COMPLETED — that only happens at BYE /
            # EOF.
            if session.state != ST_DISCONNECTED:
                session.transition(ST_LISTENING)


# ---------------------------------------------------------------------
# Inbound TCP server: STT -> orchestrator
# ---------------------------------------------------------------------

class Orchestrator:

    def __init__(self, registry: SessionRegistry,
                 driver: TurnDriver) -> None:
        self.registry = registry
        self.driver = driver

    async def maybe_start_turn(self, session: CallSession,
                               text: str) -> None:
        if session.is_busy():
            session.counters["transcripts_dropped_during_turn"] += 1
            log.info("[TURN BUSY] conn_id=%s state=%s — dropping "
                     "transcript text=%r",
                     session.conn_id, session.state, text[:80])
            return
        # Spawn the turn so the inbound read loop can keep draining
        # frames concurrently. The task reference is held on the
        # session so we can cancel it on disconnect.
        async def _runner():
            try:
                await self.driver.run_turn(session, text)
            finally:
                session.in_flight = None
        session.in_flight = asyncio.create_task(_runner())

    async def handle_event(self, evt: dict) -> None:
        etype = evt.get("type")
        conn_id = (evt.get("conn_id") or "").strip()
        if not conn_id:
            log.warning("[EVENT NO CONN_ID] type=%s evt=%r",
                        etype, evt)
            return
        if etype == "hello":
            session = self.registry.get_or_create(conn_id)
            session.language = evt.get("language") or session.language
            session.provider = evt.get("provider") or session.provider
            if session.state == ST_IDLE:
                session.transition(ST_LISTENING)
            return
        if etype == "final_transcript":
            session = self.registry.get_or_create(conn_id)
            session.language = evt.get("language") or session.language
            session.provider = evt.get("provider") or session.provider
            text = (evt.get("text") or "").strip()
            session.counters["transcripts_received"] += 1
            session.transcripts.append({
                "timestamp": evt.get("timestamp"),
                "language": session.language,
                "text": text,
            })
            await self.driver.writer_for(conn_id).write({
                "timestamp": time.time(),
                "type": "final_transcript_in",
                "language": session.language,
                "provider": session.provider, "text": text,
            })
            log.info("[FINAL] conn_id=%s lang=%s text=%r",
                     conn_id, session.language, text[:200])
            if session.state == ST_IDLE:
                session.transition(ST_LISTENING)
            await self.maybe_start_turn(session, text)
            return
        if etype == "bye":
            session = self.registry.get(conn_id)
            if session is not None:
                if session.in_flight is not None:
                    session.in_flight.cancel()
                session.transition(ST_DISCONNECTED)
                log_orch_summary(session)
                self.driver.close_writer(conn_id)
                self.registry.close(conn_id)
            return
        log.warning("[UNKNOWN EVENT] type=%r conn_id=%s",
                    etype, conn_id)

    async def handle_inbound_connection(
            self, reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter) -> None:
        peer_tup = writer.get_extra_info("peername")
        peer = ("%s:%d" % peer_tup[:2]) if peer_tup else "?"
        log.info("=== ORCH FORWARD CONNECT peer=%s ===", peer)
        seen_conn_ids: set = set()
        try:
            while True:
                try:
                    evt = await read_frame(reader)
                except ValueError as exc:
                    log.warning("orch frame parse failed peer=%s "
                                "err=%r — dropping frame", peer, exc)
                    continue
                if evt is None:
                    break
                cid = (evt.get("conn_id") or "").strip()
                if cid:
                    seen_conn_ids.add(cid)
                try:
                    await self.handle_event(evt)
                except Exception as exc:
                    # Per-event isolation. One bad transcript does
                    # not poison the connection.
                    log.exception("orch handle_event crashed "
                                  "evt_type=%s err=%r",
                                  evt.get("type"), exc)
        except Exception as exc:
            log.exception("orch inbound connection crashed peer=%s "
                          "err=%r", peer, exc)
        finally:
            log.info("=== ORCH FORWARD CLOSE peer=%s "
                     "conn_ids_seen=%d ===",
                     peer, len(seen_conn_ids))
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass


# ---------------------------------------------------------------------
# Periodic janitor — close stale sessions after grace period
# ---------------------------------------------------------------------

async def janitor(registry: SessionRegistry,
                  driver: TurnDriver,
                  grace_seconds: float) -> None:
    """Best-effort cleanup of sessions that never got a BYE. STT
    forwarder may drop without sending one; this prevents the
    registry from leaking. We DO NOT cancel in-flight turns — they
    finish on their own and re-transition to LISTENING; the janitor
    only catches sessions that have been idle longer than the grace
    window."""
    try:
        while True:
            await asyncio.sleep(15.0)
            now = time.monotonic()
            for s in list(registry.all()):
                if s.in_flight is not None:
                    continue
                if (now - s.state_t) < grace_seconds:
                    continue
                log.info("[JANITOR CLOSE] conn_id=%s idle=%.1fs",
                         s.conn_id, now - s.state_t)
                if s.state != ST_DISCONNECTED:
                    s.transition(ST_DISCONNECTED)
                try:
                    log_orch_summary(s)
                except Exception as exc:
                    log.error("janitor summary render failed: %r",
                              exc)
                driver.close_writer(s.conn_id)
                registry.close(s.conn_id)
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------

async def main_async(args) -> None:
    registry = SessionRegistry()
    llm = build_llm_adapter(args.llm_provider)
    tts = build_tts_adapter(args.tts_provider)
    mock_tts = MockTTSAdapter()  # always available as fallback

    def _writer_factory(conn_id: str) -> _OrchTranscriptWriter:
        return _OrchTranscriptWriter(conn_id, args.transcript_dir)

    driver = TurnDriver(
        llm=llm, tts=tts, mock_tts_fallback=mock_tts,
        ai_response_dir=args.response_dir,
        playback_host=args.playback_host,
        playback_port=args.playback_port,
        transcript_writer_factory=_writer_factory,
    )
    orch = Orchestrator(registry, driver)

    log.info("Phase-12 conversation_orchestrator listening on "
             "%s:%d  (llm=%s tts=%s response_dir=%s "
             "playback_target=%s:%d transcript_dir=%s "
             "llm_timeout=%.1fs tts_timeout=%.1fs)",
             args.host, args.port,
             llm.name, tts.name, args.response_dir,
             args.playback_host, args.playback_port,
             args.transcript_dir,
             LLM_TIMEOUT_S, TTS_TIMEOUT_S)
    server = await asyncio.start_server(
        orch.handle_inbound_connection, args.host, args.port)
    janitor_task = asyncio.create_task(
        janitor(registry, driver, args.session_grace))
    try:
        async with server:
            await server.serve_forever()
    finally:
        janitor_task.cancel()
        try:
            await janitor_task
        except asyncio.CancelledError:
            pass


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default=ORCH_HOST_DEFAULT)
    p.add_argument("--port", type=int, default=ORCH_PORT_DEFAULT)
    p.add_argument("--playback-host", default=PLAYBACK_HOST_DEFAULT)
    p.add_argument("--playback-port", type=int,
                   default=PLAYBACK_PORT_DEFAULT)
    p.add_argument("--llm-provider", default=LLM_PROVIDER_DEFAULT)
    p.add_argument("--tts-provider", default=TTS_PROVIDER_DEFAULT)
    p.add_argument("--response-dir", default=AI_RESPONSE_DIR_DEFAULT)
    p.add_argument("--transcript-dir",
                   default=ORCH_TRANSCRIPT_DIR_DEFAULT)
    p.add_argument("--session-grace", type=float, default=120.0,
                   help=("Seconds a session may sit idle without "
                         "BYE before janitor closes it."))
    args = p.parse_args()
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        log.info("shutdown requested")


if __name__ == "__main__":
    main()
