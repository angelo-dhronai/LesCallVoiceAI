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

# Repo root on sys.path so `shared.*` (multi-tenant routing DB) imports work
# regardless of the launcher's cwd.
_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

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

    Reads agents.is_active from the shared DB first (the Activate button
    writes it); falls back to agents/active_agent.json, then DEFAULT_AGENT,
    then the first agent folder that has an agent.yaml. Never raises.
    """
    try:
        from shared.db.database import get_db
        with get_db() as db:
            row = db.execute(
                "SELECT slug FROM agents WHERE is_active=1 "
                "ORDER BY updated_at DESC LIMIT 1").fetchone()
        if row and _agent_yaml_exists(row["slug"] or ""):
            return row["slug"]
    except Exception as exc:
        logger.warning("[BRIDGE] DB active-agent lookup unavailable: %r", exc)
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


def _agent_yaml_exists(slug: str) -> bool:
    return bool(slug) and (AGENTS_DIR / slug / "agent.yaml").exists()


def resolve_agent_slug(did: str = "", campaign_id: int | None = None,
                       agent_slug: str = "", dial_meta: dict | None = None) -> str:
    """Resolve WHICH agent answers this call (multi-tenant routing, plan §2).

    Precedence:
      1. explicit agent_slug   — set as a channel variable at originate time
      2. campaign.agent_slug   — outbound campaign binding (shared DB)
      3. phone_numbers lookup  — inbound: the dialed DID's bound agent (shared DB)
      4. dial_meta agent_slug  — the call_link.json dial record (fallback link)
      5. active_agent.json     — legacy single-active-agent behavior

    NEVER raises: if the shared DB is absent/unseeded or a bound slug has no
    agent.yaml on disk, resolution falls through to the legacy path so a live
    call can never be dropped by routing.
    """
    if _agent_yaml_exists(agent_slug):
        return agent_slug
    if agent_slug:
        logger.warning("[BRIDGE] explicit agent slug %r has no agent.yaml — falling through",
                       agent_slug)
    try:
        from shared.db.database import get_db
        from shared.phone import significant_digits
        with get_db() as db:
            if campaign_id is not None:
                row = db.execute("SELECT agent_slug FROM campaigns WHERE id=?",
                                 (campaign_id,)).fetchone()
                if row and _agent_yaml_exists(row["agent_slug"] or ""):
                    return row["agent_slug"]
            if did:
                sig = significant_digits(did)
                if sig:
                    row = db.execute(
                        "SELECT inbound_agent_slug FROM phone_numbers WHERE sig_digits=?",
                        (sig,)).fetchone()
                    if row and _agent_yaml_exists(row["inbound_agent_slug"] or ""):
                        return row["inbound_agent_slug"]
    except Exception as exc:
        logger.warning("[BRIDGE] DB agent routing unavailable (%r) — legacy resolution", exc)
    if dial_meta and _agent_yaml_exists(dial_meta.get("agent_slug", "")):
        return dial_meta["agent_slug"]
    return _resolve_active_agent_slug()


def _dial_matches_phone(caller_number: str, dial: dict | None) -> bool:
    """True only when the dial record's phone exactly suffix-matches the
    caller — the bar for letting a dial record steer agent selection."""
    if not dial:
        return False
    a = "".join(c for c in (caller_number or "") if c.isdigit())[-10:]
    b = "".join(c for c in dial.get("phone", "") if c.isdigit())[-10:]
    return bool(a) and a == b


def _lookup_agent_owner(slug: str) -> str:
    """Tenant email for a slug from the agents index. Best-effort, never raises."""
    try:
        from shared.db.database import get_db
        with get_db() as db:
            row = db.execute("SELECT owner FROM agents WHERE slug=?", (slug,)).fetchone()
            return (row["owner"] or "") if row else ""
    except Exception:
        return ""


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
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        entries: list[dict] = []
        try:
            from shared.db.database import get_db
            window_start = (now - datetime.timedelta(
                seconds=OUTBOUND_DIAL_MATCH_WINDOW_S)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            with get_db() as db:
                entries = [dict(r) for r in db.execute(
                    "SELECT owner, phone, contact_id, name, agent_slug,"
                    " dialed_at FROM call_links WHERE dialed_at >= ?"
                    " ORDER BY dialed_at DESC LIMIT 50",
                    (window_start,)).fetchall()]
        except Exception as exc:
            logger.warning("[BRIDGE] call_links DB lookup failed (%r) — "
                           "using legacy file", exc)
        if not entries and CALL_LINK_FILE.exists():
            entries = (json.loads(CALL_LINK_FILE.read_text(encoding="utf-8"))
                       or [])[-50:]
        digits = "".join(c for c in (caller_number or "") if c.isdigit())[-10:]
        best, best_age = None, None
        for entry in entries:
            try:
                dial_dt = datetime.datetime.fromisoformat(
                    str(entry.get("dialed_at", "")).replace("Z", "+00:00"))
                if dial_dt.tzinfo is None:  # DB timestamps come back naive-UTC
                    dial_dt = dial_dt.replace(tzinfo=datetime.timezone.utc)
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


def _build_agent_stack(conn_id: str, did: str = "", campaign_id: int | None = None,
                       agent_slug: str = "", dial_meta: dict | None = None):
    """Resolve this call's agent and build (slug, conversation, stt, tts).

    Mirrors backend/run.py so telephony runs the data-driven agent identically
    to the CLI/web pipeline: STT corrections + TTS voice come from the agent's
    YAML. A load failure degrades to the default ConversationRouter rather than
    dropping the call.
    """
    slug = resolve_agent_slug(did=did, campaign_id=campaign_id,
                              agent_slug=agent_slug, dial_meta=dial_meta)
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
                 skip_greeting: bool = False, did: str = "", campaign_id: int | None = None,
                 agent_slug: str = ""):
        self.conn_id = conn_id
        self.sample_rate = sample_rate
        self.caller_number = caller_number
        self.did = did
        self.campaign_id = campaign_id
        self.call_key = None  # recording folder basename; set in start()
        self.source = TelephonySource(sample_rate=sample_rate)
        self.sink = TelephonySink(sample_rate=sample_rate, conn_id=conn_id)
        self.hub = EventHub()

        # Outbound dial linkage BEFORE agent resolution: the dial record can
        # carry the agent slug bound at dial time (call_link.json fallback when
        # the channel variable didn't survive the leg).
        dial = None
        try:
            dial = _lookup_recent_outbound_dial(caller_number)
        except Exception as exc:
            logger.warning("[%s] outbound dial lookup failed: %r", conn_id, exc)
        self._dial = dial

        # Routing guard rails:
        # - The loose most-recent dial match is fine for LABELING a call
        #   outbound, but may only steer AGENT selection on an exact phone
        #   match — otherwise an unrelated inbound call arriving during a
        #   dial burst could inherit the dial's agent.
        # - On a confident outbound leg, Caller-Destination-Number is the
        #   CUSTOMER's number, not one of our DIDs — never DID-route on it.
        dial_phone_match = _dial_matches_phone(caller_number, dial)
        confident_outbound = campaign_id is not None or dial_phone_match
        routing_did = "" if confident_outbound else did
        routing_dial_meta = dial if dial_phone_match else None

        # Resolve this call's agent (DID / campaign / explicit slug) and build
        # its STT/TTS the same way run.py does.
        self.agent_slug, conversation, stt, tts = _build_agent_stack(
            conn_id, did=routing_did, campaign_id=campaign_id,
            agent_slug=agent_slug, dial_meta=routing_dial_meta)
        self.direction = "outbound" if (campaign_id is not None or dial) else "inbound"
        self.owner = _lookup_agent_owner(self.agent_slug)
        logger.info("[%s] Active agent: %s (direction=%s did=%s campaign=%s owner=%s)",
                    conn_id, self.agent_slug, self.direction, did or "-",
                    campaign_id if campaign_id is not None else "-", self.owner or "-")

        # Call context: was this session started by an outbound dial? If so,
        # hand the dialed contact's name/number to the agent so it opens by
        # confirming identity instead of asking who it's speaking with.
        try:
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
        self._metering_task = None
        self._running = False

        # Rolling tail of recent transcript turns, for the dashboard's live
        # snapshot writer (see ws_audio_receiver.py). Purely additive — fed
        # from the same hub events already consumed for call-audit logging.
        # 200 turns ≈ the whole call for the dashboard's fullscreen live
        # transcript (6 was only enough for the old card snippet); still a
        # few KB of JSON per snapshot write.
        self.transcript_tail: deque = deque(maxlen=200)

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
        _audit_session = await audit_manager.create(self.conn_id, caller_number=self.caller_number)
        # Capture the recording folder name NOW — it IS the call_key the
        # dashboard history/recordings/analysis are keyed by. The audit session
        # is popped from the manager at close(), so we can't fetch it later in
        # _persist_call_row; stash it on the session here.
        try:
            _cd = getattr(_audit_session, "_call_dir", "") if _audit_session else ""
            if _cd:
                self.call_key = os.path.basename(str(_cd).rstrip("/"))
        except Exception as exc:
            logger.warning("[%s] could not capture call_key: %r", self.conn_id, exc)
        logger.info(f"[{self.conn_id}] Session started (Phase 14) call_key=%s", self.call_key)

        # BILLING: open this call's usage-ledger row and start the 15s
        # heartbeat. Metering must never affect call handling — every touch
        # is wrapped and off the event loop. (shared/usage_metering.py)
        try:
            await asyncio.to_thread(self._metering_open)
            self._metering_task = asyncio.create_task(self._metering_beat_loop())
        except Exception as exc:
            logger.error("[%s] METERING open failed: %r", self.conn_id, exc)
        
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

        # BILLING: stop the heartbeat and close the usage-ledger row. If this
        # ever fails, the reconciler crash-closes the row from its last beat —
        # usage is never lost, at most ~15s under-counted.
        task = getattr(self, "_metering_task", None)
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            from shared.usage_metering import close_call
            await asyncio.to_thread(close_call, self.conn_id)
        except Exception as exc:
            logger.error("[%s] METERING close failed (reconciler will "
                         "recover): %r", self.conn_id, exc)

        # Owner-tagged calls row for the multi-tenant dashboard (plan §2).
        # Off the event loop: a contended sqlite write (busy_timeout 5s) must
        # never stall audio for other live calls sharing this loop.
        try:
            await asyncio.to_thread(self._persist_call_row)
        except Exception as exc:
            logger.warning("[%s] Call row persistence failed: %r", self.conn_id, exc)

        # Push the finished WAV to GCS (best-effort, off the loop). Failures
        # are retried by the recording-sync pass (shared/recording_storage.py
        # CLI); until then the local file remains the serving copy.
        try:
            from shared.recording_storage import gcs_enabled, upload_recording
            if self.call_key and gcs_enabled():
                await asyncio.to_thread(upload_recording, self.call_key)
        except Exception as exc:
            logger.warning("[%s] Recording upload failed: %r", self.conn_id, exc)

        logger.info(f"[{self.conn_id}] Session stopped")

    # ------------------------------------------------------------------
    # BILLING metering hooks (shared/usage_metering.py). The ledger is the
    # basis for charging users; these must be correct AND must never take
    # a call down. All DB writes run in worker threads.
    # ------------------------------------------------------------------

    def _metering_owner(self) -> str:
        return self.owner or os.environ.get("LESCALL_DEFAULT_OWNER",
                                            "tech@dhronai.com")

    def _metering_open(self):
        from shared.usage_metering import open_call
        phone = self.caller_number
        dial = getattr(self, "_dial", None)
        if self.direction == "outbound" and dial and dial.get("phone"):
            phone = dial["phone"]
        open_call(self.conn_id, self._metering_owner(),
                  agent_slug=self.agent_slug, direction=self.direction,
                  phone=phone or None)

    async def _metering_beat_loop(self):
        from shared.usage_metering import heartbeat
        try:
            while self._running:
                await asyncio.sleep(15)
                if not self._running:
                    break
                try:
                    await asyncio.to_thread(heartbeat, self.conn_id)
                except Exception as exc:
                    logger.warning("[%s] METERING heartbeat failed: %r",
                                   self.conn_id, exc)
        except asyncio.CancelledError:
            pass

    def _collect_call_media(self, call_key):
        """Gather transcript/summary/collected-data for the calls row.

        Everything is best-effort: a missing transcript file or an agent
        without a state manager must never block the row write."""
        import json
        from shared.recording_storage import RECORDINGS_DIR

        transcript = None
        recording_path = None
        if call_key:
            base = str(RECORDINGS_DIR / call_key)
            t_path = os.path.join(base, "transcript.txt")
            try:
                if os.path.exists(t_path):
                    with open(t_path, "r", encoding="utf-8", errors="replace") as f:
                        transcript = f.read().strip() or None
            except Exception as exc:
                logger.warning("[%s] transcript read failed: %r", self.conn_id, exc)
            wav = os.path.join(base, "caller_audio.wav")
            if os.path.exists(wav):
                recording_path = wav

        summary = None
        collected = None
        try:
            conv = getattr(self.pipeline, "conversation", None)
            sm = getattr(conv, "state_mgr", None)
            if sm is not None:
                data = sm.to_dict()
                summary = data.get("summary") or None
                collected = json.dumps(data, ensure_ascii=False)
        except Exception as exc:
            logger.warning("[%s] collected-data snapshot failed: %r", self.conn_id, exc)
        return transcript, summary, collected, recording_path

    def _resolve_contact_id(self, db, phone: str) -> int | None:
        """Bind this call to the owner's phone-book contact (by the last 10
        significant digits); auto-create a bare contact for unknown callers so
        EVERY call carries a contact_id. Same number under a different owner
        is a different contact — lookup is always owner-scoped."""
        from shared.phone import significant_digits

        sig = significant_digits(phone)
        if not sig:
            return None
        owner = self.owner or os.environ.get("LESCALL_DEFAULT_OWNER", "tech@dhronai.com")
        row = db.execute(
            "SELECT id FROM contacts WHERE owner=? AND sig_digits=?",
            (owner, sig)).fetchone()
        if row:
            return row["id"]
        # Unknown caller — create a minimal contact (implicit "No group").
        name = ""
        dial = getattr(self, "_dial", None)
        if dial and dial.get("name"):
            name = dial["name"]
        else:
            try:
                name = getattr(self.pipeline.conversation.state, "customer_name", "") or ""
            except Exception:
                pass
        legacy_id = str(int(time.time() * 1000)) + "_" + phone[-4:]
        db.execute(
            "INSERT INTO contacts (legacy_id, owner, phone, sig_digits, name, "
            "notes, call_status, disposition, call_count, extra, added_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP) "
            "ON CONFLICT (owner, sig_digits) DO NOTHING",
            (legacy_id, owner, phone, sig, name,
             "auto-created from call", "not_called", "not_classified", 0,
             '{"call_history": []}'))
        row = db.execute(
            "SELECT id FROM contacts WHERE owner=? AND sig_digits=?",
            (owner, sig)).fetchone()
        return row["id"] if row else None

    def _persist_call_row(self):
        """Best-effort INSERT into shared DB `calls`; must never block teardown."""
        import datetime
        from shared.db.database import get_db
        from shared.phone import significant_digits
        from shared.recording_storage import new_download_token, public_download_url

        lang = ""
        try:
            lang = getattr(self.pipeline.conversation.state, "language", "") or ""
        except Exception:
            pass
        started = self.stats.get("start_time") or time.time()
        ended = time.time()

        # On outbound legs the channel's caller id is OUR origination number;
        # the customer's number lives in the dial record.
        phone = self.caller_number
        dial = getattr(self, "_dial", None)
        if self.direction == "outbound" and dial and dial.get("phone"):
            phone = dial["phone"]

        def _iso(t: float) -> str:
            return datetime.datetime.fromtimestamp(
                t, datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

        # Recording folder basename == the call_key the dashboard history/
        # analysis/recordings are all keyed by. Captured in start() (the audit
        # session is gone by now), it's what lets the owner-scoped history map a
        # recording folder back to its tenant.
        call_key = self.call_key
        transcript, summary, collected, recording_path = self._collect_call_media(call_key)

        # Public download link: a fresh 256-bit random token per call. On a
        # reconnect upsert the FIRST segment's token wins (COALESCE on the
        # existing row) so an already-shared link keeps working.
        token = new_download_token()
        rec_url = public_download_url(token)

        # A mid-call WS reconnect produces two sessions with one conn_id; the
        # upsert extends the first row instead of dropping the second segment.
        with get_db() as db:
            contact_id = None
            try:
                if phone:
                    contact_id = self._resolve_contact_id(db, phone)
            except Exception as exc:
                logger.warning("[%s] contact binding failed: %r", self.conn_id, exc)
            db.execute(
                "INSERT INTO calls (conn_id, call_key, direction, agent_slug, campaign_id, "
                "phone, sig_digits, language, started_at, ended_at, duration_s, owner, "
                "transcript, summary, collected_data, recording_path, "
                "download_token, recording_url, contact_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT (conn_id) DO UPDATE SET "
                "call_key=COALESCE(excluded.call_key, calls.call_key), "
                "ended_at=excluded.ended_at, "
                "duration_s=COALESCE(calls.duration_s,0)+excluded.duration_s, "
                "language=COALESCE(NULLIF(excluded.language,''), calls.language), "
                "transcript=COALESCE(excluded.transcript, calls.transcript), "
                "summary=COALESCE(excluded.summary, calls.summary), "
                "collected_data=COALESCE(excluded.collected_data, calls.collected_data), "
                "recording_path=COALESCE(excluded.recording_path, calls.recording_path), "
                "download_token=COALESCE(calls.download_token, excluded.download_token), "
                "recording_url=COALESCE(calls.recording_url, excluded.recording_url), "
                "contact_id=COALESCE(calls.contact_id, excluded.contact_id)",
                (self.conn_id, call_key, self.direction, self.agent_slug, self.campaign_id,
                 phone or None, significant_digits(phone) or None,
                 lang or None, _iso(started), _iso(ended), round(ended - started, 1),
                 self.owner or None,
                 transcript, summary, collected, recording_path,
                 token, rec_url, contact_id),
            )

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

    async def get_or_create(self, conn_id: str, sample_rate: int, caller_number: str = "",
                            did: str = "", campaign_id: int | None = None,
                            agent_slug: str = "") -> TelephonySession:
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
                                       campaign_id=campaign_id, agent_slug=agent_slug)
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
