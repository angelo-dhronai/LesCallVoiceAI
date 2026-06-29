"""
call_audit.py — Call Audit System

Records call audio, transcripts, and metadata for every phone call.

Enabled by:  ENABLE_CALL_AUDIT=true
Disabled by: ENABLE_CALL_AUDIT=false  (zero overhead — early-exit everywhere)

Directory layout:
    recordings/
        <caller_number>_<YYYYMMDD_HHMMSS>/
            caller_audio.wav   (PCM16, 16kHz, mono — caller only, kept for back-compat)
            mixed_audio.wav    (PCM16, 16kHz, mono — caller + agent, time-aligned)
            transcript.txt
            metadata.json

Audio mixing
------------
Caller frames (inbound, from ws_audio_receiver) and agent frames (the TTS the
caller actually heard, fed from the call bridge's playback loop) are both
stamped with their wall-clock offset from call start. At close() the two
timelines are laid onto a common 16kHz grid and summed into mixed_audio.wav so
*both* voices are audible in playback. Previously only caller audio was
recorded, so the agent was silent on playback.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import wave
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("call_audit")

ENABLE_CALL_AUDIT = os.environ.get("ENABLE_CALL_AUDIT", "false").lower() == "true"

RECORDINGS_ROOT = os.environ.get(
    "CALL_AUDIT_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "recordings"),
)

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2  # PCM16 = 2 bytes per sample
CHANNELS = 1

# Safety cap on buffered audio per channel (~45 min). Frames whose timeline
# offset would exceed this are dropped, bounding memory for runaway calls.
_MAX_SAMPLES = 45 * 60 * SAMPLE_RATE

# Mix level balancing. Telephony callers arrive much quieter than full-scale
# agent TTS, so a naive sum buries the caller. We normalise each channel to a
# common RMS target (with a peak guard so we never clip the loudest sample) so
# both voices are clearly audible in mixed_audio.wav and caller_audio.wav.
MIX_TARGET_RMS = 3000.0   # int16 RMS target per channel before mixing
MAX_NORM_GAIN = 8.0       # cap so a near-silent channel's noise floor can't explode
SILENCE_FLOOR_RMS = 50.0  # below this a channel is treated as silent (left as-is)


class CallAuditSession:
    """Per-call recording session. All push_* calls are non-blocking.

    Audio is buffered in memory as (offset_samples, pcm_bytes) fragments and
    rendered to disk once at close(). For normal call lengths this is a few MB
    per channel and lets us time-align caller and agent onto one timeline.
    """

    def __init__(self, call_id: str, caller_number: str = "") -> None:
        self.call_id = call_id
        self.caller_number = caller_number or "unknown"
        self._start_time = time.time()
        self._start_dt = datetime.now(timezone.utc)

        # Folder: <caller_number>_<YYYYMMDD_HHMMSS>
        dt_str = self._start_dt.strftime("%Y%m%d_%H%M%S")
        folder_name = f"{self.caller_number}_{dt_str}"
        self._call_dir = os.path.join(RECORDINGS_ROOT, folder_name)
        os.makedirs(self._call_dir, exist_ok=True)

        self._wav_path = os.path.join(self._call_dir, "caller_audio.wav")
        self._mixed_path = os.path.join(self._call_dir, "mixed_audio.wav")
        self._txt_path = os.path.join(self._call_dir, "transcript.txt")
        self._meta_path = os.path.join(self._call_dir, "metadata.json")

        # Transcript file — opened immediately so turns stream as they arrive
        self._txt_file = open(self._txt_path, "a", encoding="utf-8")

        # Time-stamped audio fragments, rendered at close()
        self._caller_frags: List[Tuple[int, bytes]] = []
        self._agent_frags: List[Tuple[int, bytes]] = []

        # Transcript counter for elapsed-time calculation
        self._transcript_count = 0

        # Metadata fields populated at close()
        self._language_detected: str = "unknown"
        self._stt_provider: str = "sarvam"
        self._fallback_used: bool = False

        logger.info("[AUDIT] Session created call_id=%s dir=%s", call_id, self._call_dir)

    async def start(self) -> None:
        # No background writer needed — audio is rendered at close(). Kept async
        # for API compatibility with the manager's await session.start().
        return

    def _elapsed_samples(self) -> int:
        return int((time.time() - self._start_time) * SAMPLE_RATE)

    def push_audio(self, pcm_bytes: bytes) -> None:
        """Queue an inbound caller PCM16/16kHz frame (non-blocking)."""
        if not pcm_bytes:
            return
        off = self._elapsed_samples()
        if off + len(pcm_bytes) // 2 > _MAX_SAMPLES:
            return
        self._caller_frags.append((off, pcm_bytes))

    def push_agent_audio(
        self, pcm_bytes: bytes, src_rate: int = SAMPLE_RATE, channels: int = 1
    ) -> None:
        """Queue agent (TTS) audio the caller heard. Resampled to 16kHz mono and
        stamped with the current call-timeline offset (non-blocking)."""
        if not pcm_bytes:
            return
        try:
            samples = np.frombuffer(pcm_bytes, dtype=np.int16)
            if channels > 1:
                samples = samples.reshape(-1, channels).mean(axis=1).astype(np.int16)
            if src_rate != SAMPLE_RATE and samples.size:
                new_len = int(samples.size * SAMPLE_RATE / src_rate)
                if new_len <= 0:
                    return
                x_old = np.linspace(0.0, 1.0, samples.size, endpoint=False)
                x_new = np.linspace(0.0, 1.0, new_len, endpoint=False)
                samples = np.interp(x_new, x_old, samples.astype(np.float32)).astype(np.int16)
        except Exception as exc:
            logger.warning("[AUDIT] Agent audio decode failed: %r", exc)
            return
        off = self._elapsed_samples()
        if off + samples.size > _MAX_SAMPLES:
            return
        self._agent_frags.append((off, samples.tobytes()))

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

    @staticmethod
    def _normalize_channel(
        buf: np.ndarray,
        target_rms: float = MIX_TARGET_RMS,
        max_gain: float = MAX_NORM_GAIN,
        silence_floor: float = SILENCE_FLOOR_RMS,
    ) -> np.ndarray:
        """Scale an int32 timeline toward ``target_rms`` so quiet callers and
        loud agent TTS sit at comparable loudness. Robust to silent channels and
        never clips the loudest sample (peak guard). Returns float64."""
        if buf.size == 0:
            return buf.astype(np.float64)
        x = buf.astype(np.float64)
        rms = float(np.sqrt(np.mean(x * x)))
        if rms < silence_floor:
            # Treat as silent/idle — boosting would just amplify the noise floor.
            return x
        gain = min(target_rms / rms, max_gain)
        # Peak-guard against the 99.5th-percentile sample rather than the
        # absolute max: telephony audio has rare transient spikes (clicks, DTMF)
        # with a huge crest factor, and letting those dictate the gain would keep
        # speech far too quiet. Allowing the top ~0.5% to hard-clip is inaudible.
        peak = float(np.percentile(np.abs(x), 99.5)) * gain
        if peak > 32767.0:
            gain *= 32767.0 / peak
        return x * gain

    @staticmethod
    def _render_channel(frags: List[Tuple[int, bytes]]) -> np.ndarray:
        """Lay time-stamped fragments onto a single int32 timeline (summed on
        overlap). Returns int32 to avoid intermediate clipping."""
        if not frags:
            return np.zeros(0, dtype=np.int32)
        total = max(off + len(b) // 2 for off, b in frags)
        buf = np.zeros(total, dtype=np.int32)
        for off, b in frags:
            arr = np.frombuffer(b, dtype=np.int16).astype(np.int32)
            buf[off:off + arr.size] += arr
        return buf

    async def close(
        self,
        language_detected: str = "unknown",
        stt_provider: str = "sarvam",
        fallback_used: bool = False,
    ) -> None:
        """Render audio, close files, write metadata."""
        self._language_detected = language_detected
        self._stt_provider = stt_provider
        self._fallback_used = fallback_used

        # Render audio timelines
        try:
            left = self._render_channel(self._caller_frags)   # caller
            right = self._render_channel(self._agent_frags)    # agent
            n = max(left.size, right.size)
            if left.size < n:
                left = np.concatenate([left, np.zeros(n - left.size, dtype=np.int32)])
            if right.size < n:
                right = np.concatenate([right, np.zeros(n - right.size, dtype=np.int32)])

            # Balance both channels to a common loudness before mixing so the
            # quiet caller isn't buried under full-scale agent TTS. Silent
            # channels (e.g. agent-only or caller-only calls) pass through
            # untouched via the silence guard in _normalize_channel.
            left_n = self._normalize_channel(left)
            right_n = self._normalize_channel(right)

            caller_mono = np.clip(left_n, -32768, 32767).astype(np.int16)
            # Final mix-bus normalisation keeps simultaneous speech from
            # clipping when both balanced channels sum.
            mixed = self._normalize_channel(left_n + right_n)
            mixed_mono = np.clip(mixed, -32768, 32767).astype(np.int16)

            self._write_wav(self._wav_path, caller_mono)
            self._write_wav(self._mixed_path, mixed_mono)
        except Exception as exc:
            logger.error("[AUDIT] Audio render failed: %r", exc)

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
            "has_agent_audio": bool(self._agent_frags),
        }
        try:
            with open(self._meta_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2)
        except Exception as exc:
            logger.warning("[AUDIT] Metadata write failed: %r", exc)

        logger.info(
            "[AUDIT] Session closed call_id=%s duration=%.1fs transcripts=%d agent_audio=%s",
            self.call_id,
            duration,
            self._transcript_count,
            bool(self._agent_frags),
        )

    @staticmethod
    def _write_wav(path: str, mono_int16: np.ndarray) -> None:
        with wave.open(path, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(SAMPLE_WIDTH)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(mono_int16.tobytes())


class CallAuditManager:
    """Global registry of active audit sessions."""

    def __init__(self) -> None:
        self._sessions: Dict[str, CallAuditSession] = {}

    def enabled(self) -> bool:
        return ENABLE_CALL_AUDIT

    async def create(self, call_id: str, caller_number: str = "") -> Optional[CallAuditSession]:
        if not ENABLE_CALL_AUDIT:
            return None
        if call_id in self._sessions:
            return self._sessions[call_id]
        session = CallAuditSession(call_id, caller_number=caller_number)
        self._sessions[call_id] = session
        await session.start()
        return session

    def push_audio(self, call_id: str, pcm_bytes: bytes) -> None:
        if not ENABLE_CALL_AUDIT:
            return
        session = self._sessions.get(call_id)
        if session is not None:
            session.push_audio(pcm_bytes)

    def push_agent_audio(
        self, call_id: str, pcm_bytes: bytes, src_rate: int = SAMPLE_RATE, channels: int = 1
    ) -> None:
        if not ENABLE_CALL_AUDIT:
            return
        session = self._sessions.get(call_id)
        if session is not None:
            session.push_agent_audio(pcm_bytes, src_rate=src_rate, channels=channels)

    def add_event(self, call_id: str, speaker: str, text: str) -> None:
        if not ENABLE_CALL_AUDIT:
            return
        session = self._sessions.get(call_id)
        if session is not None:
            session.add_event(speaker, text)

    def transcript_path(self, call_id: str) -> Optional[str]:
        """Path to the (already-flushed) transcript.txt for a live session, or
        None. Call before close() — close() pops the session."""
        session = self._sessions.get(call_id)
        return session._txt_path if session is not None else None

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
