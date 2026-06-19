import asyncio
import logging
import json
import struct
import time
import wave
from typing import Dict

import sys
import os

# Add calling-agent to Python path to allow imports
sys.path.insert(0, os.path.abspath('calling-agent'))

from call_audit import audit_manager

from backend.app.voice.pipeline import VoicePipeline
from backend.app.voice.event_hub import EventHub
from backend.app.audio_transport.telephony_source import TelephonySource
from backend.app.audio_transport.telephony_sink import TelephonySink

logger = logging.getLogger("call_bridge")

# Telephony playback injection server details
PLAYBACK_HOST = "127.0.0.1"
PLAYBACK_PORT = 9004

class TelephonySession:
    """Manages one AI pipeline per phone call."""
    def __init__(self, conn_id: str, sample_rate: int, caller_number: str = "",
                 skip_greeting: bool = False, did: str = "", campaign_id: int | None = None):
        self.conn_id = conn_id
        self.sample_rate = sample_rate
        self.caller_number = caller_number
        self.did = did
        self.campaign_id = campaign_id
        self.source = TelephonySource(sample_rate=sample_rate)
        self.sink = TelephonySink(sample_rate=sample_rate, conn_id=conn_id)
        self.hub = EventHub()

        # Resolve the DB-backed agent for this call (inbound by DID, outbound by
        # campaign). Best-effort: falls back to the default hardcoded persona.
        try:
            from backend.app.voice.agent_config import load_agent
            agent_config = load_agent(did=did, campaign_id=campaign_id)
            logger.info("[%s] agent=%r (did=%s campaign=%s)",
                        conn_id, getattr(agent_config, "name", "?"), did, campaign_id)
        except Exception as e:
            logger.warning("[%s] agent load failed (%r); using default", conn_id, e)
            agent_config = None
        self.agent_config = agent_config

        # Initialize VoicePipeline with telephony endpoints
        self.pipeline = VoicePipeline(
            audio_source=self.source,
            audio_sink=self.sink,
            event_hub=self.hub,
            mic_enabled=True,
            skip_greeting=skip_greeting,
            call_id=conn_id,
            agent_config=agent_config,
        )
        self.pipeline_task = None
        self.playback_task = None
        self.event_task = None
        self._running = False
        
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
        self.pipeline_task = asyncio.create_task(self.pipeline.run_until_shutdown())
        self.playback_task = asyncio.create_task(self._playback_loop())
        self.event_task = asyncio.create_task(self._event_loop())
        await audit_manager.create(self.conn_id, caller_number=self.caller_number,
                                   owner=getattr(self.agent_config, "owner", None),
                                   direction="outbound" if self.campaign_id else "inbound")
        logger.info(f"[{self.conn_id}] Session started (Phase 14)")
        self._publish_live("started")
        
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
                    self._publish_live("transcript", role="caller", text=event.get("text", ""))
                elif etype == "agent":
                    audit_manager.add_event(self.conn_id, "Agent", event.get("text", ""))
                    self._publish_live("transcript", role="agent", text=event.get("text", ""))
        except asyncio.CancelledError:
            pass
        finally:
            self.hub.unsubscribe(queue)

    def _publish_live(self, kind: str, **fields):
        """Best-effort publish of a live call event to Redis (no-op if unconfigured)."""
        try:
            from shared.events import publish_call_event
            publish_call_event(self.conn_id, kind, caller=self.caller_number,
                               did=self.did, campaign_id=self.campaign_id, **fields)
        except Exception:
            pass

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

    async def get_or_create(self, conn_id: str, sample_rate: int, caller_number: str = "",
                            did: str = "", campaign_id: int | None = None) -> TelephonySession:
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
                                       skip_greeting=skip_greeting, did=did,
                                       campaign_id=campaign_id)
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
