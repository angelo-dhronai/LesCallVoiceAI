import asyncio
import logging
import json
import struct
import time
import wave
from collections import deque
from typing import Dict

import sys
import os

# Add calling-agent to Python path to allow imports
sys.path.insert(0, os.path.abspath('calling-agent'))

from pathlib import Path

from call_audit import audit_manager

from backend.app.voice.pipeline import VoicePipeline
from backend.app.voice.event_hub import EventHub
from backend.app.audio_transport.telephony_source import TelephonySource
from backend.app.audio_transport.telephony_sink import TelephonySink

# Data-driven agent runtime (replaces the old hardcoded ConversationRouter).
# Telephony resolves the operator-selected "active" agent per call and runs it
# through the exact same stack run.py uses for the CLI/web pipeline.
from backend.app.voice.conversation import ConversationRouter
from backend.app.voice.stt.router import STTRouter
from backend.app.voice.tracking_stt import TrackingSarvamSTT, TrackingWhisperSTT
from backend.app.voice.tracking_tts import TrackingSarvamTTS
from backend.app.voice.credit_tracker import SarvamCreditTracker
from backend.app.agent_definition.registry import agent_registry

logger = logging.getLogger("call_bridge")

# Telephony playback injection server details
PLAYBACK_HOST = "127.0.0.1"
PLAYBACK_PORT = 9004

# ---------------------------------------------------------------------------
# Active-agent resolution (single-active-agent model)
# ---------------------------------------------------------------------------
# Agents are YAML folders under calling-agent/agents/<slug>/agent.yaml. The
# dashboard's Agent Builder "Activate" button writes agents/active_agent.json
# ({"active": "<slug>"}); every inbound call runs that agent. Multi-agent /
# per-DID routing can be layered on later via routing.yaml + resolve_agent().
AGENTS_DIR = Path(os.path.abspath("calling-agent")) / "agents"
ACTIVE_AGENT_FILE = AGENTS_DIR / "active_agent.json"
DEFAULT_AGENT = os.environ.get("DEFAULT_AGENT", "pramukh")

# Start the registry hot-reload watcher once for this process so edits made in
# the builder take effect on the next call without a restart.
try:
    agent_registry.start_watcher(AGENTS_DIR)
except Exception as _exc:  # pragma: no cover - watcher is best-effort
    logger.warning("[BRIDGE] registry watcher not started: %r", _exc)


def _resolve_active_agent_slug() -> str:
    """Slug of the agent that should answer inbound calls.

    Reads agents/active_agent.json; falls back to DEFAULT_AGENT, then to the
    first agent folder that has an agent.yaml. Never raises.
    """
    try:
        if ACTIVE_AGENT_FILE.exists():
            data = json.loads(ACTIVE_AGENT_FILE.read_text(encoding="utf-8")) or {}
            slug = data.get("active")
            if slug and (AGENTS_DIR / slug / "agent.yaml").exists():
                return slug
    except Exception as exc:
        logger.warning("[BRIDGE] Could not read active_agent.json: %r", exc)
    if (AGENTS_DIR / DEFAULT_AGENT / "agent.yaml").exists():
        return DEFAULT_AGENT
    try:
        for p in sorted(AGENTS_DIR.iterdir()):
            if p.is_dir() and not p.name.startswith("_") and (p / "agent.yaml").exists():
                return p.name
    except Exception:
        pass
    return DEFAULT_AGENT


# ---------------------------------------------------------------------------
# Outbound dial linkage
# ---------------------------------------------------------------------------
# The dashboard logs every outbound dial (phone, contact name, timestamp) to
# dashboard/call_link.json just before running the FreeSWITCH originate. When
# a pipeline session starts shortly after a logged dial, this call is treated
# as that outbound call and the contact's name/number are handed to the agent
# so it can greet the person by name and confirm identity.
CALL_LINK_FILE = Path(os.path.abspath("dashboard")) / "call_link.json"
OUTBOUND_DIAL_MATCH_WINDOW_S = 120.0


