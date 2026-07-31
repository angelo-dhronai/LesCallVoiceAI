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

# Streaming STT canary (default OFF): when the STT router's primary is a
# SarvamStreamingSTT (installed by the bridge under the same flag), speech
# frames are ALSO fed to Sarvam's WebSocket while the caller talks, so the
# transcript is near-ready when the endpoint fires instead of paying the
# full batch round-trip afterwards. The local buffer and every batch-path
# safety net stay in place — the stream self-downgrades to batch on any
# failure.
ENABLE_STREAMING_STT = (
    os.environ.get("ENABLE_STREAMING_STT", "false").lower() == "true")

# VAD engine (deploy/PLAN-vad-stack.md): "energy" = legacy threshold VAD,
# "fsmn" = WebRTC pre-filter → FSMN-VAD model stack + EndpointController +
# audio-event classifier + shadow session state machine. Rollback is this
# one env var.
VAD_ENGINE = os.environ.get("VAD_ENGINE", "energy").strip().lower()
TELEPHONY_MODE = os.environ.get("TELEPHONY_MODE", "false").lower() == "true"


@dataclass
class AudioSegment:
    """A complete speech segment ready for STT."""
    audio: bytes
    duration_ms: float
    timestamp: float
    # Monotonic timestamps of the caller's actual speech onset/end for this
    # segment (continuation-merge timing anchors; 0.0 = unknown).
    speech_start_ts: float = 0.0
    speech_end_ts: float = 0.0


@dataclass
class Transcript:
    """STT result ready for conversation engine."""
    text: str
    duration_ms: float
    timestamp: float
    stt_latency_ms: float = 0.0
    # Carried through from AudioSegment (continuation-merge timing anchors).
    speech_start_ts: float = 0.0
    speech_end_ts: float = 0.0
    # Derived STT confidence (0..1) from the router; None = unknown.
    confidence: "float | None" = None
    # Which STT path produced the text ("primary" | "primary-script-retry" |
    # "fallback"). Recovered paths are mishear-prone: they never vote on the
    # conversation language (A3, 2026-07-30).
    stt_source: str = "primary"


@dataclass
class Response:
    """Engine response ready for TTS."""
    text: str
    is_shortcut: bool
    timestamp: float
    # True only for filler utterances ("एक सेकंड…").  Used by the TTS layer to
    # grant the filler — and the real response that follows it — a longer
    # barge-in guard so the filler's own echo can't cancel the real response.
    is_filler: bool = False
    # True for utterances that exist because the caller missed something
    # (hello replays, the connect intro, watchdog reprompts, the greeting).
    # The connect delivery guard protects these from hello barge-ins at any
    # call age, not just inside CONNECT_WINDOW_S.
    protected: bool = False
    # Short language code ("en"/"hi"/"gu") stamped when the text was chosen,
    # so the accent is decided with the words — not re-read from mutable
    # conversation state at dequeue time (a mid-queue language flip once spoke
    # a fully-English reply as hi-IN). None → derived from the text itself in
    # _resolve_tts_language.
    language: str | None = None
    # A5: replay of the agent's previous utterance (early-"hello?" repair /
    # repeat request). A replay exists to fill a silence — if a NEWER
    # response started playing after this replay was created, the silence is
    # gone and the replay must be dropped, not double-spoken.
    replay: bool = False


QUEUE_SIZE_AUDIO = 40
QUEUE_SIZE_STT = 3
QUEUE_SIZE_TRANSCRIPT = 3
# Raised from 3 to 8: streamed LLM responses produce multiple sentence-level
# chunks in quick succession. A queue of 3 caused drops when the TTS layer was
# busy playing an earlier chunk while new ones arrived.
QUEUE_SIZE_TTS = 8

MAX_SPEECH_SECONDS = 25
MAX_BUFFER_BYTES = SAMPLE_RATE * 2 * MAX_SPEECH_SECONDS
# 300ms minimum: short inbound responses like "haan", "yes", "ha" are
# valid single-word answers that must reach STT. 300ms is enough audio to
# distinguish real speech from a click/breath without missing short replies.
MIN_SPEECH_MS = 300.0
# (removed 2026-07-23: BARGE_IN_COOLDOWN_MS was dead — the live guard is
# BARGE_IN_MIN_ELAPSED_MS below; audits kept grabbing the wrong knob)
# 5 sustained frames (100ms) before barge-in fires. 3 frames (60ms) was
# too hair-trigger: brief noise or the tail of a TTS echo could satisfy it
# and cut the agent mid-sentence. 5 frames still catches a genuine
# interrupt within the first spoken syllable (~100-150ms).
BARGE_IN_MIN_FRAMES = 5

# Barge-in grace for the utterance that immediately FOLLOWS a filler
# ("एक सेकंड…", "One moment…").  A filler's own audio echoes back through the
# telephony round-trip (~700ms injection + the filler's playback duration) and
# lands *during* the real response that plays right after it.  Those echo frames
# are loud and fresh, so they trip barge-in and cancel the real response — the
# caller hears only the filler, then silence.  The normal 250ms guard is far too
# short to skip that echo.  For the post-filler utterance only (and the filler
# itself) we require this much elapsed time before barge-in can fire, which
# covers the filler-echo window.  Every other utterance keeps the 250ms guard,
# so normal barge-in responsiveness is unchanged.
BARGE_IN_MIN_ELAPSED_MS = 250.0
FILLER_ECHO_GRACE_MS = 1500.0

# Echo cooldown after TTS ends naturally: prevents residual TTS audio from
# being re-captured by the microphone and sent to STT as "user speech".
# In telephony mode the TTS speaker and caller mic are on separate paths.
# In local-mic mode: reduced from 1500ms to 600ms. 1500ms was swallowing the
# caller's first 3-4 words when they responded immediately after the agent.
# Real acoustic echo decays within 100-300ms in headphone/normal setups.
# The echo gate now also exits early if sustained real speech is detected
# (see ECHO_EARLY_EXIT_FRAMES below), providing a second layer of protection.
TTS_ECHO_COOLDOWN_MS = float(os.environ.get(
    "TTS_ECHO_COOLDOWN_MS", "400" if TELEPHONY_MODE else "600"))

# Minimum consecutive loud frames during the echo cooldown window that signal
# real caller speech rather than echo. Echo is short-lived (1-3 frames);
# a human speaking produces sustained energy well above this threshold.
ECHO_EARLY_EXIT_FRAMES = int(os.environ.get("ECHO_EARLY_EXIT_FRAMES", "5"))

# Greeting-window softening: the caller's post-greeting "hello" is the most
# predictable sustained-speech event of the call, so while the greeting is the
# utterance being guarded (not _greeting_done yet) the echo gate exits earlier
# and the mute tail is shorter. All later windows keep the defaults above.
GREETING_ECHO_EARLY_EXIT_FRAMES = int(os.environ.get(
    "GREETING_ECHO_EARLY_EXIT_FRAMES", "3"))
GREETING_ECHO_TAIL_MS = float(os.environ.get("GREETING_ECHO_TAIL_MS", "100"))

# Onset lowered 3→2 frames: catch speech sooner so the very first word after
# a pause isn't clipped. Hold kept at 10 for noise immunity.
SPEECH_ONSET_MIN_FRAMES = 2
SPEECH_HOLD_MIN_FRAMES = 10

# Hard-flush silence gate (frames of consecutive silence after hold expires).
# LATENCY: this is dead air the caller hears after they stop talking before the
# agent even begins to think. 560 ms (28 frames) still tolerates a breath
# pause; any mid-sentence split it does cause is re-merged by
# TRANSCRIPT_MERGE_WINDOW_MS below (which now only waits when a follow-on can
# actually exist), and the continuation-merge feature (when enabled) recovers
# genuinely long pauses. With ENABLE_CONTINUATION_MERGE live, 20 (400 ms) is
# the intended tier-b value.
# (History: 30→45→55 crept up to fight splits, then 35; env-tunable now.)
HARD_FLUSH_SILENCE_FRAMES = int(os.environ.get("HARD_FLUSH_SILENCE_FRAMES", "28"))

# TurnManager backstop threshold (see __init__); hard-flush usually wins.
TURN_SILENCE_THRESHOLD_MS = float(os.environ.get("TURN_SILENCE_THRESHOLD_MS", "1200"))

# Debounce window (ms) after the first transcript arrives before firing the LLM.
# Catches follow-on STT segments from the same split utterance so they can be
# merged into one turn rather than generating two separate agent responses.
TRANSCRIPT_MERGE_WINDOW_MS = float(os.environ.get("TRANSCRIPT_MERGE_WINDOW_MS", "550"))
# Skip the merge wait entirely when no follow-on segment can exist (nothing
# queued, nothing in STT, caller silent) — removes a fixed ~550ms per turn.
TRANSCRIPT_MERGE_SKIP_WHEN_IDLE = os.environ.get(
    "TRANSCRIPT_MERGE_SKIP_WHEN_IDLE", "true").lower() == "true"

# Post-response continuation window (ms). If TTS completed cleanly and a short
# fragment (≤ 4 words) arrives within this window, it is likely a trailing
# clause of the previous caller turn ("for that", "yes, and also..."). Prepend
# the previous turn's text so the LLM has full context.
POST_RESPONSE_MERGE_MS = 800.0

# ── Continuation merge (pause-tolerant turns) ─────────────────────────────
# A caller who pauses mid-utterance gets a fast reply to the partial input;
# if they resume speaking within CONTINUATION_WINDOW_MS of their own speech
# end, the reply is cut, the router rolls the turn back, and ONE merged
# utterance is re-run (see conversation.begin_turn_transaction). OFF by
# default until validated on live calls; when OFF, behavior is identical to
# before, including the legacy Case-B fragment merge.
ENABLE_CONTINUATION_MERGE = os.environ.get(
    "ENABLE_CONTINUATION_MERGE", "false").lower() == "true"
CONTINUATION_WINDOW_MS = float(os.environ.get("CONTINUATION_WINDOW_MS", "2000"))
# Relaxed hearing during the bounded post-endpoint window: barge-in energy
# factor (vs the normal 2.0× echo-reject threshold) and sustained-frame
# requirements. Outside the window today's constants apply unchanged.
CONTINUATION_BARGE_FACTOR = float(os.environ.get("CONTINUATION_BARGE_FACTOR", "1.3"))
CONTINUATION_BARGE_FRAMES = int(os.environ.get("CONTINUATION_BARGE_FRAMES", "5"))
CONTINUATION_ECHO_EXIT_FRAMES = int(os.environ.get("CONTINUATION_ECHO_EXIT_FRAMES", "4"))
# Cap on consecutive rollback-merges per chain (background-noise guard).
CONTINUATION_MAX_MERGES = int(os.environ.get("CONTINUATION_MAX_MERGES", "3"))

# ── Connect window (repeated-"hello" loop counter) ──────────────────────────
# In the first CONNECT_WINDOW_S of a call the caller's patience is ~2s and a
# "hello?" is the most likely utterance. Three protections apply, each with
# its own kill switch and a per-agent opt-out via call_flow.connect:
#   delivery guard — a barge-in against a reply the caller has NOT heard yet
#     (nothing flushed, or < CONNECT_MIN_PLAYED_S played) is deferred until
#     STT shows the interruption was more than a bare hello. Replayed /
#     intro utterances (Response.protected) get the guard at any call age.
#   force-language — short first segments skip lang=unknown (bare "hello"
#     routinely misdetects as Odia/Kannada, doubling STT latency exactly on
#     the loop's critical turns).
#   lower min-speech — a clipped 240ms "hello" must still reach STT.
CONNECT_DELIVERY_GUARD = os.environ.get(
    "CONNECT_DELIVERY_GUARD",
    "true" if TELEPHONY_MODE else "false").lower() == "true"
CONNECT_WINDOW_S = float(os.environ.get("CONNECT_WINDOW_S", "12"))
CONNECT_MIN_PLAYED_S = float(os.environ.get("CONNECT_MIN_PLAYED_S", "1.0"))
CONNECT_MIN_SPEECH_MS = float(os.environ.get("CONNECT_MIN_SPEECH_MS", "200"))
CONNECT_FORCE_LANG = os.environ.get(
    "CONNECT_FORCE_LANG", "true").lower() == "true"
