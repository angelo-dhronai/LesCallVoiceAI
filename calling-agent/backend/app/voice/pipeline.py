"""
Pipeline orchestrator — connects all layers via async queues.

Architecture:
    [AudioLayer] → vad_queue → [VADLayer] → stt_queue → [STTWorker]
        → transcript_queue → [ConversationEngine] → tts_queue → [TTSPlayer]

Each layer is an independent async task. Queues are bounded.
Failure in one layer does not crash others.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from dataclasses import dataclass

import numpy as np

from .audio_input import SAMPLE_RATE, SAMPLES_PER_FRAME
from .vad import VoiceActivityDetector
from .stt.router import STTRouter
from .tts_stream import StreamingTTS, MockStreamingTTS
from .tts_sarvam import SarvamTTS
from .turn_manager import TurnManager, TurnManagerConfig
from .event_hub import EventHub
from ..agent.brain import VoiceAgent

from ..audio_transport.base import AudioSource, AudioSink
from ..audio_transport.local_mic_source import LocalMicSource
from ..audio_transport.local_speaker_sink import LocalSpeakerSink

logger = logging.getLogger(__name__)

# ENV-based control for barge-in and telephony defaults
ENABLE_BARGE_IN = os.environ.get("ENABLE_BARGE_IN", "true").lower() == "true"
TELEPHONY_MODE = os.environ.get("TELEPHONY_MODE", "false").lower() == "true"


@dataclass
class AudioSegment:
    """A complete speech segment ready for STT."""
    audio: bytes
    duration_ms: float
    timestamp: float


@dataclass
class Transcript:
    """STT result ready for conversation engine."""
    text: str
    duration_ms: float
    timestamp: float
    stt_latency_ms: float = 0.0


@dataclass
class Response:
    """Engine response ready for TTS."""
    text: str
    is_shortcut: bool
    timestamp: float


QUEUE_SIZE_AUDIO = 40
QUEUE_SIZE_STT = 3
QUEUE_SIZE_TRANSCRIPT = 3
QUEUE_SIZE_TTS = 3

MAX_SPEECH_SECONDS = 4
MAX_BUFFER_BYTES = SAMPLE_RATE * 2 * MAX_SPEECH_SECONDS
# Lowered 700→300ms: short inbound responses like "haan", "yes", "ha" are
# valid single-word answers that must reach STT. 300ms is enough audio to
# distinguish real speech from a click/breath without missing short replies.
MIN_SPEECH_MS = 300.0
BARGE_IN_COOLDOWN_MS = 400.0
# Lowered 5→3 frames: telephony callers often speak immediately after hearing
# the agent; requiring 5 sustained frames (100ms) was causing their first
# syllable to be swallowed before barge-in triggered.
BARGE_IN_MIN_FRAMES = 3

# Echo cooldown after TTS ends naturally: prevents residual TTS audio from
# being re-captured by the microphone and sent to STT as "user speech".
# In telephony mode the TTS speaker and caller mic are on separate paths, but
# we still add a small margin for packet-level delays and codec artifacts.
# In local-mic mode (development) acoustic echo from the room can persist for
# 1-2 seconds, so a longer window is essential to avoid self-transcription.
TTS_ECHO_COOLDOWN_MS = 400.0 if TELEPHONY_MODE else 1500.0

# Onset lowered 3→2 frames: catch speech sooner so the very first word after
# a pause isn't clipped. Hold kept at 10 for noise immunity.
SPEECH_ONSET_MIN_FRAMES = 2
SPEECH_HOLD_MIN_FRAMES = 10

# Hard-flush silence gate (frames of consecutive silence after hold expires).
# 12 frames (240 ms total) was too aggressive — natural mid-sentence pauses
# of 200–400 ms caused premature flushing and cut off the second half of
# callers' utterances. Raised to 30 frames (600 ms total) which still catches
# single-word responses while tolerating normal conversational pauses.
HARD_FLUSH_SILENCE_FRAMES = 30


def _detect_tts_language(text: str) -> str:
    """Detect language from script for Sarvam TTS voice selection."""
    for ch in text:
        cp = ord(ch)
        if 0x0A80 <= cp <= 0x0AFF:   # Gujarati block
            return "gu"
        if 0x0900 <= cp <= 0x097F:   # Devanagari block (Hindi)
            return "hi"
    return "en"


def _generate_keyboard_sound(sample_rate: int = 16000) -> bytes:
    """Generate a burst of keyboard typing clicks (PCM16 mono).

    Returns ~700ms of audio: 4 clicks spaced ~120ms apart with a trailing
    silence gap so the burst can be looped naturally.
    """
    rng = np.random.default_rng(7)
    frames: list[bytes] = []

    click_s = 0.022   # 22ms per click
    gap_s   = 0.110   # 110ms silent gap between clicks
    n_clicks = 4

    click_n = int(sample_rate * click_s)
    gap_n   = int(sample_rate * gap_s)

    for i in range(n_clicks):
        t = np.linspace(0, click_s, click_n, endpoint=False)
        # Decaying white-noise burst with a mild tonal component
        noise = rng.standard_normal(click_n)
        decay = np.exp(-t / 0.005)            # 5 ms time-constant
        freq  = 900 + (i * 300 % 600)
        click = (noise * 0.55 + np.sin(2 * np.pi * freq * t) * 0.45) * decay * 0.22
        click = np.clip(click, -1.0, 1.0)
        frames.append((click * 32767).astype(np.int16).tobytes())
        if i < n_clicks - 1:
            frames.append(bytes(gap_n * 2))   # silent gap (int16 = 2 bytes/sample)

    # 320 ms trailing silence so looped bursts feel natural
    frames.append(bytes(int(sample_rate * 0.32) * 2))
    return b"".join(frames)


class VoicePipeline:
    """
    Production voice pipeline with independent async layers.

    Each layer runs as a separate task communicating via bounded queues.
    The audio loop never blocks on downstream processing.
    """

    def __init__(
        self,
        audio_source: AudioSource | None = None,
        audio_sink: AudioSink | None = None,
        vad: VoiceActivityDetector | None = None,
        stt: STTRouter | None = None,
        tts: StreamingTTS | None = None,
        conversation: VoiceAgent | None = None,
        event_hub: EventHub | None = None,
        mic_enabled: bool = True,
    ) -> None:
        self.audio = audio_source or LocalMicSource(sample_rate=SAMPLE_RATE)
        self.sink = audio_sink or LocalSpeakerSink(sample_rate=SAMPLE_RATE)
        
        # Phase 17: Adjust VAD for telephony mode
        if vad is None:
            vad = VoiceActivityDetector(
                minimum_threshold=260.0 if TELEPHONY_MODE else 150.0,
                minimum_rms_floor=60.0 if TELEPHONY_MODE else 50.0,
                # Absolute ceiling for threshold to prevent deafness
                maximum_threshold=3000.0 if TELEPHONY_MODE else 1500.0,
            )
        self.vad = vad
        
        self.stt = stt or STTRouter()
        self.conversation = conversation or VoiceAgent()
        self._hub = event_hub

        # TTS uses the sink
        self.tts = tts or SarvamTTS(audio_sink=self.sink)
        if tts and hasattr(tts, 'set_sink'):
            tts.set_sink(self.sink)

        # Inter-layer queues
        self._stt_queue: asyncio.Queue[AudioSegment] = asyncio.Queue(maxsize=QUEUE_SIZE_STT)
        self._transcript_queue: asyncio.Queue[Transcript] = asyncio.Queue(maxsize=QUEUE_SIZE_TRANSCRIPT)
        self._tts_queue: asyncio.Queue[Response] = asyncio.Queue(maxsize=QUEUE_SIZE_TTS)

        # State
        self._running = False
        self._call_active = False  # True only after signal_call_active() is called
        self._tasks: list[asyncio.Task] = []
        self._current_generation_task: asyncio.Task | None = None
        self._tts_playing = False
        self._tts_start_time = 0.0
        self._tts_end_time = 0.0
        self._vad_mute_until = 0.0
        self._barge_in_occurred = False
        self._mic_enabled = mic_enabled

        # VAD + buffering state
        self._audio_buffer = bytearray()
        self._speech_duration_ms = 0.0
        self._prev_speech = False
        self._speech_frame_streak = 0
        self._frame_count = 0
        self._speech_onset_frames = 0  # debounce: frames since speech started
        self._silence_frames = 0       # debounce: frames since last speech

        # Phase 17: Hysteresis tuning
        self._speech_onset_min_frames =  SPEECH_ONSET_MIN_FRAMES
        self._speech_hold_min_frames = SPEECH_HOLD_MIN_FRAMES
       

        logger.info(
            "[PIPELINE] BARGE_IN=%s TELEPHONY=%s echo_cooldown=%.0fms "
            "hard_flush=%d_frames min_speech=%.0fms",
            ENABLE_BARGE_IN, TELEPHONY_MODE, TTS_ECHO_COOLDOWN_MS,
            HARD_FLUSH_SILENCE_FRAMES, MIN_SPEECH_MS,
        )
        if TELEPHONY_MODE:
            logger.info(
                "[VAD] TELEPHONY_MODE=True: onset=%d, hold=%d, thr_min=%.1f, rms_floor=%.1f",
                self._speech_onset_min_frames, self._speech_hold_min_frames,
                self.vad._minimum_threshold, self.vad._minimum_rms_floor,
            )

        # Turn manager — tuned for phone call cadence.
        # Longer silence threshold (1400 ms) gives callers time to finish their
        # thought before the agent jumps in.  Grace window 300 ms absorbs
        # trailing syllables.  min_words raised so single-frame noise doesn't
        # trigger a turn.
        self._turn = TurnManager(TurnManagerConfig(
            silence_threshold_ms=1400.0,
            grace_window_ms=300.0,
            min_words_short=2,
            min_words_long=3,
        ))

        # Keyboard sound state — tracks the current thinking-sound task
        self._keyboard_stop: asyncio.Event = asyncio.Event()
        self._keyboard_stop.set()  # start in "stopped" state

    async def start(self) -> None:
        self._running = True
        self._call_active = False
        logger.info("=" * 60)
        logger.info("[PIPELINE] Starting voice pipeline")
        logger.info("=" * 60)

        # Fire-and-forget connection warm-ups so the first user turn doesn't
        # pay TLS handshake cost (~300ms each). Failures are silently ignored.
        warmup_targets = []
        primary_stt = getattr(self.stt, "_primary", None)
        if primary_stt is not None and hasattr(primary_stt, "warmup"):
            warmup_targets.append(("stt_primary", primary_stt.warmup()))
        for name, coro in warmup_targets:
            asyncio.create_task(coro, name=f"warm_{name}")

        self._tasks = [
            asyncio.create_task(self._audio_vad_layer(), name="audio_vad"),
            asyncio.create_task(self._stt_worker(), name="stt_worker"),
            asyncio.create_task(self._conversation_layer(), name="conversation"),
            asyncio.create_task(self._tts_layer(), name="tts"),
        ]
        # Greeting is deferred: it is enqueued only when signal_call_active()
        # is called (i.e. when the first real audio frame arrives from the
        # caller). This prevents phantom greetings and Sarvam credit usage
        # when a WebSocket connection opens without an actual phone call.
        logger.info("[CALL_NOT_CONNECTED] Pipeline started — no call active yet; greeting and all Sarvam API calls are BLOCKED until first audio frame arrives")
        logger.info("[PIPELINE] Layers started — waiting for call signal before greeting")

    async def stop(self) -> None:
        self._running = False
        await self.audio.stop()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("[PIPELINE] All layers stopped")

    async def signal_call_active(self) -> None:
        """Called once when real audio arrives, confirming an active call.

        Generates and enqueues the opening greeting. Guards against duplicate
        calls (reconnect loops) and against calls arriving after pipeline stop.
        Greeting is NEVER generated by start() — only here.
        """
        if self._call_active:
            logger.info("[GREETING_BLOCKED] signal_call_active called again — already active, skipping")
            return
        if not self._running:
            logger.info("[GREETING_BLOCKED] signal_call_active called but pipeline is not running")
            return
        self._call_active = True
        logger.info("[CALL_ANSWERED] First audio frame confirmed — call is active")
        logger.info("[GREETING_ALLOWED] Generating opening greeting")
        opening = await self.conversation.generate_opening()
        try:
            self._tts_queue.put_nowait(Response(
                text=opening.text,
                is_shortcut=True,
                timestamp=time.monotonic(),
            ))
            logger.info("[GREETING_TRIGGER] Opening greeting enqueued to TTS: %r", opening.text)
            logger.info("[PIPELINE] Agent opening: %r", opening.text)
            if self._hub is not None:
                self._hub.emit_nowait({
                    "type": "agent",
                    "text": opening.text,
                    "llm_latency_ms": 0,
                    "first_chunk_ms": 0,
                    "total_latency_ms": 0,
                })
        except asyncio.QueueFull:
            logger.warning("[PIPELINE] Could not enqueue opening greeting")

    async def run_until_shutdown(self) -> None:
        """Run pipeline until Ctrl+C or signal."""
        await self.start()

        shutdown = asyncio.Event()

        def _signal():
            logger.info("[PIPELINE] Shutdown signal received")
            shutdown.set()

        try:
            loop = asyncio.get_running_loop()
            loop.add_signal_handler(signal.SIGINT, _signal)
            loop.add_signal_handler(signal.SIGTERM, _signal)
        except NotImplementedError:
            pass

        try:
            await shutdown.wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await self.stop()

    # -----------------------------------------------------------------------
    # Layer 1: Audio + VAD + Turn Detection
    # -----------------------------------------------------------------------

    async def _audio_vad_layer(self) -> None:
        """
        Captures audio, runs VAD, buffers speech, detects turns.
        When a turn completes, enqueues an AudioSegment for STT.
        NEVER blocks on downstream — uses put_nowait with overflow handling.
        """
        try:
            async for frame in self.audio.frames():
                if not self._running:
                    break
                self._frame_count += 1
                await self._process_vad_frame(frame)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("[AUDIO] Layer crashed")
        finally:
            logger.info("[AUDIO] Layer stopped (%d frames)", self._frame_count)

    @property
    def mic_enabled(self) -> bool:
        return self._mic_enabled

    def set_mic_enabled(self, enabled: bool) -> None:
        """Toggle mic gating. When disabled, frames are discarded before VAD —
        no calibration progress, no speech detection, no barge-in."""
        if enabled == self._mic_enabled:
            return
        self._mic_enabled = enabled
        logger.info("[MIC] %s", "enabled" if enabled else "disabled")
        if not enabled:
            # Drop any in-flight speech buffer so a partial utterance doesn't
            # get committed when the mic comes back on.
            self._reset_vad_state()
            self._speech_frame_streak = 0
        if self._hub is not None:
            self._hub.emit_nowait({"type": "mic_state", "enabled": enabled})

    async def _process_vad_frame(self, frame: bytes) -> None:
        # Mic gate: skip speech detection / barge-in while disabled, but keep
        # advancing VAD calibration on disabled-period ambient. Otherwise the
        # first ~600ms of speech after the user enables mic is silently
        # consumed by the calibration window. is_speech() returns False during
        # calibration, so this never triggers downstream processing.
        if not self._mic_enabled:
            if not self.vad.calibrated:
                self.vad.is_speech(frame)
            return

        raw_speech = self.vad.is_speech(frame)

        # === FULL MUTE while TTS is playing — only allow barge-in ===
        # Barge-in uses a stricter threshold than turn-detection so TTS
        # echo / ambient noise can't masquerade as a sustained interrupt.
        if self._tts_playing:
            if not ENABLE_BARGE_IN:
                return
                    
            loud = self.vad.last_energy > self.vad.barge_in_threshold

            if loud:
                self._speech_frame_streak += 1
            else:
                self._speech_frame_streak = 0

            elapsed = (time.monotonic() - self._tts_start_time) * 1000

            if (
                self._speech_frame_streak >= BARGE_IN_MIN_FRAMES
                and elapsed > 250
            ):
                logger.info(
                    "[BARGE-IN] Interrupting TTS "
                    "frames=%d energy=%.0f threshold=%.0f",
                    self._speech_frame_streak,
                    self.vad.last_energy,
                    self.vad.barge_in_threshold,
                )

                await self.tts.stop()

                self._tts_playing = False
                self._barge_in_occurred = True
                self._vad_mute_until = 0.0
                self._speech_frame_streak = 0

                self._flush_pipeline_queues()
                if (
                    self._current_generation_task
                    and not self._current_generation_task.done()
                    ):
                    self._current_generation_task.cancel()
                    
                self._reset_vad_state()

            else:
                return
            
            # Original logic disabled below:
            # if not ENABLE_BARGE_IN:
            #     return
            #
            # loud = self.vad.last_energy > self.vad.barge_in_threshold
            # if loud:
            #     self._speech_frame_streak += 1
            # else:
            #     self._speech_frame_streak = 0
            #
            # elapsed = (time.monotonic() - self._tts_start_time) * 1000
            # if (self._speech_frame_streak >= BARGE_IN_MIN_FRAMES
            #         and elapsed > BARGE_IN_COOLDOWN_MS):
            #     logger.info("[BARGE-IN] Sustained loud speech (%d frames, energy=%.0f, thr=%.0f) — interrupting TTS",
            #                 self._speech_frame_streak, self.vad.last_energy, self.vad.barge_in_threshold)
            #     await self.tts.stop()
            #     self._tts_playing = False
            #     self._barge_in_occurred = True
            #     self._vad_mute_until = 0.0
            #     self._speech_frame_streak = 0
            #     self._flush_pipeline_queues()
            #     self._reset_vad_state()
            # return
            # PHASE17_DEBUG_DISABLE_BARGE_IN_END

        # === ECHO COOLDOWN after TTS ends naturally ===
        # During this window any captured audio is TTS residue (room echo in
        # local-mic mode, or codec artifacts in telephony). Discard it so it
        # never reaches STT and gets mistaken for user speech.
        if time.monotonic() < self._vad_mute_until:
            if self._audio_buffer:
                logger.info(
                    "[ECHO-GATE] Discarding %d bytes of post-TTS audio "
                    "(%.0fms cooldown remaining)",
                    len(self._audio_buffer),
                    (self._vad_mute_until - time.monotonic()) * 1000,
                )
                self._audio_buffer.clear()
                self._speech_duration_ms = 0.0
                self._turn.flush()
            self._prev_speech = False
            self._speech_onset_frames = 0
            self._silence_frames = 0
            return

        # === Normal VAD processing ===
        if raw_speech:
            self._speech_onset_frames += 1
            self._silence_frames = 0
        else:
            self._silence_frames += 1
            self._speech_onset_frames = 0

        if raw_speech and self._speech_onset_frames < self._speech_onset_min_frames and not self._prev_speech:
            speech = False
        elif not raw_speech and self._silence_frames < self._speech_hold_min_frames and self._prev_speech:
            speech = True
        else:
            speech = raw_speech

        if speech:
            if not self._prev_speech:
                logger.info("[VAD] Speech start")
            self._turn.mark_speech()
            self._audio_buffer.extend(frame)
            self._speech_duration_ms += 20.0

            if len(self._audio_buffer) > MAX_BUFFER_BYTES:
                del self._audio_buffer[:len(self._audio_buffer) - MAX_BUFFER_BYTES]

            proxy_words = max(1, int(self._speech_duration_ms / 300))
            self._turn.update_text(" ".join(["x"] * proxy_words))
        else:
            if self._prev_speech:
                logger.info("[VAD] Speech end (%.0fms)", self._speech_duration_ms)

        self._prev_speech = speech

        # TurnManager OR hard telephony silence flush
        should_flush = self._turn.should_trigger()

        # Hard flush: after enough accumulated speech, a long consecutive
        # silence window triggers a flush even if TurnManager hasn't fired.
        # Using HARD_FLUSH_SILENCE_FRAMES (600 ms) instead of the previous
        # 12-frame (240 ms) threshold to tolerate natural mid-sentence pauses.
        if (
            not speech
            and self._speech_duration_ms >= MIN_SPEECH_MS
            and self._silence_frames >= HARD_FLUSH_SILENCE_FRAMES
        ):
            logger.info(
                "[TURN] Hard-flush: duration=%.0fms silence_frames=%d",
                self._speech_duration_ms, self._silence_frames,
            )
            should_flush = True

        if should_flush:
            logger.info(
                "[TURN] Triggered flush speech_ms=%.0f silence_frames=%d buffer=%d",
                self._speech_duration_ms,
                self._silence_frames,
                len(self._audio_buffer),
            )

            if self._speech_duration_ms < MIN_SPEECH_MS:
                logger.debug(
                    "[TURN] Too short (%.0fms) — discarding",
                    self._speech_duration_ms
                )
                self._audio_buffer.clear()
                self._speech_duration_ms = 0.0
                self._turn.flush()
                return

            segment = AudioSegment(
                audio=bytes(self._audio_buffer),
                duration_ms=self._speech_duration_ms,
                timestamp=time.monotonic(),
            )

            self._audio_buffer.clear()
            self._speech_duration_ms = 0.0
            
            self._turn.flush()

            try:
                self._stt_queue.put_nowait(segment)

                logger.info(
                    "[TURN] Enqueued audio segment (%.0fms)",
                    segment.duration_ms
                )

            except asyncio.QueueFull:
                logger.warning("[TURN] STT queue full — dropping segment")

    def _reset_vad_state(self) -> None:
        self._audio_buffer.clear()
        self._speech_duration_ms = 0.0
        self._prev_speech = False
        self._speech_onset_frames = 0
        self._silence_frames = 0
        self._speech_frame_streak = 0
        self._turn.flush()

    def _flush_pipeline_queues(self) -> None:
        drained = 0
        for q in (self._tts_queue, self._stt_queue, self._transcript_queue):
            while not q.empty():
                try:
                    q.get_nowait()
                    drained += 1
                except asyncio.QueueEmpty:
                    break
        if drained:
            logger.info("[BARGE-IN] Drained %d items from pipeline queues", drained)

    # -----------------------------------------------------------------------
    # Layer 2: STT Worker
    # -----------------------------------------------------------------------

    async def _stt_worker(self) -> None:
        """
        Consumes AudioSegments, transcribes via STT router.
        Runs independently — never blocks the audio layer.
        """
        while self._running:
            try:
                segment = await asyncio.wait_for(
                    self._stt_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                latency_start = time.monotonic()
                # STT always auto-detects. Forcing a language here was the
                # root cause of broken mid-call switching: when locked to
                # Hindi, English audio was transcribed as Devanagari junk,
                # so the language-switch intent never matched. Output language
                # is governed by the conversation engine, not STT input.
                logger.info(
                    "[STT-WORKER] Submitting segment: %.0fms audio (%d bytes) "
                    "queued %.0fms ago",
                    segment.duration_ms,
                    len(segment.audio),
                    (time.monotonic() - segment.timestamp) * 1000,
                )
                transcript_text = await self.stt.transcribe(
                    segment.audio, sample_rate=SAMPLE_RATE, language="unknown",
                )
                stt_latency = (time.monotonic() - latency_start) * 1000

                if not transcript_text or len(transcript_text.strip()) < 2:
                    logger.info("[STT] Empty transcript — skipping")
                    continue

                logger.info("[STT] %r (%.0fms latency)", transcript_text, stt_latency)

                if self._hub is not None:
                    self._hub.emit_nowait({
                        "type": "user",
                        "text": transcript_text.strip(),
                        "stt_latency_ms": round(stt_latency),
                    })

                transcript = Transcript(
                    text=transcript_text.strip(),
                    duration_ms=segment.duration_ms,
                    timestamp=time.monotonic(),
                    stt_latency_ms=stt_latency,
                )

                try:
                    self._transcript_queue.put_nowait(transcript)
                except asyncio.QueueFull:
                    logger.warning("[STT] Transcript queue full — dropping")

            except Exception:
                logger.exception("[STT] Transcription failed")

        logger.info("[STT] Worker stopped")

    # -----------------------------------------------------------------------
    # Layer 3: Conversation Engine
    # -----------------------------------------------------------------------

    async def _conversation_layer(self) -> None:
        """
        Consumes transcripts, routes through conversation engine.
        Uses streaming: sends sentence-level chunks to TTS as they arrive.
        """
        while self._running:
            try:
                transcript = await asyncio.wait_for(
                    self._transcript_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                # Keyboard-click sound disabled — plays mechanical clicking to
                # callers during LLM processing which is unwanted in production.
                # Keep kb_stop/kb_task structure intact so cancellation logic is unchanged.
                kb_stop = asyncio.Event()
                kb_stop.set()  # disable: loop checks stop.is_set() before first write
                kb_task = asyncio.create_task(
                    self._play_keyboard_loop(kb_stop), name="keyboard_sound"
                )

                async def _run_generation():

                    latency_start = time.monotonic()

                    first_result = None
                    first_chunk_ms = 0.0
                    first_chunk_sent = False

                    full_text_parts: list[str] = []

                    async for result in self.conversation.process_stream(
                        transcript.text
                    ):

                        if first_result is None:

                            first_result = result

                            first_chunk_ms = (
                                time.monotonic() - latency_start
                            ) * 1000

                        # Stop keyboard sound on the very first response chunk
                        # (whether a filler or real answer) so audio doesn't
                        # overlap with the agent's spoken reply.
                        if not first_chunk_sent:
                            kb_stop.set()
                            first_chunk_sent = True

                        full_text_parts.append(result.text)

                        try:

                            self._tts_queue.put_nowait(
                                Response(
                                    text=result.text,
                                    is_shortcut=result.is_shortcut,
                                    timestamp=time.monotonic(),
                                )
                            )

                        except asyncio.QueueFull:

                            logger.warning(
                                "[ENGINE] TTS queue full -- dropping chunk"
                            )

                    # Safety: ensure keyboard is stopped even if no chunks
                    kb_stop.set()

                    engine_latency = (
                        time.monotonic() - latency_start
                    ) * 1000

                    total_latency = (
                        transcript.stt_latency_ms
                        + engine_latency
                    )

                    if first_result:

                        full_text = " ".join(full_text_parts)

                        logger.info(
                            "[ENGINE] %r -> %r (%.0fms, first_chunk=%.0fms)",
                            transcript.text,
                            full_text,
                            engine_latency,
                            first_chunk_ms,
                        )

                        if self._hub is not None:
                            self._hub.emit_nowait({
                                "type": "agent",
                                "text": full_text,
                                "llm_latency_ms": round(engine_latency),
                                "first_chunk_ms": round(first_chunk_ms),
                                "total_latency_ms": round(total_latency),
                            })

                self._current_generation_task = asyncio.create_task(
                    _run_generation()
                )

                try:
                    await self._current_generation_task
                finally:
                    # Always stop keyboard, even on cancellation or exception
                    kb_stop.set()
                    if not kb_task.done():
                        kb_task.cancel()

            except asyncio.CancelledError:

                logger.info("[ENGINE] Generation cancelled")

            except Exception:

                logger.exception("[ENGINE] Processing failed")

        logger.info("[ENGINE] Layer stopped")

    # -----------------------------------------------------------------------
    # Keyboard sound — plays while the LLM is thinking
    # -----------------------------------------------------------------------

    async def _play_keyboard_loop(self, stop: asyncio.Event) -> None:
        """Write keyboard-click PCM frames to the audio sink until stop is set.

        Loops the pre-generated burst so the caller hears continuous typing
        during LLM processing.  Stops within one frame (~20 ms) of stop being
        set, so there is no audible overlap with the agent's spoken response.
        """
        sound = _generate_keyboard_sound(SAMPLE_RATE)
        frame_size = SAMPLES_PER_FRAME * 2   # int16 = 2 bytes per sample
        try:
            while not stop.is_set():
                for offset in range(0, len(sound), frame_size):
                    if stop.is_set():
                        return
                    frame = sound[offset : offset + frame_size]
                    if len(frame) < frame_size:
                        frame = frame + b"\x00" * (frame_size - len(frame))
                    await self.sink.write_frame(frame)
                    await asyncio.sleep(0)  # yield to event loop
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("[KEYBOARD] Sound error — skipping")

    # -----------------------------------------------------------------------
    # Layer 4: TTS Playback
    # -----------------------------------------------------------------------

    async def _tts_layer(self) -> None:
        """
        Consumes responses, plays via TTS.
        Tracks playback state for barge-in detection.

        Ensures only one utterance plays at a time: waits for speak()
        to fully complete before starting the next. Stale responses
        (queued >3s ago) are dropped to prevent pile-up.
        """
        while self._running:
            try:
                response = await asyncio.wait_for(
                    self._tts_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            # Drop genuinely-orphaned responses. Cap is intentionally generous:
            # a multi-sentence streamed response can spend tens of seconds in
            # the queue waiting for earlier chunks to finish speaking. Barge-in
            # already drains this queue via _flush_pipeline_queues(), so this
            # cap only fires on genuinely stuck pipelines.
            age_ms = (time.monotonic() - response.timestamp) * 1000
            if age_ms > 30000:
                logger.warning("[TTS] Dropping stale response (%.0fms old): %r",
                               age_ms, response.text[:50])
                continue

            if self._tts_playing:
                logger.info("[TTS] Interrupting previous playback")

                try:
                    await self.tts.stop()
                except Exception:
                    logger.exception("[TTS] stop() failed")

                self._tts_playing = False

                await asyncio.sleep(0.05)

            try:
                logger.info("[TTS] Playing: %r (queued %.0fms ago)", response.text, age_ms)

                self._tts_playing = True
                self._tts_start_time = time.monotonic()

                if hasattr(self.tts, 'set_language'):
                    self.tts.set_language(_detect_tts_language(response.text))

                await self.tts.speak(response.text)

            except asyncio.CancelledError:
                logger.info("[TTS] Interrupted")
            except Exception:
                logger.exception("[TTS] Playback failed — recovering")
                # Reset TTS state so next utterance can play
                try:
                    await self.tts.stop()
                except Exception:
                    pass
            finally:
                self._tts_playing = False
                self._tts_end_time = time.monotonic()
                if self._barge_in_occurred:
                    self._barge_in_occurred = False
                else:
                    self._vad_mute_until = (
                        time.monotonic() + TTS_ECHO_COOLDOWN_MS / 1000.0
                    )

        logger.info("[TTS] Layer stopped")