def _lookup_recent_outbound_dial(caller_number: str = "") -> dict | None:
    """Most recent dial logged within the match window; prefers a phone match.

    Best-effort timestamp heuristic (same contract as the dashboard's own
    _classify_direction) — returns None on any doubt so inbound calls are
    never mislabeled outbound.
    """
    try:
        if not CALL_LINK_FILE.exists():
            return None
        entries = json.loads(CALL_LINK_FILE.read_text(encoding="utf-8")) or []
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        digits = "".join(c for c in (caller_number or "") if c.isdigit())[-10:]
        best, best_age = None, None
        for entry in entries[-50:]:
            try:
                dial_dt = datetime.datetime.fromisoformat(
                    entry.get("dialed_at", "").replace("Z", "+00:00"))
            except Exception:
                continue
            age = (now - dial_dt).total_seconds()
            if not (0 <= age <= OUTBOUND_DIAL_MATCH_WINDOW_S):
                continue
            entry_digits = "".join(c for c in entry.get("phone", "") if c.isdigit())[-10:]
            if digits and entry_digits and digits == entry_digits:
                return entry  # exact phone match wins immediately
            if best_age is None or age < best_age:
                best, best_age = entry, age
        return best
    except Exception as exc:
        logger.warning("[BRIDGE] outbound dial lookup failed: %r", exc)
        return None


def _build_agent_stack(conn_id: str):
    """Resolve the active agent and build (slug, conversation, stt, tts) for a call.

    Mirrors backend/run.py so telephony runs the data-driven agent identically
    to the CLI/web pipeline: STT corrections + TTS voice come from the agent's
    YAML. A load failure degrades to the default ConversationRouter rather than
    dropping the call.
    """
    slug = _resolve_active_agent_slug()
    agent_def = None
    try:
        agent_def = agent_registry.get(slug)
    except Exception as exc:
        logger.error("[%s] [BRIDGE] Failed to load agent '%s': %r — using default",
                     conn_id, slug, exc)

    call_id_provider = lambda: conn_id  # noqa: E731 - tiny provider closure
    corrections = agent_def.knowledge_base.stt_corrections if agent_def else None
    stt = STTRouter(
        primary=TrackingSarvamSTT(call_id_provider=call_id_provider),
        fallback=TrackingWhisperSTT(call_id_provider=call_id_provider),
        stt_corrections=corrections,
    )
    tts_model, tts_speaker, tts_pace = "bulbul:v3", "shubh", 1.0
    if agent_def:
        dlang = agent_def.languages.default
        vcfg = agent_def.languages.tts_voices.get(dlang)
        if vcfg:
            tts_model, tts_speaker = vcfg.model, vcfg.speaker
            tts_pace = getattr(vcfg, "pace", 1.0) or 1.0
    tts = TrackingSarvamTTS(
        call_id_provider=call_id_provider,
        model=tts_model,
        speaker=tts_speaker,
        pace=tts_pace,
    )
    conversation = ConversationRouter(agent_def=agent_def)
    return slug, conversation, stt, tts