CONNECT_SHORT_SEG_MS = float(os.environ.get("CONNECT_SHORT_SEG_MS", "800"))
# Empty-transcript streak damper: speak "sorry, couldn't hear you" only on
# the SECOND consecutive empty (first stays silent, later ones defer to the
# dead-air watchdog). Four sorries in a row once fed an echo→empty→sorry
# self-loop.
EMPTY_STREAK_DAMPER = os.environ.get(
    "EMPTY_STREAK_DAMPER", "true").lower() == "true"
# TTS accent consistency (PLAN-tts-accent-consistency.md).
# TTS_LANG_FROM_TEXT: the target_language_code sent to Sarvam is derived from
# the text actually being spoken (script/marker detection) instead of raw
# conversation state — English replies were going out hi-IN ("rural accent").
# LANG_MIRROR_GUARDED: Sarvam's per-segment language detection updates
# conversation state only when the transcript survives validation AND the
# claim is consistent with the transcript's own script/markers — a bare
# "hello" misdetected as Odia, or Indian-accented English tagged hi-IN, no
# longer flips the agent's language mid-call.
TTS_LANG_FROM_TEXT = os.environ.get(
    "TTS_LANG_FROM_TEXT", "true").lower() == "true"
LANG_MIRROR_GUARDED = os.environ.get(
    "LANG_MIRROR_GUARDED", "true").lower() == "true"
# A4a (2026-07-30): once the conversation language has been stable for
# STT_LANG_PIN_TURNS accepted transcripts, pin the streaming STT to it —
# per-segment auto-detect rendered short Hindi in Punjabi/Bengali/Kannada
# script 29×/day, each costing a 2-4s retry/fallback chain and often an
# English mishear. Unpins the moment conversation state moves off the
# pinned language (explicit request or evidence switch), so genuine
# switching is never locked out. false = always auto-detect (legacy).
STT_LANG_PIN = os.environ.get("STT_LANG_PIN", "true").lower() == "true"
STT_LANG_PIN_TURNS = int(os.environ.get("STT_LANG_PIN_TURNS", "2"))
# B2 (2026-07-30): spelled contact capture. While the conversation is
# capturing an email/phone the merge window stretches so letter-by-letter
# spelling with natural pauses aggregates into one turn instead of three
# separately-answered fragments.
CAPTURE_MODE = os.environ.get("CAPTURE_MODE", "true").lower() == "true"
CAPTURE_MERGE_WINDOW_MS = float(os.environ.get(
    "CAPTURE_MERGE_WINDOW_MS", "2000"))
# H2 (2026-07-31): the barge-in queue flush used to reset_turn() the
# streaming STT, discarding echo-gate-vetted caller audio fed before the
# barge threshold tripped (live: "cleaning and helping…" and a spelled
# email fragment both vanished this way). The interrupting speech IS the
# next turn — keep it. false = legacy discard.
BARGE_KEEP_STREAM_AUDIO = os.environ.get(
    "BARGE_KEEP_STREAM_AUDIO", "true").lower() == "true"
# H5 (2026-07-31): a transcript ending in a continuation word ("हम बेसिकली
# जो।") must not take the idle-skip fast path — hold the merge window so
# the caller's resumed speech joins the same turn instead of racing the LLM.
MERGE_HOLD_CONTINUATION = os.environ.get(
    "MERGE_HOLD_CONTINUATION", "true").lower() == "true"
MERGE_HOLD_CONTINUATION_MS = float(os.environ.get(
    "MERGE_HOLD_CONTINUATION_MS", "1200"))

# Words a finished thought does not end on — dangling conjunctions,
# postpositions and discourse connectors in the three call languages
# (Sarvam appends daṇḍa/period even mid-thought, so punctuation is
# stripped before the check).
_CONTINUATION_CUE_WORDS = frozenset({
    # Hindi / Hinglish
    "जो", "कि", "तो", "और", "या", "पर", "लेकिन", "यानी", "मतलब",
    "बेसिकली", "से", "का", "की", "के", "में", "वाला", "वाले",
    # English
    "and", "but", "so", "or", "because", "like", "basically", "mean",
    "umm", "uhh",
    # Gujarati + romanized
    "અને", "પણ", "કે", "એટલે", "ane", "etle",
})


def _ends_with_continuation_cue(text: str) -> bool:
    words = (text or "").strip().split()
    if not words:
        return False
    last = words[-1].strip(".।॥?!,;:\"'").lower()
    return last in _CONTINUATION_CUE_WORDS

# Fallback phrases played when STT returns empty on substantial speech,
# or when TTS receives an empty string or fails to play (feature 2 & 5).
_STT_FALLBACK_PHRASES: dict[str, str] = {
    "hi": "माफ़ कीजिए, आपकी आवाज़ स्पष्ट नहीं आई। कृपया दोबारा बताइए।",
    "gu": "માફ કરશો, તમારો અવાજ સ્પષ્ટ સંભળાયો નથી. કૃપા કરીને ફરી કહેશો?",
    "en": "Sorry, I couldn't hear you clearly. Could you please repeat that?",
}
# Minimum speech duration that triggers a language-fallback response on empty STT
_STT_FALLBACK_MIN_SPEECH_MS = 800.0

# Dead-air repair (platform rule, every agent): when the agent has finished
# speaking and the caller produces NO voice activity for this long, the most
# likely explanations on a phone line are "they answered and STT/VAD missed
# it" or "they never heard us" — so the agent says it couldn't hear them and
# re-asks (ConversationRouter.generate_silence_reprompt). Capped so a caller
# who is deliberately silent isn't nagged forever. Defaults ON in telephony;
# CLI/dev mic sessions idle at a desk, so it's opt-in there.
SILENCE_REPROMPT_ENABLED = os.environ.get(
    "SILENCE_REPROMPT_ENABLED",
    "true" if TELEPHONY_MODE else "false").lower() == "true"
SILENCE_REPROMPT_S = float(os.environ.get("SILENCE_REPROMPT_S", "6.0"))
SILENCE_REPROMPT_MAX = int(os.environ.get("SILENCE_REPROMPT_MAX", "2"))

# Silence hangup (platform rule, every agent; per-agent override via
# call_flow.termination.silence_hangup): once every repair reprompt is spent
# and the caller still shows no voice activity for a further grace window,
# say ONE goodbye and end the call — a walked-away caller used to leave the
# channel (and the billing meter) open until the far end gave up. Same
# telephony-only default as the reprompts themselves.
SILENCE_HANGUP_ENABLED = os.environ.get(
    "SILENCE_HANGUP_ENABLED",
    "true" if TELEPHONY_MODE else "false").lower() == "true"
