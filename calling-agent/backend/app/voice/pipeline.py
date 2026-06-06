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
import re
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
from .conversation import ConversationRouter
from .memory import ConversationMemory
from .event_hub import EventHub

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
# Raised from 3 to 8: streamed LLM responses produce multiple sentence-level
# chunks in quick succession. A queue of 3 caused drops when the TTS layer was
# busy playing an earlier chunk while new ones arrived.
QUEUE_SIZE_TTS = 8

MAX_SPEECH_SECONDS = 4
MAX_BUFFER_BYTES = SAMPLE_RATE * 2 * MAX_SPEECH_SECONDS
# 300ms minimum: short inbound responses like "haan", "yes", "ha" are
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
# Raised 30→50 frames (600→1000 ms): callers speaking Hindi/Gujarati regularly
# pause 400–700 ms between clause groups within a single utterance.  600 ms
# was splitting those utterances, sending 2 short fragments to STT instead of
# one complete sentence.  1000 ms still catches end-of-turn pauses (>1s) while
# tolerating normal intra-utterance breathing pauses.
HARD_FLUSH_SILENCE_FRAMES = 50

# Fallback phrases played when STT returns empty on substantial speech,
# or when TTS receives an empty string or fails to play (feature 2 & 5).
_STT_FALLBACK_PHRASES: dict[str, str] = {
    "hi": "माफ़ कीजिए, आपकी आवाज़ स्पष्ट नहीं आई। कृपया दोबारा बताइए।",
    "gu": "માફ કરશો, તમારો અવાજ સ્પષ્ટ સંભળાયો નથી. કૃપા કરીને ફરી કહેશો?",
    "en": "Sorry, I couldn't hear you clearly. Could you please repeat that?",
}
# Minimum speech duration that triggers a language-fallback response on empty STT
_STT_FALLBACK_MIN_SPEECH_MS = 800.0


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
        conversation: ConversationRouter | None = None,
        event_hub: EventHub | None = None,
        mic_enabled: bool = True,
        skip_greeting: bool = False,
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
        self.conversation = conversation or ConversationRouter()
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
        self._tasks: list[asyncio.Task] = []
        self._current_generation_task: asyncio.Task | None = None
        self._tts_playing = False
        self._tts_start_time = 0.0
        self._tts_end_time = 0.0
        self._vad_mute_until = 0.0
        self._barge_in_occurred = False
        self._mic_enabled = mic_enabled
        # Issue 4: skip greeting on reconnect — caller already heard it
        self._skip_greeting: bool = skip_greeting
        # Warmup gate — set after all provider warmups complete (feature 6)
        self._warmup_done: asyncio.Event = asyncio.Event()
        # Greeting guard — False until the opening TTS utterance has fully
        # played.  STT fallback phrases and barge-in are suppressed until this
        # is True so startup noise / TTS echo never produce spurious output.
        # When skip_greeting=True we mark it done immediately so barge-in
        # and STT fallback are active from the start.
        self._greeting_done: bool = skip_greeting

        # VAD + buffering state
        self._audio_buffer = bytearray()
        self._speech_duration_ms = 0.0
        self._prev_speech = False
        self._speech_frame_streak = 0
        self._frame_count = 0
        self._speech_onset_frames = 0  # debounce: frames since speech started
        self._silence_frames = 0       # debounce: frames since last speech
        self._pre_speech_buffer: list[bytes] = []  # look-back to recover onset clip

        # Diagnostic counters — logged at session end to trace audio path
        self._vad_frames_seen: int = 0
        self._vad_speech_frames: int = 0
        self._segments_created: int = 0
        self._segments_sent_to_stt: int = 0

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

    async def start(self) -> None:
        self._running = True
        logger.info("=" * 60)
        logger.info("[PIPELINE] Starting voice pipeline")
        logger.info("=" * 60)

        # Await all provider warmups before starting the pipeline layers so the
        # first live caller turn never pays TLS/API handshake cost (feature 6).
        # Issue 3: only log "queued" for warmups that will actually run
        # (guarded by class-level _warmup_done_flag inside each provider).
        warmup_coros = []
        primary_stt = getattr(self.stt, "_primary", None)
        if primary_stt is not None and hasattr(primary_stt, "warmup"):
            if not getattr(primary_stt.__class__, "_warmup_done_flag", False):
                logger.info("[WARMUP] STT primary warmup queued")
            warmup_coros.append(primary_stt.warmup())
        if hasattr(self.tts, "warmup"):
            if not getattr(self.tts.__class__, "_warmup_done_flag", False):
                logger.info("[WARMUP] TTS warmup queued")
            warmup_coros.append(self.tts.warmup())
        if hasattr(self.conversation, "warmup"):
            if not getattr(self.conversation.__class__, "_warmup_done_flag", False):
                logger.info("[WARMUP] LLM warmup queued")
            warmup_coros.append(self.conversation.warmup())

        if warmup_coros:
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*warmup_coros, return_exceptions=True),
                    timeout=5.0,
                )
                errs = [r for r in results if isinstance(r, Exception)]
                if errs:
                    for e in errs:
                        logger.warning("[WARMUP] Error: %s", e)
            except asyncio.TimeoutError:
                logger.warning("[WARMUP] Timed out after 5s — proceeding anyway")
        self._warmup_done.set()

        self._tasks = [
            asyncio.create_task(self._audio_vad_layer(), name="audio_vad"),
            asyncio.create_task(self._stt_worker(), name="stt_worker"),
            asyncio.create_task(self._conversation_layer(), name="conversation"),
            asyncio.create_task(self._tts_layer(), name="tts"),
        ]

        # Issue 4: skip greeting on reconnect — caller already heard it.
        if self._skip_greeting:
            logger.info("[PIPELINE] Greeting suppressed — already greeted this call (reconnect)")
            return

        # Wait for the first inbound audio frame before playing the greeting.
        # With mod_audio_stream the WebSocket connects when the dialplan executes
        # the application — which may be before FreeSWITCH sends CHANNEL_ANSWER.
        # Waiting for the first real audio frame guarantees the call is live.
        if hasattr(self.audio, '_pushed_frames'):
            logger.info("[PIPELINE] Waiting for first audio frame to confirm call is established...")
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if getattr(self.audio, '_pushed_frames', 0) > 0:
                    logger.info("[PIPELINE] First audio frame received — call established, queuing greeting")
                    break
                await asyncio.sleep(0.025)
            else:
                logger.warning("[PIPELINE] No audio in 5s — playing greeting anyway (call may not be answered yet)")

        # Agent speaks first — bilingual greeting
        opening = await self.conversation.generate_opening()
        try:
            self._tts_queue.put_nowait(Response(
                text=opening.text,
                is_shortcut=True,
                timestamp=time.monotonic(),
            ))
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

    async def stop(self) -> None:
        self._running = False
        await self.audio.stop()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("[PIPELINE] All layers stopped")

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
            logger.info(
                "[AUDIO] Layer stopped: frames=%d vad_seen=%d vad_speech=%d "
                "segments_created=%d segments_sent_to_stt=%d",
                self._frame_count, self._vad_frames_seen, self._vad_speech_frames,
                self._segments_created, self._segments_sent_to_stt,
            )

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

        self._vad_frames_seen += 1
        raw_speech = self.vad.is_speech(frame)
        if raw_speech:
            self._vad_speech_frames += 1

        # === FULL MUTE while TTS is playing — only allow barge-in ===
        # Barge-in uses a stricter threshold than turn-detection so TTS
        # echo / ambient noise can't masquerade as a sustained interrupt.
        if self._tts_playing:
            # Issue 4: never allow barge-in during the opening greeting.
            # The greeting echo (via mod_audio_stream) is often loud enough to
            # trip the barge-in threshold, cutting the greeting short and leaving
            # no echo-cooldown window.  Once _greeting_done is True, barge-in
            # is subject to the normal ENABLE_BARGE_IN flag.
            if not ENABLE_BARGE_IN or not self._greeting_done:
                if not self._greeting_done:
                    logger.debug("[BARGE-IN] Suppressed — greeting not yet complete")
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
            # Keep a 2-frame look-back so onset clip is recoverable
            self._pre_speech_buffer.append(frame)
            if len(self._pre_speech_buffer) > SPEECH_ONSET_MIN_FRAMES:
                self._pre_speech_buffer.pop(0)

        if raw_speech and self._speech_onset_frames < self._speech_onset_min_frames and not self._prev_speech:
            speech = False
        elif not raw_speech and self._silence_frames < self._speech_hold_min_frames and self._prev_speech:
            speech = True
        else:
            speech = raw_speech

        if speech:
            if not self._prev_speech:
                logger.info("[VAD] Speech start")
                # Prepend buffered pre-onset frames to recover clipped onset
                if self._pre_speech_buffer:
                    for pre_frame in self._pre_speech_buffer:
                        self._audio_buffer.extend(pre_frame)
                        self._speech_duration_ms += 20.0
                    self._pre_speech_buffer.clear()
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
        # Using HARD_FLUSH_SILENCE_FRAMES (1000 ms) to tolerate natural
        # intra-utterance pauses common in Hindi/Gujarati speech.
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
            self._segments_created += 1

            self._audio_buffer.clear()
            self._speech_duration_ms = 0.0

            self._turn.flush()

            try:
                self._stt_queue.put_nowait(segment)
                self._segments_sent_to_stt += 1

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
        self._pre_speech_buffer.clear()
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
                # Pass the locked conversation language to Sarvam to prevent
                # auto-detect misfires (Bengali/Punjabi/Malayalam/Marathi on
                # Gujarati/Hindi audio). "unknown" is only used pre-lock (first
                # turn / GREETING stage) to allow free language detection.
                # English is also passed explicitly when locked — Sarvam handles
                # English-IN well and it prevents misclassification on accented
                # English with Indian words.
                _stt_lang = "unknown"
                # Always use unknown for auto-detection — prevents the feedback loop
                # where language_locked forces STT to a specific language, causing
                # cross-language transcripts to be mis-detected and keeping the lock
                # active. Sarvam's auto-detection on each turn enables true dynamic
                # language switching as required.
                logger.debug("[STT-WORKER] stt_lang=unknown (auto-detect every turn)")
                _seg_samples = np.frombuffer(segment.audio, dtype=np.int16).astype(np.float32)
                _seg_rms = float(np.sqrt(np.mean(_seg_samples ** 2))) if len(_seg_samples) else 0.0
                logger.info(
                    "[STT-WORKER] Submitting segment: %.0fms audio (%d bytes) "
                    "queued %.0fms ago lang=%s rms=%.0f",
                    segment.duration_ms,
                    len(segment.audio),
                    (time.monotonic() - segment.timestamp) * 1000,
                    _stt_lang,
                    _seg_rms,
                )
                stt_result = await self.stt.transcribe(
                    segment.audio, sample_rate=SAMPLE_RATE, language=_stt_lang,
                )
                transcript_text = stt_result.text
                stt_latency = (time.monotonic() - latency_start) * 1000

                # Update conversation language from Sarvam's native detection result.
                # Applied on every turn — no lock. Enables truly dynamic language switching.
                if (
                    stt_result.detected_language
                    and self.conversation
                    and transcript_text
                ):
                    _bcp47_to_short = {"en-IN": "en", "hi-IN": "hi", "gu-IN": "gu"}
                    short = _bcp47_to_short.get(stt_result.detected_language)
                    if short:
                        logger.info(
                            "[STT] Language auto-detected by Sarvam: %s → %s",
                            stt_result.detected_language, short,
                        )
                        self.conversation.state.language = short
                        # Do NOT lock — allow every utterance to update language dynamically

                # Duration-aware plausibility: natural speech maxes at ~4 words/sec.
                # A transcript with far more words than the audio can contain is a
                # hallucination (STT filling silence with fluent-sounding text).
                # Threshold: 250 ms/word (4 wps) with a floor of 4 words for short segments.
                if transcript_text and transcript_text.strip():
                    _word_count = len(transcript_text.split())
                    _max_plausible = max(4, int(segment.duration_ms / 250))
                    if _word_count > _max_plausible * 2:
                        logger.warning(
                            "[STT] Rejecting implausible transcript: %d words in %.0fms "
                            "(max_plausible=%d) — likely hallucination: %r",
                            _word_count, segment.duration_ms, _max_plausible,
                            transcript_text[:80],
                        )
                        transcript_text = ""
                    elif _word_count > _max_plausible:
                        logger.warning(
                            "[STT] Suspicious word count: %d words in %.0fms "
                            "(max_plausible=%d): %r",
                            _word_count, segment.duration_ms, _max_plausible,
                            transcript_text[:80],
                        )

                # Guard against very short segments producing a single ambiguous word
                # that could spuriously trigger location/name/budget extraction.
                # Segments < 700ms yielding a single word are more likely noise than
                # meaningful input — unless the segment is clearly an affirmation/negation
                # (yes/no/haan/ji etc.) which must pass through for flow logic to work.
                if (
                    transcript_text
                    and len(transcript_text.split()) == 1
                    and segment.duration_ms < 700.0
                ):
                    _single = transcript_text.strip().lower()
                    _affirmation_negation = {
                        "yes", "yeah", "yep", "ok", "okay", "sure",
                        "haan", "ha", "ji", "haan", "na", "nahi", "no",
                        "nope", "theek", "barobar", "aavjo",
                        "हां", "हाँ", "जी", "ठीक", "हा", "ना", "नहीं",
                        "હા", "જી", "ઠીક", "ના", "નહીં",
                    }
                    if _single not in _affirmation_negation:
                        logger.info(
                            "[STT-WORKER] Single-word fragment %r from %.0fms segment — "
                            "below duration threshold, skipping",
                            transcript_text, segment.duration_ms,
                        )
                        continue

                if not transcript_text or len(transcript_text.strip()) < 2:
                    # Unsupported-language recovery: play a language-appropriate
                    # "please repeat" phrase when the user clearly spoke but STT
                    # produced nothing usable.
                    #
                    # ALL four guards must pass before queuing the fallback
                    # (Issues 1 & 2):
                    #   1. Duration ≥ threshold — actual user speech, not a click
                    #   2. _greeting_done     — greeting has finished playing
                    #   3. not _tts_playing   — agent is not currently speaking
                    #   4. _vad_mute_until elapsed — not inside echo-cooldown window
                    if segment.duration_ms >= _STT_FALLBACK_MIN_SPEECH_MS:
                        if not self._greeting_done:
                            logger.info(
                                "[STT] Skipping fallback — greeting not yet complete "
                                "(startup/warmup audio, %.0fms)", segment.duration_ms,
                            )
                        elif self._tts_playing:
                            logger.info(
                                "[STT] Skipping fallback — agent is currently speaking "
                                "(%.0fms segment)", segment.duration_ms,
                            )
                        elif time.monotonic() < self._vad_mute_until:
                            logger.info(
                                "[STT] Skipping fallback — inside TTS echo-cooldown window "
                                "(%.0fms segment, %.0fms remaining)",
                                segment.duration_ms,
                                (self._vad_mute_until - time.monotonic()) * 1000,
                            )
                        else:
                            lang = self.conversation.state.language if self.conversation else "en"
                            fallback = _STT_FALLBACK_PHRASES.get(lang, _STT_FALLBACK_PHRASES["en"])
                            logger.info(
                                "[STT] Unrecognized speech (%.0fms, lang=%s) — queuing language-fallback phrase",
                                segment.duration_ms, lang,
                            )
                            try:
                                self._tts_queue.put_nowait(Response(
                                    text=fallback,
                                    is_shortcut=True,
                                    timestamp=time.monotonic(),
                                ))
                            except asyncio.QueueFull:
                                logger.warning("[STT] TTS queue full — cannot play language-fallback phrase")
                    else:
                        logger.info("[STT] Empty transcript (%.0fms) — below fallback threshold, skipping",
                                    segment.duration_ms)
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

            # Drain any newer transcripts — only process the most recent one.
            # When the caller speaks multiple times while the agent was processing
            # (or barge-in produced several quick segments), responding to stale
            # earlier transcripts causes the agent to reply out of context.
            # Note: all drained transcripts had their QA observer called in the STT
            # worker already; we only skip conversation engine processing here.
            while not self._transcript_queue.empty():
                try:
                    newer = self._transcript_queue.get_nowait()
                    logger.info(
                        "[ENGINE] Skipping stale transcript %r in favor of: %r",
                        transcript.text[:50], newer.text[:50],
                    )
                    transcript = newer
                except asyncio.QueueEmpty:
                    break

            try:
                async def _run_generation():

                    latency_start = time.monotonic()

                    first_result = None
                    first_chunk_ms = 0.0

                    full_text_parts: list[str] = []
                    # Collect LLM (non-shortcut) sentence chunks to combine
                    # into a single TTS call, preventing per-sentence gaps.
                    llm_parts: list[str] = []

                    async for result in self.conversation.process_stream(
                        transcript.text
                    ):

                        if first_result is None:

                            first_result = result

                            first_chunk_ms = (
                                time.monotonic() - latency_start
                            ) * 1000

                        full_text_parts.append(result.text)

                        if result.is_shortcut:
                            # Fillers, shortcuts, language-switch confirmations
                            # go to TTS immediately for low latency.
                            try:
                                self._tts_queue.put_nowait(
                                    Response(
                                        text=result.text,
                                        is_shortcut=True,
                                        timestamp=time.monotonic(),
                                    )
                                )
                            except asyncio.QueueFull:
                                logger.warning(
                                    "[ENGINE] TTS queue full -- dropping shortcut chunk"
                                )
                        else:
                            # Buffer LLM sentence chunks; sent combined below.
                            llm_parts.append(result.text)

                    # Send all LLM sentences as ONE TTS request to produce a
                    # single coherent utterance instead of fragmented pieces.
                    if llm_parts:
                        combined_text = " ".join(llm_parts)
                        try:
                            self._tts_queue.put_nowait(
                                Response(
                                    text=combined_text,
                                    is_shortcut=False,
                                    timestamp=time.monotonic(),
                                )
                            )
                        except asyncio.QueueFull:
                            logger.warning(
                                "[ENGINE] TTS queue full -- dropping combined LLM response"
                            )

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

                await self._current_generation_task

            except asyncio.CancelledError:

                logger.info("[ENGINE] Generation cancelled")

            except Exception:

                logger.exception("[ENGINE] Processing failed")

                try:
                    self._tts_queue.put_nowait(Response(
                        text="Sorry, could you say that again?",
                        is_shortcut=True,
                        timestamp=time.monotonic(),
                    ))
                except asyncio.QueueFull:
                    pass

        logger.info("[ENGINE] Layer stopped")

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
                # Silence protection (feature 5): never send blank text to TTS
                if not response.text.strip():
                    lang = self.conversation.state.language if self.conversation else "en"
                    fallback = _STT_FALLBACK_PHRASES.get(lang, _STT_FALLBACK_PHRASES["en"])
                    logger.warning("[TTS] Empty response text — substituting fallback phrase (lang=%s)", lang)
                    response = Response(
                        text=fallback,
                        is_shortcut=True,
                        timestamp=response.timestamp,
                    )

                # Preprocess text to avoid gibberish: normalize punctuation that
                # can confuse Sarvam TTS (em-dashes produce audio glitches).
                tts_text = response.text
                tts_text = tts_text.replace("—", ", ").replace("–", ", ")
                tts_text = tts_text.replace("…", ".").replace("…", ".")
                tts_text = tts_text.replace("•", "").replace("·", "")
                # Collapse multiple spaces/newlines that may appear in LLM output
                tts_text = re.sub(r'\s+', ' ', tts_text).strip()

                logger.info("[TTS] Playing: %r (queued %.0fms ago)", tts_text, age_ms)

                self._tts_playing = True
                self._tts_start_time = time.monotonic()

                if hasattr(self.tts, 'set_language') and self.conversation:
                    self.tts.set_language(self.conversation.state.language)

                await self.tts.speak(tts_text)

            except asyncio.CancelledError:
                logger.info("[TTS] Interrupted")
            except Exception:
                logger.exception("[TTS] Playback failed — recovering")
                # Reset TTS state so next utterance can play
                try:
                    await self.tts.stop()
                except Exception:
                    pass
                # Silence protection (feature 5): play fallback phrase to avoid dead air
                lang = self.conversation.state.language if self.conversation else "en"
                fallback = _STT_FALLBACK_PHRASES.get(lang, _STT_FALLBACK_PHRASES["en"])
                logger.info("[TTS] Playing silence-protection fallback phrase (lang=%s)", lang)
                try:
                    await self.tts.speak(fallback)
                except Exception:
                    logger.warning("[TTS] Fallback phrase also failed — accepting silence")
            finally:
                self._tts_playing = False
                self._tts_end_time = time.monotonic()

                # Issue 4 / Issue 1: mark greeting as done after the very first
                # TTS utterance finishes.  From this point barge-in and STT
                # fallback phrases are permitted.
                if not self._greeting_done:
                    self._greeting_done = True
                    logger.info("[PIPELINE] Greeting complete — barge-in and STT fallback now active")

                if self._barge_in_occurred:
                    self._barge_in_occurred = False
                else:
                    # Issue 3: In telephony mode the echo of TTS audio returns
                    # via mod_audio_stream with a delay of:
                    #   injection_overhead + audio_playback_duration
                    # The old 400 ms window expired long before the echo arrived,
                    # so agent speech leaked into STT.  Use the actual audio
                    # duration from TelephonySink (if available) to cover the
                    # full echo window.
                    audio_dur = getattr(self.sink, 'last_flush_duration', 0.0)
                    if audio_dur > 0.0:
                        # 700 ms covers injection pipeline overhead; audio_dur
                        # covers the full playback window; 200 ms is safety buffer.
                        mute_s = 0.7 + audio_dur + 0.2
                        logger.info(
                            "[ECHO-MUTE] Extended window %.1fs "
                            "(audio=%.1fs + 0.7s injection + 0.2s buffer)",
                            mute_s, audio_dur,
                        )
                    else:
                        mute_s = TTS_ECHO_COOLDOWN_MS / 1000.0
                    self._vad_mute_until = time.monotonic() + mute_s

        logger.info("[TTS] Layer stopped")