class TelephonySession:
    """Manages one AI pipeline per phone call."""
    def __init__(self, conn_id: str, sample_rate: int, caller_number: str = "",
                 skip_greeting: bool = False):
        self.conn_id = conn_id
        self.sample_rate = sample_rate
        self.caller_number = caller_number
        self.source = TelephonySource(sample_rate=sample_rate)
        self.sink = TelephonySink(sample_rate=sample_rate, conn_id=conn_id)
        self.hub = EventHub()

        # Resolve the operator-selected active agent and build its STT/TTS the
        # same way run.py does, then run it through the telephony endpoints.
        self.agent_slug, conversation, stt, tts = _build_agent_stack(conn_id)
        logger.info("[%s] Active agent: %s", conn_id, self.agent_slug)

        # Call context: was this session started by an outbound dial? If so,
        # hand the dialed contact's name/number to the agent so it opens by
        # confirming identity instead of asking who it's speaking with.
        try:
            dial = _lookup_recent_outbound_dial(caller_number)
            if dial:
                conversation.set_call_context(
                    direction="outbound",
                    contact_name=dial.get("name", ""),
                    contact_phone=dial.get("phone", ""),
                    conn_id=conn_id,
                )
                logger.info("[%s] Outbound call context: contact=%r phone=%s",
                            conn_id, dial.get("name"), dial.get("phone"))
            else:
                conversation.set_call_context(conn_id=conn_id)
        except Exception as exc:
            logger.warning("[%s] set_call_context failed: %r", conn_id, exc)

        # Initialize VoicePipeline with telephony endpoints + data-driven agent
        self.pipeline = VoicePipeline(
            audio_source=self.source,
            audio_sink=self.sink,
            event_hub=self.hub,
            conversation=conversation,
            stt=stt,
            tts=tts,
            mic_enabled=True,
            skip_greeting=skip_greeting,
        )
        self.pipeline_task = None
        self.playback_task = None
        self.event_task = None
        self._running = False

        # Rolling tail of recent transcript turns, for the dashboard's live
        # snapshot writer (see ws_audio_receiver.py). Purely additive — fed
        # from the same hub events already consumed for call-audit logging.
        self.transcript_tail: deque = deque(maxlen=6)

        # Diagnostics
        self.stats = {
            "first_audio_t": None,
            "first_transcript_t": None,
            "first_response_t": None,
            "first_playback_t": None,
            "playback_count": 0,
            "start_time": time.time(),
        }
        
    async def start(self):
        """Starts the AI pipeline and the playback injection loop."""
        self._running = True
        SarvamCreditTracker.get().start_call(self.conn_id)
        self.pipeline_task = asyncio.create_task(self.pipeline.run_until_shutdown())
        self.playback_task = asyncio.create_task(self._playback_loop())
        self.event_task = asyncio.create_task(self._event_loop())
        await audit_manager.create(self.conn_id, caller_number=self.caller_number)
        logger.info(f"[{self.conn_id}] Session started (Phase 14)")
        
    async def stop(self):
        """Stops and cleans up the session."""
        self._running = False
        
        # PHASE17_DEBUG_DISABLE_BARGE_IN_START
        # Wait briefly for any pending playback to enqueue
        if not self.sink.wav_queue.empty():
            logger.info(f"[{self.conn_id}] [PLAYBACK_PRESERVATION] Draining wav_queue before stop")
            for _ in range(10):
                if self.sink.wav_queue.empty():
                    break
                await asyncio.sleep(0.1)
        # PHASE17_DEBUG_DISABLE_BARGE_IN_END

        if self.pipeline_task:
            await self.pipeline.stop()
            self.pipeline_task.cancel()
            try:
                await self.pipeline_task
            except asyncio.CancelledError:
                pass
        
        for t in [self.playback_task, self.event_task]:
            if t:
                # PHASE17_DEBUG_DISABLE_BARGE_IN_START
                # Make sure we don't hard-cancel mid-injection if we can help it, 
                # though `_playback_loop` checks `self._running`. We will let it gracefully exit.
                # PHASE17_DEBUG_DISABLE_BARGE_IN_END
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
                
        await self.source.stop()
        await self.sink.stop()
        self._print_summary()

        # Persist the conversation-state snapshot (slots + audit trail) so every
        # call leaves a machine-readable record of what was collected and when.
        try:
            conv = getattr(self.pipeline, "conversation", None)
            sm = getattr(conv, "state_mgr", None)
            if sm is not None:
                # Cancel any in-flight LLM slot extractor BEFORE snapshotting so
                # a late merge can't land after the file is written.
                task = getattr(conv, "_slot_extract_task", None)
                if task is not None and not task.done():
                    task.cancel()
                call_dir = audit_manager.call_dir(self.conn_id) if hasattr(audit_manager, "call_dir") else None
                if not call_dir:
                    call_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            "recordings", "_state")
                os.makedirs(call_dir, exist_ok=True)
                state_path = os.path.join(call_dir, f"state_{self.conn_id}.json")
                with open(state_path, "w", encoding="utf-8") as f:
                    f.write(sm.to_json())
                logger.info("[%s] Conversation state persisted: %s", self.conn_id, state_path)
        except Exception as exc:
            logger.warning("[%s] State persistence failed: %r", self.conn_id, exc)

        # Close audit session — collect language from conversation state
        try:
            lang = "unknown"
            fallback_used = False
            if hasattr(self.pipeline, "conversation") and hasattr(self.pipeline.conversation, "state"):
                lang = getattr(self.pipeline.conversation.state, "language", "unknown") or "unknown"
            await audit_manager.close(
                self.conn_id,
                language_detected=lang,
                stt_provider="sarvam",
                fallback_used=fallback_used,
            )
        except Exception as exc:
            logger.warning("[%s] Audit close failed: %r", self.conn_id, exc)

        try:
            SarvamCreditTracker.get().end_call(self.conn_id)
        except Exception as exc:
            logger.warning("[%s] Credit end_call failed: %r", self.conn_id, exc)

        logger.info(f"[{self.conn_id}] Session stopped")

    async def _event_loop(self):
        """Listens for AI pipeline events to capture first_transcript_t."""
        queue = self.hub.subscribe()
        try:
            while self._running:
                event = await queue.get()
                etype = event.get("type")
                if etype == "user":
                    if self.stats["first_transcript_t"] is None:
                        self.stats["first_transcript_t"] = time.time()
                        logger.info(f"[{self.conn_id}] First transcript received")
                    audit_manager.add_event(self.conn_id, "Caller", event.get("text", ""))
                    self.transcript_tail.append({"speaker": "Caller", "text": event.get("text", "")})
                elif etype == "agent":
                    audit_manager.add_event(self.conn_id, "Agent", event.get("text", ""))
                    self.transcript_tail.append({"speaker": "Agent", "text": event.get("text", "")})
        except asyncio.CancelledError:
            pass
        finally:
            self.hub.unsubscribe(queue)
        
    def push_audio(self, pcm: bytes):
        """Called by ws_audio_receiver when a PCM frame arrives."""
        if not self._running:
            return
        if self.stats["first_audio_t"] is None:
            self.stats["first_audio_t"] = time.time()
        self.source.push_frame(pcm)

    async def _playback_loop(self):
        """Polls the TelephonySink for finalized WAV files and injects them."""
        while self._running:
            try:
                wav_path = await self.sink.wav_queue.get()
                
                # Task 3: Playback Enqueue Observability
                duration = 0.0
                size = 0
                try:
                    size = os.path.getsize(wav_path)
                    with wave.open(wav_path, 'rb') as wf:
                        frames = wf.getnframes()
                        rate = wf.getframerate()
                        duration = frames / float(rate)
                except Exception as e:
                    logger.warning(f"[{self.conn_id}] Could not parse WAV info for {wav_path}: {e}")

                logger.info("[%s] [PLAYBACK_ENQUEUE] path=%s duration=%.2fs bytes=%d ts=%.3f",
                            self.conn_id, wav_path, duration, size, time.time())
                
                if self.stats["first_response_t"] is None:
                    self.stats["first_response_t"] = time.time()

                # Send to socket_server
                success = await self._send_playback_request(wav_path, duration, size)
                
                if success:
                    if self.stats["first_playback_t"] is None:
                        self.stats["first_playback_t"] = time.time()
                    self.stats["playback_count"] += 1
                    logger.info("[%s] [PLAYBACK_COMPLETE] path=%s duration=%.2fs bytes=%d ts=%.3f",
                                self.conn_id, wav_path, duration, size, time.time())
                
                # Pace the injection to prevent overlapping AI speech
                if duration > 0:
                    logger.info(f"[{self.conn_id}] [PLAYBACK_PACE] Sleeping for {duration:.2f}s duration")
                    await asyncio.sleep(duration + 0.1) # slight buffer
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self.conn_id}] [PLAYBACK_ERROR] Loop error: {e}")
                
    async def _send_playback_request(self, wav_path: str, duration: float, size: int) -> bool:
        """Connects to socket_server.py to trigger playback."""
        logger.info("[%s] [PLAYBACK_SEND] path=%s duration=%.2fs bytes=%d ts=%.3f",
                    self.conn_id, wav_path, duration, size, time.time())
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(PLAYBACK_HOST, PLAYBACK_PORT),
                timeout=2.0
            )
            req = {"type": "playback_request", "conn_id": self.conn_id, "wav_path": wav_path}
            payload = json.dumps(req).encode("utf-8")
            
            # Send 4-byte big-endian length + JSON payload
            writer.write(struct.pack(">I", len(payload)) + payload)
            await writer.drain()
            
            # Wait for ack
            try:
                ack_hdr = await asyncio.wait_for(reader.readexactly(4), timeout=2.0)
                (length,) = struct.unpack(">I", ack_hdr)
                ack_payload = await asyncio.wait_for(reader.readexactly(length), timeout=2.0)
                ack = json.loads(ack_payload.decode("utf-8"))
                
                if ack.get("ok"):
                    writer.close()
                    await writer.wait_closed()
                    return True
                else:
                    logger.error(f"[{self.conn_id}] [PLAYBACK_RESULT] Failed: {ack.get('reason')}")
            except Exception as e:
                logger.warning(f"[{self.conn_id}] [PLAYBACK_RESULT] Ack failed: {e}")
            
            writer.close()
            await writer.wait_closed()
            # If we sent it, we assume success unless ack says otherwise
            return True
        except Exception as e:
            logger.error(f"[{self.conn_id}] [PLAYBACK_ERROR] Failed to inject playback: {e}")
            return False

    def _print_summary(self):
        """Logs the Phase 14 Session Summary."""
        st = self.stats
        t0 = st["start_time"]
        
        def _delta(t):
            return f"{t - t0:.3f}s" if t else "—"

        logger.info("---------- PHASE-14 SESSION SUMMARY ---")
        logger.info(f"    conn_id               {self.conn_id}")
        logger.info(f"    first_audio           {_delta(st['first_audio_t'])}")
        logger.info(f"    first_transcript      {_delta(st['first_transcript_t'])}")
        logger.info(f"    first_response        {_delta(st['first_response_t'])}")
        logger.info(f"    first_playback        {_delta(st['first_playback_t'])}")
        logger.info(f"    playback_count        {st['playback_count']}")
        logger.info(f"    source_queue_depth    {self.source.stats['queue_size']}")
        logger.info(f"    source_dropped        {self.source.stats['dropped']}")
        logger.info("---------------------------------------")