SILENCE_HANGUP_GRACE_S = float(os.environ.get("SILENCE_HANGUP_GRACE_S", "10.0"))


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
        self._session = None
        self._endpointer = None
        if vad is None and VAD_ENGINE == "fsmn":
            try:
                from .vad_stack import FsmnVadStack
                from .audio_events import AudioEventClassifier
                from .call_session import CallSession
                from .endpointer import EndpointController
                self._session = CallSession(
                    direction=getattr(conversation, "call_direction",
                                      "inbound") or "inbound",
                    event_hub=event_hub)
                classifier = AudioEventClassifier(
                    on_label=self._session.on_audio_label)
                vad = FsmnVadStack(
                    minimum_threshold=260.0 if TELEPHONY_MODE else 150.0,
                    minimum_rms_floor=60.0 if TELEPHONY_MODE else 50.0,
                    event_sink=classifier)
                self._endpointer = EndpointController()
            except Exception:
                logger.exception(
                    "[VAD-STACK] init failed — falling back to energy VAD")
                vad = None
                self._session = None
                self._endpointer = None
        if vad is None:
            vad = VoiceActivityDetector(
                minimum_threshold=260.0 if TELEPHONY_MODE else 150.0,
                minimum_rms_floor=60.0 if TELEPHONY_MODE else 50.0,
                # Absolute ceiling for threshold to prevent deafness
                maximum_threshold=3000.0 if TELEPHONY_MODE else 1500.0,
            )
        self.vad = vad
        
        self.stt = stt or STTRouter()
        if conversation is None:
            # CLI/dev default only — telephony always passes a conversation.
            from ..agent_definition import get_pramukh_agent
            _cli_def = get_pramukh_agent()
            conversation = ConversationRouter(
                agent_def=_cli_def,
                default_language=_cli_def.languages.default or "hi")
        self.conversation = conversation
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
        # A5 delivery epoch (2026-07-30): every supersede event (cont-merge
        # rollback, barge-in drain, deferred-barge kill) bumps this. The TTS
        # player snapshots it at dequeue and re-checks before synthesis — a
        # Response dequeued a moment before its turn was superseded used to
        # synthesize and play anyway (both pre- and post-merge replies were
        # spoken back-to-back on a live call).
        self._delivery_epoch: int = 0
        self._last_playback_start_ts: float = 0.0
        # A4a streaming-STT language pin state.
        self._stt_lang_pin: str | None = None      # BCP-47 or None (=unknown)
        self._stt_lang_last: str | None = None
        self._stt_lang_stable: int = 0
        # Live "is the caller currently speaking" flag — read-only signal for
        # external monitoring (dashboard live-call snapshot); never consulted
        # by any pipeline control-flow branch.
        self._user_speaking: bool = False
        self._tts_start_time = 0.0
        self._tts_end_time = 0.0
        self._vad_mute_until = 0.0
        self._barge_in_occurred = False
        self._mic_enabled = mic_enabled
        # Turn-level trace records (one dict per caller turn), enriched as
        # the turn's stages complete and batch-persisted to the call_turns
        # table by the bridge at teardown. Capped so a marathon call can't
        # grow memory unbounded.
        self.turn_traces: list[dict] = []
        # Set in start() when ENABLE_STREAMING_STT and the router's primary
        # supports live feeding (SarvamStreamingSTT).
        self._streaming_stt = None
        # Turn accumulator: tracks full caller text across segments within one
        # turn. Preserved when barge-in fires so the next STT fragment is merged
        # with prior context. Cleared when TTS completes a clean (non-barge-in)
        # response, meaning the caller has been fully answered.
        self._turn_accumulator: str = ""
        # Last fully-processed caller text and the time TTS finished for it.
        # Used to detect short trailing fragments ("for that") that arrive after
        # a clean TTS completion and should be merged with the previous turn.
        self._last_user_text: str = ""
        self._last_tts_end: float = 0.0
        # Connect delivery guard state: per-utterance sequence number,
        # protected flag, when this utterance's audio first reached the
        # sink's playback clock, and the deferred-barge latch (see
        # _connect_barge_defer / the deferred verdict in _conversation_layer).
        self._utt_seq: int = 0
        self._utt_protected: bool = False
        self._utt_first_flush_ts: float = 0.0
        self._bargein_deferred: bool = False
        self._bargein_deferred_utt: int = -1
        # Continuation-merge state (see ENABLE_CONTINUATION_MERGE):
        # window during which resumed caller speech is a probable
        # continuation, timing anchors, and the per-chain merge counter.
        self._continuation_likely_until: float = 0.0
        self._cur_speech_start_ts: float = 0.0
        self._turn_txn_speech_end: float = 0.0
        self._merge_chain_count: int = 0
        # Number of segments currently being transcribed (merge-window skip
        # signal: a follow-on transcript can only arrive if audio is queued,
        # in flight through STT, or the caller is speaking right now).
        self._stt_inflight: int = 0
        # True while the ACTIVE echo-mute window follows the greeting.
        self._greeting_echo_window: bool = False
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
        # Counts consecutive STT failures (empty transcript while user spoke).
        # Reset to 0 on any successful transcript.  When it hits the limit the
        # pipeline plays the agent's graceful-close phrase and stops queuing
        # more fallbacks — preventing an infinite "sorry I couldn't hear you" loop.
        self._stt_consecutive_fallbacks: int = 0
        # H4: when a streamed finalize consumed a whole speech episode, the
        # NEXT VAD chunk's finalize legitimately comes back empty — that
        # empty must not count as "unrecognized speech" (live: it queued a
        # "couldn't hear you" while the caller had been perfectly heard).
        self._last_accept_ts: float = 0.0
        self._last_accept_overcovered: bool = False
        # Dead-air repair state (see _silence_watchdog): last time the VAD saw
        # caller speech, consecutive reprompts without a real caller turn, and
        # when the last reprompt fired.
        self._last_voice_activity: float = 0.0
        self._reprompt_count: int = 0
        self._last_reprompt_ts: float = 0.0
        # Call-termination watchdog state: one-shot latches for the silence
        # hangup (re-armed if the caller comes back) and the max-duration
        # close (never re-armed — it's a hard cap), plus the call-age anchor.
        self._silence_hangup_fired: bool = False
        self._maxdur_fired: bool = False
        self._call_start_ts: float = time.monotonic()

        # VAD + buffering state
        self._audio_buffer = bytearray()
        self._speech_duration_ms = 0.0
        self._prev_speech = False
        self._speech_frame_streak = 0
        # Tracks whether the previously-started TTS utterance was a filler, so
        # the real response that immediately follows it gets a longer barge-in
        # guard (see FILLER_ECHO_GRACE_MS).  Set per-utterance in the TTS layer.
        self._last_was_filler = False
        self._barge_in_min_elapsed_ms = BARGE_IN_MIN_ELAPSED_MS
        self._frame_count = 0
        self._speech_onset_frames = 0  # debounce: frames since speech started
        self._silence_frames = 0       # debounce: frames since last speech
        self._pre_speech_buffer: list[bytes] = []  # look-back to recover onset clip
        self._echo_gate_speech_frames: int = 0    # sustained frames during echo cooldown

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
            silence_threshold_ms=TURN_SILENCE_THRESHOLD_MS,
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
        # Streaming STT: the bridge installs SarvamStreamingSTT as the
        # router's primary under ENABLE_STREAMING_STT; the VAD layer feeds
        # it live speech frames whenever this handle is non-None.
        self._streaming_stt = (
            primary_stt
            if ENABLE_STREAMING_STT and hasattr(primary_stt, "feed_nowait")
            else None)
        if self._streaming_stt is not None:
            logger.info("[PIPELINE] Streaming STT ACTIVE (ENABLE_STREAMING_STT)")
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

        # CRITICAL LATENCY FIX: warm up in the BACKGROUND. The greeting is canned
        # TTS and does NOT need the LLM — but LLM warmup is a real ~4-5s call to
        # a US endpoint. Awaiting it here used to delay the caller's first word by
        # ~10s. Now the greeting plays immediately while warmup runs concurrently
        # (it finishes long before the caller's first reply needs STT/LLM).
        if warmup_coros:
            async def _bg_warmup():
                try:
                    results = await asyncio.wait_for(
                        asyncio.gather(*warmup_coros, return_exceptions=True),
                        timeout=8.0,
                    )
                    for r in results:
                        if isinstance(r, Exception):
                            logger.warning("[WARMUP] Error: %s", r)
                except asyncio.TimeoutError:
                    logger.warning("[WARMUP] Timed out — proceeding anyway")
                finally:
                    self._warmup_done.set()
                    logger.info("[WARMUP] Background warmup complete")
            asyncio.create_task(_bg_warmup())
        else:
            self._warmup_done.set()

        self._tasks = [
            asyncio.create_task(self._audio_vad_layer(), name="audio_vad"),
            asyncio.create_task(self._stt_worker(), name="stt_worker"),
            asyncio.create_task(self._conversation_layer(), name="conversation"),
            asyncio.create_task(self._tts_layer(), name="tts"),
            asyncio.create_task(self._silence_watchdog(), name="silence_watchdog"),
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
                protected=True,
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
        # Finalize any pending turn transaction BEFORE teardown so deferred
        # side effects (calendar booking) flush and the bridge's persistence
        # reads fully-committed state.
        if hasattr(self.conversation, "commit_pending_turn"):
            try:
                self.conversation.commit_pending_turn()
            except Exception:
                logger.exception("[PIPELINE] commit_pending_turn at stop failed")
        await self.audio.stop()
        if self._session is not None:
            self._session.set("CALL_ENDED")
        if hasattr(self.vad, "close"):
            try:
                self.vad.close()
            except Exception:
                pass
        if self._streaming_stt is not None:
            try:
                await self._streaming_stt.close()
            except Exception:
                pass
            self._streaming_stt = None
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
            self._user_speaking = False
            return

        self._vad_frames_seen += 1
        raw_speech = self.vad.is_speech(frame)
        if raw_speech:
            self._vad_speech_frames += 1
        self._user_speaking = bool(raw_speech) and not self._tts_playing

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
                    
            # Continuation-likely window: right after a fast endpoint the
            # caller may still be finishing their sentence — hear a soft
            # continuation that the 2× echo-reject threshold would drop.
            # Bounded to CONTINUATION_WINDOW_MS past their speech end; outside
            # it, today's constants apply unchanged.
            in_cont_window = (
                ENABLE_CONTINUATION_MERGE
                and self._greeting_done
                and time.monotonic() < self._continuation_likely_until
            )
            if in_cont_window:
                barge_threshold = max(
                    self.vad.threshold * CONTINUATION_BARGE_FACTOR, 300.0)
            else:
                barge_threshold = self.vad.barge_in_threshold
            # B2: while capturing an email the agent's prompts are short and
            # the caller often talks over them mid-spelling — demand a much
            # louder interrupt so the capture prompt actually finishes.
            if (CAPTURE_MODE and self.conversation is not None
                    and getattr(self.conversation, "_capture_slot", None)):
                barge_threshold *= 2.0
            loud = self.vad.last_energy > barge_threshold

            if loud:
                self._speech_frame_streak += 1
            else:
                self._speech_frame_streak = 0

            elapsed = (time.monotonic() - self._tts_start_time) * 1000

            # Incremental TTS flushing means injected audio can already be
            # playing on the call while speak() is still synthesizing later
            # parts — its echo returns via mod_audio_stream DURING
            # _tts_playing. While the sink reports playback in flight,
            # require a longer sustained-speech streak so echo can't cancel
            # the rest of the utterance. A real interrupter clears the bar in
            # ~200ms; LocalSpeakerSink has no playback_busy_until, so CLI
            # mode is unaffected.
            min_frames = BARGE_IN_MIN_FRAMES
            if in_cont_window:
                min_frames = CONTINUATION_BARGE_FRAMES
            elif time.monotonic() < getattr(self.sink, 'playback_busy_until', 0.0):
                min_frames = BARGE_IN_MIN_FRAMES * 2

            if (
                self._speech_frame_streak >= min_frames
                and elapsed > self._barge_in_min_elapsed_ms
            ):
                # Connect delivery guard: never let a burst of energy kill a
                # reply the caller has not heard yet (the repeated-"hello"
                # loop: each hello killed the answer before its first WAV
                # was flushed, so the caller heard only dead air). Latch the
                # deferral; the STT verdict in _conversation_layer kills the
                # playback iff the interruption was more than a bare hello.
                _defer_now = time.monotonic()
                if self._connect_barge_defer(_defer_now):
                    if not self._bargein_deferred:
                        self._bargein_deferred = True
                        self._bargein_deferred_utt = self._utt_seq
                        logger.info(
                            "[BARGE-IN] Deferred (delivery window) "
                            "frames=%d energy=%.0f played=%.2fs — "
                            "awaiting STT verdict",
                            self._speech_frame_streak,
                            self.vad.last_energy,
                            max(0.0, _defer_now - self._utt_first_flush_ts)
                            if self._utt_first_flush_ts > 0.0 else 0.0,
                        )
                    self._speech_frame_streak = 0
                    return

                logger.info(
                    "[BARGE-IN] Interrupting TTS "
                    "frames=%d energy=%.0f threshold=%.0f cont_window=%s",
                    self._speech_frame_streak,
                    self.vad.last_energy,
                    barge_threshold,
                    in_cont_window,
                )

                # Media-layer abort FIRST: WAVs already injected into
                # FreeSWITCH keep playing no matter what we cancel locally.
                # interrupt_playback() sends uuid_break to the channel, then
                # drains the sink's queued WAVs (LocalSpeakerSink has no such
                # method — CLI mode is unaffected).
                interrupt = getattr(self.sink, "interrupt_playback", None)
                if interrupt is not None:
                    await interrupt()

                await self.tts.stop()

                self._tts_playing = False
                self._barge_in_occurred = True
                if self._session is not None:
                    self._session.set("USER_INTERRUPTED", "barge-in")
                if self.turn_traces:
                    self.turn_traces[-1]["barged_in"] = True
                self._vad_mute_until = 0.0
                self._speech_frame_streak = 0
                # The extended post-filler barge-in guard must not outlive the
                # filler/response pair it protects: without this reset, the
                # NEXT unrelated utterance after a barge-in inherited the
                # 1500ms FILLER_ECHO_GRACE window and was deaf to
                # interruption for 1.5s on a turn that had no filler.
                self._last_was_filler = False

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
        # During this window audio is likely TTS residue. Discard it unless
        # sustained real speech is detected, in which case exit early.
        if time.monotonic() < self._vad_mute_until:
            if raw_speech:
                self._echo_gate_speech_frames = getattr(self, '_echo_gate_speech_frames', 0) + 1
            else:
                self._echo_gate_speech_frames = 0

            # Post-greeting window exits earliest ("hello" is the most
            # predictable speech event of the call); continuation-likely
            # windows exit slightly early; everything else keeps the default.
            exit_frames = ECHO_EARLY_EXIT_FRAMES
            if self._greeting_echo_window:
                exit_frames = GREETING_ECHO_EARLY_EXIT_FRAMES
            elif (ENABLE_CONTINUATION_MERGE
                    and time.monotonic() < self._continuation_likely_until):
                exit_frames = CONTINUATION_ECHO_EXIT_FRAMES

            if self._echo_gate_speech_frames >= exit_frames:
                # Sustained loud speech during cooldown — this is a real caller,
                # not echo. Exit the mute window early and let speech through.
                logger.info(
                    "[ECHO-GATE] Early exit: %d sustained speech frames detected "
                    "(%.0fms cooldown remaining) — treating as real caller speech",
                    self._echo_gate_speech_frames,
                    (self._vad_mute_until - time.monotonic()) * 1000,
                )
                self._vad_mute_until = 0.0
                self._echo_gate_speech_frames = 0
                # Fall through to normal VAD processing below
            else:
                if self._audio_buffer:
                    logger.debug(
                        "[ECHO-GATE] Discarding %d bytes of post-TTS audio "
                        "(%.0fms cooldown remaining, echo_frames=%d)",
                        len(self._audio_buffer),
                        (self._vad_mute_until - time.monotonic()) * 1000,
                        self._echo_gate_speech_frames,
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
            self._last_voice_activity = time.monotonic()
        else:
            self._silence_frames += 1
            self._speech_onset_frames = 0
            # Look-back ring so onset clip is recoverable. Energy engine:
            # 2 frames (decision is instant). FSMN engine: 20 frames
            # (400ms) — the model confirms speech ~100-300ms after true
            # onset, and the ring is what recovers those first syllables
            # into the STT buffer.
            self._pre_speech_buffer.append(frame)
            _ring_max = (20 if self._endpointer is not None
                         else SPEECH_ONSET_MIN_FRAMES)
            if len(self._pre_speech_buffer) > _ring_max:
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
                # Continuation-merge anchor: when the caller actually began
                # speaking (accounting for the recovered pre-onset frames).
                self._cur_speech_start_ts = (
                    time.monotonic() - 0.02 * len(self._pre_speech_buffer)
                )
                # Prepend buffered pre-onset frames to recover clipped onset
                if self._pre_speech_buffer:
                    for pre_frame in self._pre_speech_buffer:
                        self._audio_buffer.extend(pre_frame)
                        self._speech_duration_ms += 20.0
                        if self._streaming_stt is not None:
                            self._streaming_stt.feed_nowait(pre_frame)
                    self._pre_speech_buffer.clear()
            self._turn.mark_speech()
            self._audio_buffer.extend(frame)
            self._speech_duration_ms += 20.0
            if self._streaming_stt is not None:
                self._streaming_stt.feed_nowait(frame)
            if self._session is not None:
                # Monologue-length tick (outbound IVR/voicemail heuristics).
                self._session.on_speech_run(self._speech_duration_ms)

            if len(self._audio_buffer) > MAX_BUFFER_BYTES:
                del self._audio_buffer[:len(self._audio_buffer) - MAX_BUFFER_BYTES]

            # Streaming STT emits finalized SEGMENT transcripts mid-turn
            # (no word partials exist on saaras) — when real words are
            # available the TurnManager sees them instead of proxy tokens,
            # so its sentence-end heuristic can actually match.
            stream_text = (self._streaming_stt.turn_text
                           if self._streaming_stt is not None else "")
            if stream_text:
                self._turn.update_text(stream_text)
            else:
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
        # Legacy energy engine: fixed HARD_FLUSH_SILENCE_FRAMES. FSMN engine
        # (VAD_ENGINE=fsmn): the model already absorbed FSMN_MAX_END_SIL_MS
        # of pause tolerance before is_speech flipped false, so the
        # EndpointController decides with small semantic-aware residuals
        # (fast on complete sentences, hold on dangling/digit dictation).
        if (
            not speech
            and self._speech_duration_ms >= self._min_speech_ms(time.monotonic())
        ):
            if self._endpointer is not None:
                _flush_due = self._endpointer.should_flush(
                    self._silence_frames,
                    self._speech_duration_ms,
                    (self._streaming_stt.turn_text
                     if self._streaming_stt is not None else ""),
                    self._streaming_stt is not None,
                )
            else:
                _flush_due = self._silence_frames >= HARD_FLUSH_SILENCE_FRAMES
            if _flush_due:
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

            if self._speech_duration_ms < self._min_speech_ms(time.monotonic()):
                logger.debug(
                    "[TURN] Too short (%.0fms) — discarding",
                    self._speech_duration_ms
                )
                self._audio_buffer.clear()
                self._speech_duration_ms = 0.0
                self._turn.flush()
                if self._streaming_stt is not None:
                    # The fed audio was dropped locally — invalidate any
                    # segment the server may still emit for it.
                    self._streaming_stt.reset_turn()
                return

            _now_flush = time.monotonic()
            _speech_end_ts = _now_flush - self._silence_frames * 0.02
            segment = AudioSegment(
                audio=bytes(self._audio_buffer),
                duration_ms=self._speech_duration_ms,
                timestamp=_now_flush,
                speech_start_ts=self._cur_speech_start_ts,
                speech_end_ts=_speech_end_ts,
            )
            self._segments_created += 1

            # Arm the continuation-likely window: for CONTINUATION_WINDOW_MS
            # past the caller's speech end, resumed speech is treated as a
            # probable continuation (relaxed hearing above; merge decision in
            # the conversation layer).
            if ENABLE_CONTINUATION_MERGE:
                self._continuation_likely_until = (
                    _speech_end_ts + CONTINUATION_WINDOW_MS / 1000.0
                )

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

    def _maybe_commit_pending_turn(self) -> None:
        """Lazily commit the pending turn transaction once the continuation
        window has clearly expired and no caller input is in flight.

        The in-flight guard matters: committing flushes deferred side effects
        (calendar booking), which must not happen while a just-spoken
        continuation is still on its way through STT.
        """
        if not ENABLE_CONTINUATION_MERGE:
            return
        conv = self.conversation
        if not hasattr(conv, "has_pending_turn") or not conv.has_pending_turn():
            return
        if self._turn_txn_speech_end <= 0.0:
            return
        expired = (
            time.monotonic() - self._turn_txn_speech_end
            > (CONTINUATION_WINDOW_MS + 1000.0) / 1000.0
        )
        quiescent = (
            not self._prev_speech
            and self._stt_queue.empty()
            and self._stt_inflight == 0
            and self._transcript_queue.empty()
        )
        if expired and quiescent:
            conv.commit_pending_turn()
            self._merge_chain_count = 0

    def _flush_pipeline_queues(self) -> None:
        self._delivery_epoch += 1   # A5: anything dequeued earlier is stale
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
        if self._streaming_stt is not None and not BARGE_KEEP_STREAM_AUDIO:
            # Legacy behavior: invalidate everything fed to the stream. This
            # also discarded the caller's own interrupting speech (fed before
            # the barge threshold tripped), so it is off by default — the
            # superseded turn's segments were already consumed by its own
            # finalize, which bumps the generation itself.
            self._streaming_stt.reset_turn()

    # -----------------------------------------------------------------------
    # Layer 2: STT Worker
    # -----------------------------------------------------------------------

    def _update_stt_lang_pin(self) -> None:
        """A4a: pin the streaming STT once the conversation language has been
        stable for STT_LANG_PIN_TURNS accepted transcripts; release the pin
        the moment conversation state moves off it (explicit request or
        evidence switch flips state → one auto-detect turn → re-pin). The
        reconnect itself happens inside the stream client at the next turn
        boundary — never mid-turn."""
        if not STT_LANG_PIN or self.conversation is None:
            return
        cur = getattr(getattr(self.conversation, "state", None),
                      "language", None)
        if cur not in ("en", "hi", "gu"):
            return
        if cur == self._stt_lang_last:
            self._stt_lang_stable += 1
        else:
            self._stt_lang_last = cur
            self._stt_lang_stable = 1
        setter = getattr(self.stt, "set_stream_language", None)
        mapped = {"en": "en-IN", "hi": "hi-IN", "gu": "gu-IN"}[cur]
        if self._stt_lang_pin and self._stt_lang_pin != mapped:
            self._stt_lang_pin = None
            if callable(setter):
                setter("unknown")
            logger.info("[STT] Language pin released (state now %s)", cur)
        elif (self._stt_lang_pin is None
                and self._stt_lang_stable >= STT_LANG_PIN_TURNS):
            self._stt_lang_pin = mapped
            if callable(setter):
                setter(mapped)
            logger.info("[STT] Language pinned to %s (stable %d turns)",
                        mapped, self._stt_lang_stable)

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

            self._stt_inflight += 1
            try:
                latency_start = time.monotonic()
                # Pass the locked conversation language to Sarvam to prevent
                # auto-detect misfires (Bengali/Punjabi/Malayalam/Marathi on
                # Gujarati/Hindi audio). "unknown" is only used pre-lock (first
                # turn / GREETING stage) to allow free language detection.
                # English is also passed explicitly when locked — Sarvam handles
                # English-IN well and it prevents misclassification on accented
                # English with Indian words.
                # Default: auto-detect every turn (prevents a lock feedback loop
                # and enables dynamic language switching). BUT an agent may pin STT
                # to one language via languages.stt_forced_language (e.g. "hi-IN")
                # when it is single-language and auto-detect misfires (Hindi tagged
                # as Punjabi/Tamil/Malayalam).
                _stt_lang = "unknown"
                _forced_lang = ""
                if self.conversation and hasattr(self.conversation, "_def"):
                    _forced_lang = getattr(
                        self.conversation._def.languages, "stt_forced_language", ""
                    ) or ""
                if _forced_lang:
                    _stt_lang = _forced_lang
                    logger.debug("[STT-WORKER] stt_lang forced to %s by agent config", _stt_lang)
                elif (
                    CONNECT_FORCE_LANG
                    and segment.duration_ms < CONNECT_SHORT_SEG_MS
                    and (time.monotonic() - self._call_start_ts)
                    < CONNECT_WINDOW_S
                ):
                    # Connect window: a short first segment is almost always
                    # "hello" — with lang=unknown Sarvam routinely labels it
                    # Odia/Kannada, forcing a retry round-trip that doubles
                    # STT latency exactly where dead air feeds the hello
                    # loop. Anchor to the agent's default language; real
                    # sentences (≥ CONNECT_SHORT_SEG_MS) keep auto-detect.
                    _default_short = "en"
                    if self.conversation and hasattr(self.conversation, "_def"):
                        _default_short = (
                            getattr(self.conversation._def.languages,
                                    "default", "") or "en")
                    _stt_lang = {"en": "en-IN", "hi": "hi-IN",
                                 "gu": "gu-IN"}.get(_default_short, "unknown")
                    if _stt_lang != "unknown":
                        logger.info(
                            "[STT-WORKER] Connect window: forcing lang=%s "
                            "for %.0fms segment", _stt_lang,
                            segment.duration_ms)
                elif (CAPTURE_MODE and self.conversation is not None
                        and getattr(self.conversation, "_capture_slot", None)):
                    # B2: spelled emails/letters are Latin — en-IN stops the
                    # letter-name transliteration ("एल दब्यू वाय एल") and the
                    # junk-script roulette mid-capture.
                    _stt_lang = "en-IN"
                    logger.info(
                        "[STT-WORKER] Capture mode: forcing lang=en-IN")
                elif STT_LANG_PIN and self._stt_lang_pin:
                    _stt_lang = self._stt_lang_pin
                    logger.debug("[STT-WORKER] stt_lang pinned to %s", _stt_lang)
                else:
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
                # F16: best-known conversation language for the STT router's
                # script-misdetection retry (genuine Hindi transcribed as
                # Gurmukhi etc.). Before any caller turn establishes a language
                # this is the language the agent itself opened in — an
                # approximate anchor, but the retry only fires on transcripts
                # in scripts we don't support at all, where the alternative is
                # dropping the caller's first response outright (2026-07-24
                # test call: 540ms "hello" → ml-IN conf 1.00 → dropped → dead-
                # air reprompt → hang-up).
                _retry_lang = None
                if self.conversation is not None:
                    _conv_state = getattr(self.conversation, "state", None)
                    if _conv_state is not None:
                        _retry_lang = getattr(_conv_state, "language", None) or None
                stt_result = await self.stt.transcribe(
                    segment.audio, sample_rate=SAMPLE_RATE, language=_stt_lang,
                    default_language=_retry_lang,
                )
                transcript_text = stt_result.text
                stt_latency = (time.monotonic() - latency_start) * 1000

                # Update conversation language from Sarvam's native detection
                # result. Guarded by default (LANG_MIRROR_GUARDED): the write
                # is deferred until the transcript survives validation, and
                # only lands when the claim is consistent with the
                # transcript's own script/markers — acoustic misdetection
                # (bare "hello" → anything, Indian-accented English → hi-IN)
                # used to flip the agent's accent mid-call before the
                # transcript was even accepted. LANG_MIRROR_GUARDED=false
                # restores the legacy immediate unconditional write.
                _pending_mirror: str | None = None
                if (
                    stt_result.detected_language
                    and self.conversation
                    and transcript_text
                ):
                    _bcp47_to_short = {"en-IN": "en", "hi-IN": "hi", "gu-IN": "gu"}
                    short = _bcp47_to_short.get(stt_result.detected_language)
                    if short:
                        if LANG_MIRROR_GUARDED:
                            _pending_mirror = short
                        else:
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
                # H1: a streamed finalize spans the whole speech EPISODE
                # (server-stitched across VAD chunks), so score against the
                # audio it actually covered — the local chunk alone rejected
                # real answers as hallucination (14 words / 1440ms, live).
                if transcript_text and transcript_text.strip():
                    _covered_ms = getattr(self.stt, "last_covered_seconds",
                                          0.0) * 1000.0
                    _effective_ms = max(segment.duration_ms, _covered_ms)
                    _word_count = len(transcript_text.split())
                    _max_plausible = max(4, int(_effective_ms / 250))
                    if _word_count > _max_plausible * 2:
                        logger.warning(
                            "[STT] Rejecting implausible transcript: %d words in %.0fms "
                            "(max_plausible=%d) — likely hallucination: %r",
                            _word_count, _effective_ms, _max_plausible,
                            transcript_text[:80],
                        )
                        transcript_text = ""
                    elif _word_count > _max_plausible:
                        logger.warning(
                            "[STT] Suspicious word count: %d words in %.0fms "
                            "(max_plausible=%d): %r",
                            _word_count, _effective_ms, _max_plausible,
                            transcript_text[:80],
                        )

                # Guard against very short segments producing a single ambiguous word
                # that could spuriously trigger location/name/budget extraction.
                # Segments < 700ms yielding a single word are more likely noise than
                # meaningful input — unless the segment is clearly an affirmation/negation
                # (yes/no/haan/ji etc.) or a greeting reply, which must pass through
                # for flow logic to work.
                if (
                    transcript_text
                    and len(transcript_text.split()) == 1
                    and segment.duration_ms < 700.0
                ):
                    # Sarvam punctuates single words ('Hello.'), so trailing marks
                    # must come off before the allowlist lookup — otherwise even
                    # 'Yes.' falls through and the turn is lost.
                    _single = transcript_text.strip().lower().strip(".,!?;:।॥'\"")
                    _affirmation_negation = {
                        "yes", "yeah", "yep", "ok", "okay", "sure",
                        "haan", "ha", "ji", "haan", "na", "nahi", "no",
                        "nope", "theek", "barobar", "aavjo",
                        "हां", "हाँ", "जी", "ठीक", "हा", "ना", "नहीं",
                        "હા", "જી", "ઠીક", "ના", "નહીં",
                    }
                    # A caller answering the agent's greeting with a bare greeting is
                    # the most predictable turn of the call — it must never be dropped,
                    # or the agent goes silent and the call stalls before stage 1.
                    _greetings = {
                        "hello", "hallo", "helo", "hi", "hey", "yello",
                        "namaste", "namaskar", "salaam", "salam",
                        "kemcho", "kem", "bolo", "boliye", "haanji", "hanji",
                        "नमस्ते", "नमस्कार", "हैलो", "हलो", "हेलो", "हॅलो",
                        "हैल्लो", "हल्लो", "बोलो", "बोलिए",
                        "નમસ્તે", "નમસ્કાર", "હેલો", "હલો", "કેમ", "બોલો",
                    }
                    if _single not in _affirmation_negation and _single not in _greetings:
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
                        if (self._last_accept_overcovered
                                and time.monotonic() - self._last_accept_ts < 3.0):
                            # H4: previous streamed transcript covered this
                            # chunk's audio too — nothing was missed.
                            logger.info(
                                "[STT] Empty finalize (%.0fms) right after an "
                                "over-covered streamed transcript — episode "
                                "already consumed, skipping",
                                segment.duration_ms,
                            )
                        elif not self._greeting_done:
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
                            self._stt_consecutive_fallbacks += 1
                            _MAX_STT_FALLBACKS = 4
                            if self._stt_consecutive_fallbacks > _MAX_STT_FALLBACKS:
                                # Break the infinite loop: too many consecutive failures.
                                # Play a graceful-close phrase and stop queuing more fallbacks.
                                logger.warning(
                                    "[STT] %d consecutive STT failures — playing graceful close to break loop",
                                    self._stt_consecutive_fallbacks,
                                )
                                close_text = None
                                if self.conversation and hasattr(self.conversation, '_def'):
                                    rb = self.conversation._def.response_bank
                                    close_text = (
                                        rb.graceful_close.get(lang)
                                        or rb.lang_fallbacks.get(lang)
                                        or rb.response_fallback
                                    )
                                if not close_text:
                                    close_text = {
                                        "hi": "क्षमा कीजिए, मुझे आपकी आवाज़ सुनने में परेशानी हो रही है। कृपया बाद में कॉल करें।",
                                        "gu": "માફ કરશો, મને સાંભળવામાં તકલીફ છે. કૃપા કરીને પછીથી કૉલ કરો.",
                                        "en": "I'm sorry, I'm having trouble hearing you. Please try calling again shortly.",
                                    }.get(lang, "I'm sorry, I'm having trouble hearing you. Please try calling again.")
                                try:
                                    self._tts_queue.put_nowait(Response(
                                        text=close_text,
                                        is_shortcut=True,
                                        timestamp=time.monotonic(),
                                        language=(lang if lang in
                                                  ("en", "hi", "gu") else "en"),
                                    ))
                                except asyncio.QueueFull:
                                    pass
                            elif not self._empty_fallback_speaks(
                                    self._stt_consecutive_fallbacks):
                                # Damper: only the SECOND consecutive empty
                                # speaks. The first stays silent (one garbled
                                # segment is normal), later ones defer to the
                                # dead-air watchdog — a spoken "sorry" per
                                # empty once fed an echo→empty→sorry loop.
                                logger.info(
                                    "[STT] Unrecognized speech (%.0fms, lang=%s, "
                                    "attempt %d/%d) — staying silent (empty-streak damper)",
                                    segment.duration_ms, lang,
                                    self._stt_consecutive_fallbacks, _MAX_STT_FALLBACKS,
                                )
                            else:
                                fallback = _STT_FALLBACK_PHRASES.get(lang, _STT_FALLBACK_PHRASES["en"])
                                logger.info(
                                    "[STT] Unrecognized speech (%.0fms, lang=%s, attempt %d/%d) — queuing fallback",
                                    segment.duration_ms, lang,
                                    self._stt_consecutive_fallbacks, _MAX_STT_FALLBACKS,
                                )
                                try:
                                    self._tts_queue.put_nowait(Response(
                                        text=fallback,
                                        is_shortcut=True,
                                        timestamp=time.monotonic(),
                                        language=(lang if lang in
                                                  ("en", "hi", "gu") else "en"),
                                    ))
                                except asyncio.QueueFull:
                                    logger.warning("[STT] TTS queue full — cannot play language-fallback phrase")
                    else:
                        logger.info("[STT] Empty transcript (%.0fms) — below fallback threshold, skipping",
                                    segment.duration_ms)
                    continue

                self._stt_consecutive_fallbacks = 0  # successful transcript — reset failure streak
                # H4 bookkeeping: remember whether this transcript covered
                # more audio than its own VAD chunk (streamed episode).
                self._last_accept_ts = time.monotonic()
                self._last_accept_overcovered = (
                    getattr(self.stt, "last_covered_seconds", 0.0) * 1000.0
                    > segment.duration_ms + 500.0
                )
                # Deferred language mirror: the transcript survived every
                # rejection guard — apply Sarvam's detection only if it is
                # plausible for this text (script/markers agree; a lone
                # "okay" is not an English switch).
                # A3 (2026-07-30): recovered transcripts (script-retry /
                # Whisper) are mishear-prone — a Whisper ", angie?" for
                # "हाँजी?" must never flip the conversation language.
                if getattr(stt_result, "source", "primary") != "primary":
                    if _pending_mirror:
                        logger.info(
                            "[STT] Language mirror skipped (source=%s)",
                            getattr(stt_result, "source", "?"))
                    _pending_mirror = None
                if _pending_mirror and self.conversation:
                    if self.conversation.stt_mirror_consistent(
                            _pending_mirror, transcript_text):
                        logger.info(
                            "[STT] Language auto-detected by Sarvam: %s → %s (guarded)",
                            stt_result.detected_language, _pending_mirror)
                        self.conversation.state.language = _pending_mirror
                    else:
                        logger.info(
                            "[STT] Language mirror suppressed: %r claim "
                            "inconsistent with transcript %r",
                            _pending_mirror, transcript_text[:40])
                logger.info("[STT] %r (%.0fms latency)", transcript_text, stt_latency)

                self._update_stt_lang_pin()

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
                    speech_start_ts=segment.speech_start_ts,
                    speech_end_ts=segment.speech_end_ts,
                    confidence=getattr(stt_result, "confidence", None),
                    stt_source=getattr(stt_result, "source", "primary"),
                )

                try:
                    self._transcript_queue.put_nowait(transcript)
                except asyncio.QueueFull:
                    logger.warning("[STT] Transcript queue full — dropping")

            except Exception:
                logger.exception("[STT] Transcription failed")
            finally:
                self._stt_inflight -= 1

        logger.info("[STT] Worker stopped")

    # -----------------------------------------------------------------------
    # Layer 3: Conversation Engine
    # -----------------------------------------------------------------------

    def _pipeline_quiet(self, now: float) -> bool:
        """Shared precondition for every dead-air decision: the agent has
        spoken, nothing is playing or in flight anywhere in the pipeline,
        and estimated wire playback has drained."""
        if not self._greeting_done or self._tts_playing:
            return False
        gen = self._current_generation_task
        if gen is not None and not gen.done():
            return False
        if (self._stt_inflight or self._user_speaking or self._prev_speech
                or not self._stt_queue.empty()
                or not self._transcript_queue.empty()
                or not self._tts_queue.empty()):
            return False
        busy_until = getattr(self.sink, 'playback_busy_until', 0.0)
        return now >= busy_until + 0.5

    def _silence_anchor(self) -> float:
        """Timestamp of the last activity in either direction — silence is
        measured from here."""
        busy_until = getattr(self.sink, 'playback_busy_until', 0.0)
        return max(self._tts_end_time, busy_until,
                   self._last_voice_activity, self._last_reprompt_ts)

    def _termination_cfg(self) -> dict:
        """The active agent's call_flow.termination ({} when absent — e.g.
        mock conversations in tests)."""
        fn = getattr(self.conversation, "_termination_cfg", None)
        if callable(fn):
            try:
                return fn() or {}
            except Exception:
                return {}
        return {}

    def _connect_cfg(self) -> dict:
        """The active agent's call_flow.connect ({} when absent)."""
        fn = getattr(self.conversation, "_connect_cfg", None)
        if callable(fn):
            try:
                return fn() or {}
            except Exception:
                return {}
        return {}

    def _bare_hello(self, text: str) -> bool:
        """Delegate to the router's script-aware bare-hello check."""
        fn = getattr(self.conversation, "_is_bare_hello_text", None)
        if callable(fn):
            try:
                return bool(fn(text))
            except Exception:
                return False
        return False

    def _min_speech_ms(self, now: float) -> float:
        """Effective STT floor: lowered inside the connect window so a
        clipped 240ms "hello" still reaches STT instead of being dropped
        into dead air (the watchdog then parrots the greeting)."""
        if now - self._call_start_ts < CONNECT_WINDOW_S:
            return min(CONNECT_MIN_SPEECH_MS, MIN_SPEECH_MS)
        return MIN_SPEECH_MS

    def _resolve_tts_language(self, response: "Response", text: str) -> tuple[str, str]:
        """(language, source) the TTS speaks this utterance in. A language
        stamped on the Response (decided with the words) always wins; else
        the text's own script/markers decide (TTS_LANG_FROM_TEXT, per-agent
        opt-out languages.tts_lang_from_text); conversation state is the
        fallback and the pre-fix behaviour."""
        conv = self.conversation
        state_lang = (getattr(conv.state, "language", None) or "en") if conv else "en"
        if response.language:
            return response.language, "stamp"
        if not TTS_LANG_FROM_TEXT:
            return state_lang, "state"
        if conv is not None and getattr(
                conv._def.languages, "tts_lang_from_text", True) is False:
            return state_lang, "state"
        fn = getattr(conv, "tts_language_for", None) if conv else None
        if callable(fn):
            try:
                return fn(text), "text"
            except Exception:
                return state_lang, "state"
        return state_lang, "state"

    @staticmethod
    def _empty_fallback_speaks(streak: int) -> bool:
        """Empty-transcript damper decision: with EMPTY_STREAK_DAMPER on,
        only the SECOND consecutive empty speaks the "couldn't hear you"
        line — one garbled segment is normal, and a sorry per empty once
        fed an echo→empty→sorry self-loop."""
        return (not EMPTY_STREAK_DAMPER) or streak == 2

    def _connect_barge_defer(self, now: float) -> bool:
        """Should this barge-in be DEFERRED instead of killing playback?

        True while the currently-playing utterance is one the caller cannot
        have meaningfully heard — nothing flushed to the playback clock yet,
        or less than CONNECT_MIN_PLAYED_S audible — during the connect
        window (or any time for Response.protected utterances). The kill is
        not lost: the transcript verdict in _conversation_layer performs it
        the moment STT shows real content. A bare "hello?" releases the
        deferral and playback simply continues.
        """
        if not CONNECT_DELIVERY_GUARD:
            return False
        if self._connect_cfg().get("delivery_guard") is False:
            return False
        if not self._tts_playing:
            return False
        if not (self._utt_protected
                or now - self._call_start_ts < CONNECT_WINDOW_S):
            return False
        busy_until = getattr(self.sink, "playback_busy_until", 0.0)
        if self._utt_first_flush_ts <= 0.0:
            if busy_until > now:
                # First audio of this utterance just reached the clock.
                self._utt_first_flush_ts = now
            return True
        return (now - self._utt_first_flush_ts) < CONNECT_MIN_PLAYED_S

    def _silence_reprompt_due(self, now: float) -> bool:
        """Pure decision: is the call in genuine dead air the agent should
        repair? True only when the agent has spoken, nothing is playing or
        in flight anywhere in the pipeline, the caller has produced no voice
        activity for SILENCE_REPROMPT_S, and the repair cap isn't spent."""
        if self._reprompt_count >= SILENCE_REPROMPT_MAX:
            return False
        # Call is wrapping up (farewell/outcome decided) — silence is normal.
        if getattr(self.conversation, "call_outcome", ""):
            return False
        if not self._pipeline_quiet(now):
            return False
        anchor = self._silence_anchor()
        return anchor > 0.0 and now - anchor >= SILENCE_REPROMPT_S

    def _silence_hangup_due(self, now: float) -> bool:
        """Silence hangup (call_flow.termination.silence_hangup): every
        repair reprompt is spent — or the call already reached a wrap-up
        outcome where reprompts don't fire — and the line has stayed dead
        for a further grace window. The caller always gets the full repair
        cycle's benefit of the doubt before the platform closes."""
        if not SILENCE_HANGUP_ENABLED or self._silence_hangup_fired:
            return False
        sh = self._termination_cfg().get("silence_hangup")
        sh = sh if isinstance(sh, dict) else {}
        if not bool(sh.get("enabled", True)):
            return False
        conv = self.conversation
        if not hasattr(conv, "generate_silence_goodbye"):
            return False
        if getattr(conv, "hangup_pending", False):
            return False  # a close is already draining — don't double-fire
        cap_spent = (not SILENCE_REPROMPT_ENABLED
                     or self._reprompt_count >= SILENCE_REPROMPT_MAX
                     or bool(getattr(conv, "call_outcome", "")))
        if not cap_spent:
            return False
        if not self._pipeline_quiet(now):
            return False
        try:
            grace = float(sh.get("grace_s", SILENCE_HANGUP_GRACE_S))
        except (TypeError, ValueError):
            grace = SILENCE_HANGUP_GRACE_S
        anchor = self._silence_anchor()
        return anchor > 0.0 and now - anchor >= SILENCE_REPROMPT_S + max(grace, 0.0)

    def _max_duration_due(self, now: float) -> bool:
        """Hard duration cap (call_flow.termination.max_call_minutes > 0):
        the call has outlived the business's ceiling. Fires once, and only
        at a quiet moment — the wrap-up line never talks over anyone."""
        if self._maxdur_fired:
            return False
        try:
            cap_min = float(self._termination_cfg().get("max_call_minutes") or 0)
        except (TypeError, ValueError):
            cap_min = 0.0
        if cap_min <= 0:
            return False
        conv = self.conversation
        if not hasattr(conv, "generate_timeout_goodbye"):
            return False
        if getattr(conv, "hangup_pending", False):
            return False
        if now - self._call_start_ts < cap_min * 60.0:
            return False
        return self._pipeline_quiet(now)

    async def _silence_watchdog(self) -> None:
        """Dead-air repair + call termination (platform rules): re-ask a
        quiet caller (at most SILENCE_REPROMPT_MAX times per silence), then
        close the call if they never come back; also enforces the per-agent
        max-call-duration cap."""
        while self._running:
            try:
                await asyncio.sleep(0.5)
                now = time.monotonic()

                # Hard duration cap — checked first: it outranks repair.
                if self._max_duration_due(now):
                    self._maxdur_fired = True
                    try:
                        text = self.conversation.generate_timeout_goodbye()
                    except Exception:
                        logger.exception("[HANGUP] max-duration goodbye failed")
                        continue
                    logger.info("[HANGUP] max_call_minutes reached after "
                                "%.1f min — wrap-up line queued",
                                (now - self._call_start_ts) / 60.0)
                    self._queue_watchdog_line(text, now)
                    continue

                # Silence hangup — the repair cycle is spent and the line is
                # still dead; close it with one goodbye.
                if self._silence_hangup_due(now):
                    self._silence_hangup_fired = True
                    try:
                        text = self.conversation.generate_silence_goodbye()
                    except Exception:
                        logger.exception("[HANGUP] silence goodbye failed")
                        continue
                    anchor = self._silence_anchor()
                    logger.info("[HANGUP] silence hangup after %.1fs dead air "
                                "— goodbye queued",
                                now - anchor if anchor else 0.0)
                    self._queue_watchdog_line(text, now)
                    continue

                if not SILENCE_REPROMPT_ENABLED:
                    continue
                if not self._silence_reprompt_due(now):
                    continue
                text = ""
                if hasattr(self.conversation, "generate_silence_reprompt"):
                    try:
                        text = self.conversation.generate_silence_reprompt() or ""
                    except Exception:
                        logger.exception("[REPAIR] reprompt build failed")
                if not text:
                    lang = (getattr(self.conversation.state, "language", "en")
                            or "en")[:2]
                    text = _STT_FALLBACK_PHRASES.get(lang, _STT_FALLBACK_PHRASES["en"])
                self._reprompt_count += 1
                self._last_reprompt_ts = now
                anchor = max(self._tts_end_time, self._last_voice_activity)
                logger.info("[REPAIR] Dead air %.1fs — reprompt %d/%d: %r",
                            now - anchor if anchor else 0.0,
                            self._reprompt_count, SILENCE_REPROMPT_MAX,
                            text[:80])
                self._queue_watchdog_line(text, now)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("[REPAIR] silence watchdog error")

    def _queue_watchdog_line(self, text: str, now: float) -> None:
        """Speak a watchdog-originated line (reprompt / silence goodbye /
        max-duration wrap-up) through the normal TTS path."""
        if not text:
            return
        self._tts_queue.put_nowait(Response(
            text=text, is_shortcut=True, timestamp=now, protected=True))
        if self._hub is not None:
            self._hub.emit_nowait({
                "type": "agent", "text": text,
                "llm_latency_ms": 0, "first_chunk_ms": 0,
                "total_latency_ms": 0,
            })

    def _lang_conformance_check(self, stamp: str | None, text: str) -> None:
        """A6 telemetry (warning only, never behavioral): the language the
        response text actually carries vs the language the LLM was directed
        to answer in. A mismatch is either legitimate code-mix or the exact
        wrong-language-reply class the 2026-07-30 incident belonged to —
        one grep (`RESPONSE-MISMATCH`) now finds every case."""
        try:
            directed = getattr(
                getattr(self.conversation, "state", None), "language", None)
            if (stamp and directed and stamp != directed
                    and directed in ("en", "hi", "gu")):
                logger.warning(
                    "[LANG] RESPONSE-MISMATCH directed=%s got=%s text=%r",
                    directed, stamp, text[:60])
        except Exception:
            pass

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
                # Idle tick: a pending turn transaction whose continuation
                # window has clearly expired can now be committed safely.
                self._maybe_commit_pending_turn()
                continue
            except asyncio.CancelledError:
                break

            # A real caller turn arrived — the dead-air repair cycle resets,
            # and a caller who came back after the silence goodbye gets the
            # full repair + close cycle again before any future silence close.
            self._reprompt_count = 0
            self._silence_hangup_fired = False

            # Deferred-barge verdict (connect delivery guard): the energy
            # burst that would have killed playback is now text. A bare
            # "hello?" means the caller just couldn't hear us — keep
            # playing. Anything else is a real interruption — kill the
            # playback now, exactly as the immediate barge path would have.
            if self._bargein_deferred:
                self._bargein_deferred = False
                _is_hello_defer = self._bare_hello(transcript.text)
                if (not _is_hello_defer
                        and self._tts_playing
                        and self._utt_seq == self._bargein_deferred_utt):
                    logger.info(
                        "[BARGE-IN] Deferred kill — caller said %r "
                        "(not a hello)", transcript.text[:40])
                    self._delivery_epoch += 1   # A5: stale anything dequeued
                    interrupt = getattr(self.sink, "interrupt_playback", None)
                    if interrupt is not None:
                        await interrupt()
                    await self.tts.stop()
                    self._tts_playing = False
                    self._speech_frame_streak = 0
                else:
                    logger.info(
                        "[BARGE-IN] Deferred barge released — %s",
                        "bare hello, playback continues" if _is_hello_defer
                        else "utterance already finished")

            # Debounce: wait a short window for a follow-on segment from the same
            # utterance. VAD may split one sentence at a natural pause and fire
            # two STT results in quick succession. Merging them prevents two
            # separate LLM calls and two back-to-back agent responses.
            # LATENCY: a follow-on can only exist if another transcript is
            # queued, a segment is in STT, or the caller is speaking right
            # now — when none of those hold, the wait is pure dead air and is
            # skipped (was an unconditional ~550ms tax on every turn).
            # B2: while an email/phone capture is armed, the window
            # stretches so letter-by-letter spelling with natural pauses
            # aggregates into ONE turn — three separately-answered
            # fragments is how a live capture attempt fell apart. The
            # idle skip is also disabled: mid-spelling the pipeline IS
            # idle between fragments; that is exactly the pause to absorb.
            _capture_active = (
                CAPTURE_MODE and self.conversation is not None
                and getattr(self.conversation, "_capture_slot", None)
                is not None
                and getattr(self.conversation, "capture_expecting_input",
                            lambda: True)())
            # H5: an utterance ending mid-thought ("हम बेसिकली जो।") predicts
            # a resumed continuation — never fast-path it, and widen the
            # window so the continuation merges instead of racing the LLM.
            _dangling = (
                MERGE_HOLD_CONTINUATION
                and not _capture_active
                and _ends_with_continuation_cue(transcript.text))
            merge_wait_s = (
                CAPTURE_MERGE_WINDOW_MS if _capture_active
                else TRANSCRIPT_MERGE_WINDOW_MS) / 1000.0
            if _dangling:
                merge_wait_s = max(merge_wait_s,
                                   MERGE_HOLD_CONTINUATION_MS / 1000.0)
            if (
                TRANSCRIPT_MERGE_SKIP_WHEN_IDLE
                and not _capture_active
                and not _dangling
                and self._transcript_queue.empty()
                and self._stt_queue.empty()
                and self._stt_inflight == 0
                and not self._prev_speech
            ):
                merge_wait_s = 0.0
                logger.info("[ENGINE] Merge window skipped (idle)")
            elif _dangling:
                logger.info(
                    "[ENGINE] Merge window held %.0fms (dangling ending %r)",
                    merge_wait_s * 1000.0, transcript.text[-24:])
            elif merge_wait_s > 0.0:
                logger.info(
                    "[ENGINE] Merge window %.0fms%s (queued=%d stt_q=%d inflight=%d speaking=%s)",
                    merge_wait_s * 1000.0,
                    " [capture]" if _capture_active else "",
                    self._transcript_queue.qsize(), self._stt_queue.qsize(),
                    self._stt_inflight, self._prev_speech,
                )
            if merge_wait_s > 0.0:
                # In capture mode every merged fragment re-opens the window
                # (up to 8) so a full spelled email lands as one turn.
                _max_follow_ons = 8 if _capture_active else 1
                for _fo_i in range(_max_follow_ons):
                    try:
                        follow_on = await asyncio.wait_for(
                            self._transcript_queue.get(),
                            timeout=merge_wait_s,
                        )
                    except asyncio.TimeoutError:
                        break  # No follow-on within window — proceed
                    time_gap = follow_on.timestamp - transcript.timestamp
                    _gap_cap = 3.0 if not _capture_active else (
                        CAPTURE_MERGE_WINDOW_MS / 1000.0 + 1.0)
                    if (time_gap < _gap_cap and self._bare_hello(transcript.text)
                            and self._bare_hello(follow_on.text)):
                        # Two bare hellos are ONE repair signal, not a
                        # sentence — merging them builds the 'hello hello
                        # hello' garbage that poisons slots and the LLM.
                        logger.info(
                            "[ENGINE] Dropping repeated hello follow-on "
                            "(%.1fs gap)", time_gap)
                    elif time_gap < _gap_cap:
                        # Close in time — continuation of the same utterance; merge.
                        merged_text = transcript.text.rstrip(" .,") + " " + follow_on.text.lstrip()
                        logger.info(
                            "[ENGINE] Merging split segments (%.1fs gap): %r + %r",
                            time_gap, transcript.text[:35], follow_on.text[:35],
                        )
                        transcript = Transcript(
                            text=merged_text,
                            duration_ms=transcript.duration_ms + follow_on.duration_ms,
                            timestamp=follow_on.timestamp,
                            stt_latency_ms=follow_on.stt_latency_ms,
                            # First fragment's onset, last fragment's end.
                            speech_start_ts=transcript.speech_start_ts,
                            speech_end_ts=follow_on.speech_end_ts,
                            # Any recovered fragment taints the merged turn.
                            stt_source=(transcript.stt_source
                                        if transcript.stt_source != "primary"
                                        else follow_on.stt_source),
                        )
                    else:
                        # Gap too large — from a genuinely separate prior turn; discard.
                        logger.info(
                            "[ENGINE] Dropping old segment (%.1fs gap): %r",
                            time_gap, follow_on.text[:40],
                        )

            # Drain any remaining queued transcripts.
            # Transcripts that arrived recently (within 3 s) are very likely
            # continuations of the same utterance — the VAD split a long
            # sentence at a natural pause and two STT results landed in quick
            # succession. Merge them into the current turn so the LLM sees the
            # full sentence. Older transcripts are genuinely stale (leftover
            # from a previous turn while the agent was speaking) and are dropped.
            _drain_now = time.monotonic()
            while not self._transcript_queue.empty():
                try:
                    candidate = self._transcript_queue.get_nowait()
                    age_s = _drain_now - candidate.timestamp
                    if (age_s <= 3.0 and self._bare_hello(transcript.text)
                            and self._bare_hello(candidate.text)):
                        logger.info(
                            "[ENGINE] Dropping repeated hello continuation "
                            "(%.1fs old)", age_s)
                    elif age_s <= 3.0:
                        merged_text = transcript.text.rstrip(" .,") + " " + candidate.text.lstrip()
                        transcript = Transcript(
                            text=merged_text,
                            duration_ms=transcript.duration_ms + candidate.duration_ms,
                            timestamp=candidate.timestamp,
                            stt_latency_ms=candidate.stt_latency_ms,
                            speech_start_ts=transcript.speech_start_ts,
                            speech_end_ts=candidate.speech_end_ts,
                            stt_source=(transcript.stt_source
                                        if transcript.stt_source != "primary"
                                        else candidate.stt_source),
                        )
                        logger.info(
                            "[ENGINE] Merging late continuation (%.1fs): %r + %r",
                            age_s, transcript.text[:40], candidate.text[:40],
                        )
                    else:
                        logger.info(
                            "[ENGINE] Discarding stale transcript (%.1fs old): %r",
                            age_s, candidate.text[:50],
                        )
                except asyncio.QueueEmpty:
                    break

            # ── Continuation merge (pause-tolerant turns) ────────────────────
            # The caller paused mid-utterance, got a fast reply to the partial
            # input, and resumed within the continuation window of their OWN
            # speech end: cut the reply, roll the router back to before the
            # partial turn, and re-run ONE merged utterance.
            now = time.monotonic()
            effective_text = transcript.text
            merged_from_rollback = False
            # A bare "hello?" is a repair signal, never a continuation of the
            # caller's own sentence and never context worth prepending — it
            # must reach the router clean so the replay / instant-intro
            # branches can recognize it (a merged 'hello hello hello hello
            # hello' is >4 tokens and stops matching entirely).
            _hello_turn = self._bare_hello(transcript.text)
            if ENABLE_CONTINUATION_MERGE and self._greeting_done:
                conv = self.conversation
                if (hasattr(conv, "has_pending_turn") and conv.has_pending_turn()):
                    gap_ms = (
                        transcript.speech_start_ts - self._turn_txn_speech_end
                    ) * 1000.0
                    if (
                        not _hello_turn
                        and transcript.speech_start_ts > 0.0
                        and self._turn_txn_speech_end > 0.0
                        and 0.0 <= gap_ms <= CONTINUATION_WINDOW_MS
                        and self._merge_chain_count < CONTINUATION_MAX_MERGES
                    ):
                        # Cut the agent's superseded reply wherever it is:
                        # still generating, queued for TTS, or playing.
                        # A5: the epoch bump also catches the reply the TTS
                        # player has DEQUEUED but not yet started synthesizing
                        # — the 300ms window the queue drain below can't reach.
                        self._delivery_epoch += 1
                        if self._tts_playing:
                            await self.tts.stop()
                            self._tts_playing = False
                        if (
                            self._current_generation_task
                            and not self._current_generation_task.done()
                        ):
                            self._current_generation_task.cancel()
                        while not self._tts_queue.empty():
                            try:
                                self._tts_queue.get_nowait()
                            except asyncio.QueueEmpty:
                                break
                        prev_text = conv.rollback_pending_turn()
                        if prev_text:
                            effective_text = (
                                prev_text.rstrip(" .,") + " "
                                + transcript.text.lstrip()
                            )
                            merged_from_rollback = True
                            self._merge_chain_count += 1
                            # The rollback owns the merge — no Case-A prepend,
                            # and the superseded reply must not anchor the
                            # accumulator-keeping barge-in path.
                            self._turn_accumulator = ""
                            self._barge_in_occurred = False
                            logger.info(
                                "[CONT-MERGE] gap=%.0fms chain=%d: %r + %r → %r",
                                gap_ms, self._merge_chain_count,
                                prev_text[:40], transcript.text[:40],
                                effective_text[:80],
                            )
                            if self._hub is not None:
                                self._hub.emit_nowait({
                                    "type": "turn_merged",
                                    "merged_text": effective_text,
                                    "gap_ms": round(gap_ms),
                                })
                    else:
                        # Genuinely new turn — the previous one is final.
                        conv.commit_pending_turn()
                        self._merge_chain_count = 0

            # ── Turn accumulator (Change 3) ──────────────────────────────────
            # Determine the effective text the LLM will receive this turn.
            #
            # Case A — barge-in context: if the caller interrupted the agent
            # mid-response, _turn_accumulator holds what they said before the
            # interruption. Prepend it so the LLM sees the complete caller turn,
            # not just the barge-in fragment.
            #
            # Case B — post-response continuation (legacy, subsumed by the
            # continuation merge when that feature is ON): if TTS finished
            # cleanly a moment ago and the new transcript is very short
            # (≤ 4 words), it is likely a trailing clause ("for that",
            # "yes, and...") that arrived just after the VAD split. Prepend
            # the previous turn's text.
            if merged_from_rollback:
                pass  # effective_text already merged with full state rollback
            elif _hello_turn:
                # Keep the hello clean — no prior-fragment prepend, no
                # post-response merge. The router's repair branches need the
                # bare form, and 'prior sentence + hello' is noise anyway.
                pass
            elif self._turn_accumulator:
                effective_text = (
                    self._turn_accumulator.rstrip(" .,") + " " + transcript.text.lstrip()
                )
                logger.info(
                    "[ENGINE] Prepending barge-in context to transcript: %r + %r → %r",
                    self._turn_accumulator[:40], transcript.text[:40], effective_text[:60],
                )
            elif (
                not ENABLE_CONTINUATION_MERGE
                and self._last_user_text
                and (now - self._last_tts_end) < POST_RESPONSE_MERGE_MS / 1000.0
                and len(transcript.text.split()) <= 4
            ):
                effective_text = (
                    self._last_user_text.rstrip(" .,") + " " + transcript.text.lstrip()
                )
                logger.info(
                    "[ENGINE] Merging post-response fragment %r with prior turn %r → %r",
                    transcript.text[:35], self._last_user_text[:35], effective_text[:60],
                )
            # Save for potential next barge-in — but never a bare hello:
            # stored hellos become the 'hello hello …' prepend chains.
            if not _hello_turn:
                self._turn_accumulator = effective_text
                self._last_user_text = effective_text
            # Anchor for the next continuation-gap measurement: when the
            # caller's speech for THIS turn actually ended.
            if transcript.speech_end_ts > 0.0:
                self._turn_txn_speech_end = transcript.speech_end_ts
                # Single-grep latency instrument: total dead air between the
                # caller falling silent and the engine starting (endpoint
                # gate + STT + merge window + queue time).
                logger.info(
                    "[LATENCY] speech_end→engine %.0fms (stt=%.0fms)",
                    (time.monotonic() - transcript.speech_end_ts) * 1000.0,
                    transcript.stt_latency_ms,
                )
                # Companion instrument: arm the sink so its FIRST flush of
                # this turn logs speech_end→first_audio — the number the
                # whole latency effort optimizes, previously reconstructible
                # only by hand-joining log timestamps.
                sink = getattr(self.tts, "_sink", None)
                if sink is not None and hasattr(sink, "flush"):
                    sink.turn_speech_end_ts = transcript.speech_end_ts

            # Turn-level trace record. The numbers were always computed —
            # they just died as log lines. Enriched below as the LLM stages
            # complete, by the sink when the first audio flushes, and by the
            # barge-in handler; the bridge batch-persists to call_turns.
            sink = getattr(self.tts, "_sink", None)
            state = getattr(self.conversation, "state", None)
            trace = {
                "turn_index": len(self.turn_traces),
                "ts": time.time(),
                "stage": (str(getattr(state, "conversation_stage", "")
                              or getattr(state, "stage", "") or "")
                          or None),
                "language": getattr(state, "language", None) or None,
                "user_chars": len(effective_text),
                "stt_latency_ms": round(transcript.stt_latency_ms, 1),
                "stt_confidence": getattr(transcript, "confidence", None),
                "speech_end_to_engine_ms": (
                    round((time.monotonic() - transcript.speech_end_ts)
                          * 1000.0, 1)
                    if transcript.speech_end_ts > 0.0 else None),
            }
            if self._session is not None:
                self._session.mark_exchange()
                trace["session_state"] = self._session.state
            if self._endpointer is not None:
                trace["endpoint_profile"] = self._endpointer.last_profile
            if len(self.turn_traces) < 500:
                self.turn_traces.append(trace)
            if sink is not None and hasattr(sink, "flush"):
                sink.turn_trace_ref = trace

            # A3: the router's language-evidence tiering needs to know when
            # a turn came from a mishear-prone recovery path.
            if self.conversation is not None:
                try:
                    self.conversation._turn_stt_source = transcript.stt_source
                except Exception:
                    pass

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
                        effective_text
                    ):

                        if first_result is None:

                            first_result = result

                            first_chunk_ms = (
                                time.monotonic() - latency_start
                            ) * 1000

                        # Fillers play on the call but are NOT part of what the
                        # agent said: excluding them here keeps them out of the
                        # stored transcript, and therefore out of the
                        # intelligence pipeline's extraction and embedding
                        # input (audit: every stored filler was fused onto the
                        # real reply — "One moment please... Hi, hope you're…" —
                        # polluting summaries and vectors). The LLM's own
                        # memory already excluded them.
                        if result.intent != "filler":
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
                                        is_filler=(result.intent == "filler"),
                                        # Replays / connect intros exist
                                        # because the caller missed them —
                                        # shield their delivery from hello
                                        # barge-ins at any call age.
                                        protected=(result.intent in
                                                   ("repeat", "connect_intro")),
                                        replay=(result.intent == "repeat"),
                                        # Fillers are romanized ("Ek minute…")
                                        # and picked BY the state language —
                                        # stamp it so text-derivation can't
                                        # misroute them to en-IN.
                                        language=(
                                            self.conversation.state.language
                                            if (result.intent == "filler"
                                                and self.conversation
                                                and self.conversation.state
                                                .language in ("en", "hi", "gu"))
                                            else None),
                                    )
                                )
                            except asyncio.QueueFull:
                                logger.warning(
                                    "[ENGINE] TTS queue full -- dropping shortcut chunk"
                                )
                        else:
                            # First LLM sentence → TTS immediately to cut
                            # perceived latency. Remaining sentences are buffered
                            # and sent as one follow-up request once the LLM
                            # finishes, avoiding per-sentence TTS gaps.
                            #
                            # Exception: if the first chunk ends on a dangling
                            # conjunction ("but", "and", "however", …), it is an
                            # incomplete thought mid-stream. Sending it alone
                            # produces sentences like "I understand but" that
                            # confuse callers. Buffer it and merge with the next
                            # chunk so the caller hears a complete sentence.
                            _DANGLING_END = re.compile(
                                r'\b(but|and|or|yet|however|also|so|although|'
                                r'though|because|since|if|when|while|after|'
                                r'before|as|that|which|who|whom)\s*[,.]?\s*$',
                                re.IGNORECASE,
                            )
                            if not llm_parts:
                                if _DANGLING_END.search(result.text.rstrip()):
                                    logger.debug(
                                        "[ENGINE] First sentence ends on conjunction — buffering: %r",
                                        result.text[:60],
                                    )
                                    llm_parts.append(result.text)
                                else:
                                    try:
                                        # Accent decided WITH the words: a
                                        # state flip while this waits in the
                                        # queue once spoke an English reply
                                        # as hi-IN.
                                        _stamp = (self.conversation
                                                  .tts_language_for(result.text))
                                        self._lang_conformance_check(
                                            _stamp, result.text)
                                        self._tts_queue.put_nowait(
                                            Response(
                                                text=result.text,
                                                is_shortcut=False,
                                                timestamp=time.monotonic(),
                                                language=_stamp,
                                            )
                                        )
                                        logger.debug(
                                            "[ENGINE] First LLM sentence → TTS immediately: %r",
                                            result.text[:60],
                                        )
                                    except asyncio.QueueFull:
                                        logger.warning(
                                            "[ENGINE] TTS queue full — buffering first sentence"
                                        )
                                        llm_parts.append(result.text)
                            else:
                                llm_parts.append(result.text)

                    # Send remaining LLM sentences (2nd sentence onwards) as
                    # one combined TTS request to avoid inter-sentence gaps.
                    if llm_parts:
                        combined_text = " ".join(llm_parts)
                        try:
                            _stamp = (self.conversation
                                      .tts_language_for(combined_text))
                            self._lang_conformance_check(_stamp, combined_text)
                            self._tts_queue.put_nowait(
                                Response(
                                    text=combined_text,
                                    is_shortcut=False,
                                    timestamp=time.monotonic(),
                                    language=_stamp,
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

                        trace["llm_first_chunk_ms"] = round(first_chunk_ms, 1)
                        trace["llm_total_ms"] = round(engine_latency, 1)
                        trace["agent_chars"] = len(full_text)
                        trace["intent"] = getattr(first_result, "intent",
                                                  None) or None
                        trace["is_filler"] = (
                            getattr(first_result, "intent", "") == "filler")

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

                if self._session is not None:
                    self._session.set("THINKING", "llm turn")
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
                        language="en",
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

            # A5: snapshot the delivery epoch the moment the Response leaves
            # the queue — a supersede event (cont-merge rollback, barge-in)
            # after this point must still be able to kill it.
            _epoch_at_dequeue = self._delivery_epoch

            # Drop genuinely-orphaned responses. Cap is intentionally generous:
            # a multi-sentence streamed response can spend tens of seconds in
            # the queue waiting for earlier chunks to finish speaking. Barge-in
            # already drains this queue via _flush_pipeline_queues(), so this
            # cap only fires on genuinely stuck pipelines.
            age_ms = (time.monotonic() - response.timestamp) * 1000
            if age_ms > 8000:
                logger.warning("[TTS] Dropping stale response (%.0fms old): %r",
                               age_ms, response.text[:50])
                continue

            # A5: a replay exists to fill a silence. If a newer response
            # started playing after this replay was created, the silence is
            # gone — speaking it now double-answers the caller (live case:
            # "Sure, am I speaking with Angelo?" played twice back-to-back).
            if response.replay and self._last_playback_start_ts > response.timestamp:
                logger.info(
                    "[TTS] Dropped replay — a newer response already played: %r",
                    response.text[:50])
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
                        language=lang if lang in ("en", "hi", "gu") else "en",
                    )

                # Preprocess text to avoid gibberish: normalize punctuation that
                # can confuse Sarvam TTS (em-dashes produce audio glitches).
                tts_text = response.text
                tts_text = tts_text.replace("—", ", ").replace("–", ", ")
                tts_text = tts_text.replace("…", ".").replace("…", ".")
                tts_text = tts_text.replace("•", "").replace("·", "")
                # Collapse multiple spaces/newlines that may appear in LLM output
                tts_text = re.sub(r'\s+', ' ', tts_text).strip()

                # A5: last gate before synthesis. Covers the dequeue→synth
                # window (including the stop()/sleep awaits above) where a
                # cont-merge rollback used to lose the race and both the
                # superseded and the merged reply were spoken.
                if self._delivery_epoch != _epoch_at_dequeue:
                    logger.info(
                        "[TTS] Dropped stale response (epoch %d < %d): %r",
                        _epoch_at_dequeue, self._delivery_epoch,
                        tts_text[:50])
                    continue

                logger.info("[TTS] Playing: %r (queued %.0fms ago)", tts_text, age_ms)

                self._tts_playing = True
                if self._session is not None:
                    self._session.set("AI_SPEAKING")
                self._tts_start_time = time.monotonic()
                self._last_playback_start_ts = self._tts_start_time
                # Connect delivery guard bookkeeping: new utterance, nothing
                # of it flushed yet, protected status from the Response.
                self._utt_seq += 1
                self._utt_protected = response.protected
                self._utt_first_flush_ts = 0.0
                # Barge-in guard for THIS utterance.  If this utterance is a
                # filler, or it is the real response that immediately follows a
                # filler, use the longer FILLER_ECHO_GRACE_MS so the filler's
                # own telephony echo (which lands during the next utterance)
                # cannot trip barge-in and cancel the real response.  Every
                # other utterance keeps the normal 250ms guard — full barge-in
                # responsiveness is preserved.
                prev_was_filler = self._last_was_filler
                self._last_was_filler = response.is_filler
                if response.is_filler or prev_was_filler:
                    self._barge_in_min_elapsed_ms = FILLER_ECHO_GRACE_MS
                else:
                    self._barge_in_min_elapsed_ms = BARGE_IN_MIN_ELAPSED_MS
                # Reset the barge-in streak at every utterance boundary so loud
                # frames accumulated from the PREVIOUS utterance's echo cannot
                # trip barge-in on this one.  This is what was cutting off the
                # real response right after a filler ("एक सेकंड…"): the filler's
                # echo built the streak to ~10, then the response started and
                # tripped barge-in the instant it passed the 250ms guard.  A
                # genuine interrupter simply rebuilds the streak (~60ms).
                self._speech_frame_streak = 0

                if hasattr(self.tts, 'set_language') and self.conversation:
                    _lang, _lang_src = self._resolve_tts_language(
                        response, tts_text)
                    self.tts.set_language(_lang)
                    # v2: per-language voice on mid-call language switch —
                    # only when the agent defines a voice for that language
                    # (absent = today's single-voice behaviour).
                    try:
                        _vcfg = self.conversation._def.languages.tts_voices.get(_lang)
                        if _vcfg is not None and hasattr(self.tts, 'set_voice'):
                            self.tts.set_voice(model=_vcfg.model,
                                               speaker=_vcfg.speaker,
                                               pace=getattr(_vcfg, 'pace', None))
                    except Exception:
                        pass
                    # One audit line per utterance: the accent decision used
                    # to be reconstructable only by cross-referencing three
                    # log families.
                    logger.info(
                        "[TTS] lang=%s src=%s speaker=%s",
                        _lang, _lang_src,
                        getattr(self.tts, "_speaker", "?"))

                # Canned lines (greeting, fillers, response-bank phrases) go
                # through the disk cache: repeat plays cost zero synthesis
                # time and zero Sarvam credits. LLM output is never cached.
                if response.is_shortcut and hasattr(self.tts, "speak_cached"):
                    await self.tts.speak_cached(tts_text)
                else:
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
                if self._session is not None:
                    self._session.set("LIVE_CONVERSATION", "tts done")
                self._tts_end_time = time.monotonic()
                # This utterance is over — a still-latched deferral must not
                # leak onto the next one.
                self._bargein_deferred = False

                # Issue 4 / Issue 1: mark greeting as done after the very first
                # TTS utterance finishes.  From this point barge-in and STT
                # fallback phrases are permitted.
                was_greeting = not self._greeting_done
                if not self._greeting_done:
                    self._greeting_done = True
                    logger.info("[PIPELINE] Greeting complete — barge-in and STT fallback now active")

                if self._barge_in_occurred:
                    self._barge_in_occurred = False
                    # Accumulator intentionally kept: the next transcript will be
                    # merged with what the caller said before the interruption.
                else:
                    # Clean completion — caller was fully answered. Clear the
                    # turn accumulator so the next turn starts fresh. Record
                    # when TTS ended for post-response continuation detection.
                    self._turn_accumulator = ""
                    self._last_tts_end = time.monotonic()
                    # Issue 3: In telephony mode the echo of TTS audio returns
                    # via mod_audio_stream with a delay of:
                    #   injection_overhead + audio_playback_duration
                    # The old 400 ms window expired long before the echo arrived,
                    # so agent speech leaked into STT.  Use the actual audio
                    # duration from TelephonySink (if available) to cover the
                    # full echo window.
                    now = time.monotonic()
                    busy_until = getattr(self.sink, 'playback_busy_until', 0.0)
                    audio_dur = getattr(self.sink, 'last_flush_duration', 0.0)
                    # Post-greeting mute gets a shorter echo tail and an
                    # earlier speech exit (see GREETING_ECHO_* constants).
                    self._greeting_echo_window = was_greeting
                    tail_s = (GREETING_ECHO_TAIL_MS / 1000.0
                              if was_greeting else 0.2)
                    if busy_until > 0.0:
                        # With incremental flushing, earlier parts of the
                        # utterance already played on the call WHILE speak()
                        # was still synthesizing later parts.  Anchoring the
                        # full utterance duration at speak()-return (old
                        # formula) therefore muted the pipeline for many
                        # seconds AFTER playback actually ended, swallowing
                        # the caller's reply.  The sink's flush-anchored
                        # playback clock already accounts for audio that has
                        # played; mute only until it says playback ends,
                        # plus a short echo tail.
                        mute_s = max(
                            max(busy_until - now, 0.0) + tail_s,
                            TTS_ECHO_COOLDOWN_MS / 1000.0,
                        )
                        logger.info(
                            "[ECHO-MUTE] Window %.1fs (playback ends in %.1fs "
                            "+ %.1fs tail%s)",
                            mute_s, max(busy_until - now, 0.0), tail_s,
                            ", greeting" if was_greeting else "",
                        )
                    elif audio_dur > 0.0:
                        # Sink tracks flush duration but not a playback
                        # clock: fall back to duration-anchored window.
                        mute_s = 0.7 + audio_dur + 0.2
                        logger.info(
                            "[ECHO-MUTE] Extended window %.1fs "
                            "(audio=%.1fs + 0.7s injection + 0.2s buffer)",
                            mute_s, audio_dur,
                        )
                    else:
                        mute_s = TTS_ECHO_COOLDOWN_MS / 1000.0
                    self._vad_mute_until = now + mute_s

        logger.info("[TTS] Layer stopped")
