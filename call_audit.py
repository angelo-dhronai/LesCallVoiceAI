"""
call_audit.py — Call Audit System

Records caller audio, transcripts, and metadata for every phone call.

Enabled by:  ENABLE_CALL_AUDIT=true
Disabled by: ENABLE_CALL_AUDIT=false  (zero overhead — early-exit everywhere)

Directory layout:
    recordings/
    YYYYMMDD/
        <call_id>/
            caller_audio.wav   (PCM16, 16kHz, mono)
            transcript.txt
            metadata.json
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import wave
from datetime import datetime, timezone
from typing import Dict, Optional

logger = logging.getLogger("call_audit")

ENABLE_CALL_AUDIT = os.environ.get("ENABLE_CALL_AUDIT", "false").lower() == "true"

RECORDINGS_ROOT = os.environ.get(
    "CALL_AUDIT_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "recordings"),
)

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2  # PCM16 = 2 bytes per sample
CHANNELS = 1

_AUDIO_QUEUE_MAXSIZE = 200  # ~4s of 20ms frames at 16kHz; excess frames dropped


class CallAuditSession:
    """Per-call recording session. All writes are non-blocking."""

    def __init__(self, call_id: str, caller_number: str = "",
                 agent_slug: str = "", agent_revision: Optional[int] = None,
                 agent_sha: str = "") -> None:
        self.call_id = call_id
        self.caller_number = caller_number or "unknown"
        # F8 per-call agent fidelity (optional — defaults keep old callers
        # byte-compatible): which agent revision/sha served this call.
        self.agent_slug = agent_slug or ""
        self.agent_revision = agent_revision
        self.agent_sha = agent_sha or ""
        self._start_time = time.time()
        self._start_dt = datetime.now(timezone.utc)

        # Folder: <caller_number>_<YYYYMMDD_HHMMSS>
        dt_str = self._start_dt.strftime("%Y%m%d_%H%M%S")
        folder_name = f"{self.caller_number}_{dt_str}"
        self._call_dir = os.path.join(RECORDINGS_ROOT, folder_name)
        os.makedirs(self._call_dir, exist_ok=True)

        self._wav_path = os.path.join(self._call_dir, "caller_audio.wav")
        self._txt_path = os.path.join(self._call_dir, "transcript.txt")
        self._meta_path = os.path.join(self._call_dir, "metadata.json")

        # WAV writer — opened immediately so we can stream frames
        self._wav_file = wave.open(self._wav_path, "wb")
        self._wav_file.setnchannels(CHANNELS)
        self._wav_file.setsampwidth(SAMPLE_WIDTH)
        self._wav_file.setframerate(SAMPLE_RATE)

        # Transcript file
        self._txt_file = open(self._txt_path, "a", encoding="utf-8")

        # Non-blocking audio queue → background writer
        self._audio_queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue(
            maxsize=_AUDIO_QUEUE_MAXSIZE
        )
        self._writer_task: Optional[asyncio.Task] = None

        # Transcript counter for elapsed-time calculation
        self._transcript_count = 0

        # Metadata fields populated at close()
        self._language_detected: str = "unknown"
        self._stt_provider: str = "sarvam"
        self._fallback_used: bool = False

        logger.info("[AUDIT] Session created call_id=%s dir=%s", call_id, self._call_dir)

    async def start(self) -> None:
        self._writer_task = asyncio.create_task(
            self._audio_writer_loop(), name=f"audit_writer_{self.call_id}"
        )

    def push_audio(self, pcm_bytes: bytes) -> None:
        """Non-blocking: queue PCM frame for background writing."""
        if not pcm_bytes:
            return
        try:
            self._audio_queue.put_nowait(pcm_bytes)
        except asyncio.QueueFull:
            pass  # Drop oldest-equivalent: just skip this frame

    def add_event(self, speaker: str, text: str) -> None:
        """Record a transcript entry (caller or agent) with elapsed timestamp."""
        if not text:
            return
        elapsed = time.time() - self._start_time
        minutes = int(elapsed // 60)
        seconds = elapsed % 60
        timestamp_str = f"[{minutes:02d}:{seconds:06.3f}]"
        entry = f"{timestamp_str}\n{speaker}: {text}\n\n"
        try:
            self._txt_file.write(entry)
            self._txt_file.flush()
        except Exception as exc:
            logger.warning("[AUDIT] Transcript write failed: %r", exc)
        self._transcript_count += 1

    async def close(
        self,
        language_detected: str = "unknown",
        stt_provider: str = "sarvam",
        fallback_used: bool = False,
    ) -> None:
        """Flush all buffers, close files, write metadata."""
        self._language_detected = language_detected
        self._stt_provider = stt_provider
        self._fallback_used = fallback_used

        # Signal writer to stop
        try:
            await self._audio_queue.put(None)
        except Exception:
            pass

        if self._writer_task is not None:
            try:
                await asyncio.wait_for(self._writer_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                self._writer_task.cancel()

        # Close WAV
        try:
            self._wav_file.close()
        except Exception as exc:
            logger.warning("[AUDIT] WAV close error: %r", exc)

        # Close transcript
        try:
            self._txt_file.flush()
            self._txt_file.close()
        except Exception as exc:
            logger.warning("[AUDIT] Transcript close error: %r", exc)

        # Write metadata
        end_time = time.time()
        duration = end_time - self._start_time
        metadata = {
            "call_id": self.call_id,
            "start_time": self._start_dt.isoformat(),
            "end_time": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": round(duration, 3),
            "language_detected": self._language_detected,
            "stt_provider": self._stt_provider,
            "fallback_used": self._fallback_used,
            "transcript_count": self._transcript_count,
            # F8 per-call agent fidelity — which agent bytes served the call.
            "agent_slug": self.agent_slug,
            "agent_revision": self.agent_revision,
            "agent_sha": self.agent_sha,
        }
        try:
            with open(self._meta_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2)
        except Exception as exc:
            logger.warning("[AUDIT] Metadata write failed: %r", exc)

        logger.info(
            "[AUDIT] Session closed call_id=%s duration=%.1fs transcripts=%d",
            self.call_id,
            duration,
            self._transcript_count,
        )

    async def _audio_writer_loop(self) -> None:
        """Background coroutine: drains audio queue → WAV file."""
        try:
            while True:
                chunk = await self._audio_queue.get()
                if chunk is None:
                    break
                try:
                    self._wav_file.writeframes(chunk)
                except Exception as exc:
                    logger.warning("[AUDIT] WAV write error: %r", exc)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[AUDIT] Audio writer crashed: %r", exc)


class CallAuditManager:
    """Global registry of active audit sessions."""

    def __init__(self) -> None:
        self._sessions: Dict[str, CallAuditSession] = {}

    def enabled(self) -> bool:
        return ENABLE_CALL_AUDIT

    async def create(self, call_id: str, caller_number: str = "",
                     agent_slug: str = "", agent_revision: Optional[int] = None,
                     agent_sha: str = "") -> Optional[CallAuditSession]:
        if not ENABLE_CALL_AUDIT:
            return None
        if call_id in self._sessions:
            return self._sessions[call_id]
        session = CallAuditSession(call_id, caller_number=caller_number,
                                   agent_slug=agent_slug,
                                   agent_revision=agent_revision,
                                   agent_sha=agent_sha)
        self._sessions[call_id] = session
        await session.start()
        return session

    def push_audio(self, call_id: str, pcm_bytes: bytes) -> None:
        if not ENABLE_CALL_AUDIT:
            return
        session = self._sessions.get(call_id)
        if session is not None:
            session.push_audio(pcm_bytes)

    def add_event(self, call_id: str, speaker: str, text: str) -> None:
        if not ENABLE_CALL_AUDIT:
            return
        session = self._sessions.get(call_id)
        if session is not None:
            session.add_event(speaker, text)

    async def close(
        self,
        call_id: str,
        language_detected: str = "unknown",
        stt_provider: str = "sarvam",
        fallback_used: bool = False,
    ) -> None:
        if not ENABLE_CALL_AUDIT:
            return
        session = self._sessions.pop(call_id, None)
        if session is not None:
            await session.close(language_detected, stt_provider, fallback_used)


# Global singleton
audit_manager = CallAuditManager()