class SessionManager:
    """Manages all active telephony sessions."""

    # Issue 3 & 4: process-level registry of greeted calls.
    # Prevents duplicate greetings when FreeSWITCH reconnects the WebSocket
    # for the same call (e.g., transient network glitch). TTL of 30 minutes
    # covers long calls where a mid-call reconnect should not replay the greeting.
    _greeted: Dict[str, float] = {}
    _GREETING_TTL_S: float = 1800.0

    def __init__(self):
        self.sessions: Dict[str, TelephonySession] = {}

    async def get_or_create(self, conn_id: str, sample_rate: int, caller_number: str = "") -> TelephonySession:
        if conn_id not in self.sessions:
            now = time.time()
            # Prune stale greeted entries to avoid unbounded growth
            expired = [k for k, ts in SessionManager._greeted.items()
                       if now - ts > SessionManager._GREETING_TTL_S]
            for k in expired:
                del SessionManager._greeted[k]

            # Skip greeting if this conn_id was already greeted recently
            skip_greeting = (
                conn_id in SessionManager._greeted
                and now - SessionManager._greeted[conn_id] < SessionManager._GREETING_TTL_S
            )
            if skip_greeting:
                logger.info("[BRIDGE] Reconnect detected for conn_id=%s — greeting suppressed", conn_id)

            session = TelephonySession(conn_id, sample_rate, caller_number=caller_number,
                                       skip_greeting=skip_greeting)
            self.sessions[conn_id] = session

            # Mark as greeted on first creation (before start so no race)
            if not skip_greeting:
                SessionManager._greeted[conn_id] = now

            await session.start()
        return self.sessions[conn_id]

    def push_audio(self, conn_id: str, pcm: bytes):
        if conn_id in self.sessions:
            self.sessions[conn_id].push_audio(pcm)

    async def remove(self, conn_id: str):
        if conn_id in self.sessions:
            session = self.sessions.pop(conn_id)
            await session.stop()

# Global registry
call_bridge = SessionManager()
