"""
SarvamCreditTracker — estimates Sarvam API credit consumption from runtime signals.

Since Sarvam exposes no quota endpoint, all tracking is derived from:
  - STT: seconds of audio sent (len(audio_bytes) / sample_rate / 2)
  - TTS: characters of text sent (len(text))

Events are persisted to JSONL files under SARVAM_CREDIT_LOG_DIR so
usage survives restarts and can be queried offline by credit_report.py.

Each JSONL line is one of:
  {"type": "call_start", "call_id": "...", "ts": "ISO"}
  {"type": "stt_event",  "call_id": "...", "ts": "ISO", "provider": "sarvam"|"whisper", "audio_seconds": float}
  {"type": "tts_event",  "call_id": "...", "ts": "ISO", "chars": int, "completed": bool}
  {"type": "call_end",   "call_id": "...", "ts": "ISO", ...aggregated fields...}

Thread/task safety: asyncio.Lock protects in-memory accumulators.
File writes are append-only and wrapped in try/except — I/O errors
never surface to callers.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_LOG_DIR = "/home/angelo/lescall/credit_log"
_CREDIT_LOG_DIR = Path(os.environ.get("SARVAM_CREDIT_LOG_DIR", _DEFAULT_LOG_DIR))

# Sarvam pricing (Bulbul v3 TTS, saaras:v3 STT):
#   STT  = ₹30 / hour  → ₹0.5 / min  (rounded up per-request to nearest second)
#   TTS  = ₹30 / 10K chars → ₹3.0 / 1K chars (rounded up per-request to nearest char)
# These match SARVAM_STT_PRICE_PER_MIN and SARVAM_TTS_PRICE_PER_1K_CHARS in .env.
_STT_PRICE_PER_MIN = float(os.environ.get("SARVAM_STT_PRICE_PER_MIN", "0.5"))
_TTS_PRICE_PER_1K_CHARS = float(os.environ.get("SARVAM_TTS_PRICE_PER_1K_CHARS", "3.0"))
_PRICING_CONFIGURED = True  # real defaults now known; always show estimates


def _ceil_seconds(audio_seconds: float) -> float:
    """Sarvam bills STT rounded up to the nearest second per request."""
    return float(math.ceil(audio_seconds))


def _calc_stt_cost(billed_seconds: float) -> float:
    return round((billed_seconds / 60.0) * _STT_PRICE_PER_MIN, 6)


def _calc_tts_cost(chars: int) -> float:
    return round((chars / 1000.0) * _TTS_PRICE_PER_1K_CHARS, 6)


@dataclass
class _CallAccum:
    call_id: str
    start_time: str
    end_time: str | None = None
    stt_sarvam_seconds: float = 0.0       # raw actual seconds
    stt_sarvam_billed_seconds: float = 0.0  # ceil-per-request (what Sarvam charges)
    stt_sarvam_calls: int = 0
    stt_whisper_seconds: float = 0.0
    stt_whisper_calls: int = 0
    tts_chars: int = 0
    tts_calls: int = 0
    tts_cancelled_calls: int = 0


class SarvamCreditTracker:
    """
    Singleton credit tracker for Sarvam STT + TTS usage.

    Obtain the instance via SarvamCreditTracker.get().
    All public methods are safe to call with call_id=None (no-ops at
    call level, still accumulates session totals).
    """

    _instance: "SarvamCreditTracker | None" = None

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._active_calls: dict[str, _CallAccum] = {}
        # Session-level accumulators (reset only on process restart)
        self._s_stt_sarvam_sec: float = 0.0
        self._s_stt_sarvam_billed_sec: float = 0.0
        self._s_stt_sarvam_calls: int = 0
        self._s_stt_whisper_sec: float = 0.0
        self._s_stt_whisper_calls: int = 0
        self._s_tts_chars: int = 0
        self._s_tts_calls: int = 0
        self._s_tts_cancelled: int = 0
        self._s_call_count: int = 0

    @classmethod
    def get(cls) -> "SarvamCreditTracker":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------
    # Call lifecycle
    # ------------------------------------------------------------------

    def start_call(self, call_id: str) -> None:
        """Register a new call. Call before the pipeline starts."""
        if not call_id:
            return
        ts = _now_iso()
        accum = _CallAccum(call_id=call_id, start_time=ts)
        self._active_calls[call_id] = accum
        self._s_call_count += 1
        _append_event({"type": "call_start", "call_id": call_id, "ts": ts})
        logger.info("[CREDITS] Call started: %s (session call #%d)", call_id, self._s_call_count)

    def end_call(self, call_id: str) -> None:
        """Finalise a call record. Call when the pipeline stops."""
        if not call_id:
            return
        ts = _now_iso()
        accum = self._active_calls.pop(call_id, None)
        if accum is None:
            return
        accum.end_time = ts
        event = {
            "type": "call_end",
            "call_id": call_id,
            "ts": ts,
            "start_time": accum.start_time,
            "stt_sarvam_seconds": round(accum.stt_sarvam_seconds, 3),
            "stt_sarvam_billed_seconds": round(accum.stt_sarvam_billed_seconds, 3),
            "stt_sarvam_calls": accum.stt_sarvam_calls,
            "stt_whisper_seconds": round(accum.stt_whisper_seconds, 3),
            "stt_whisper_calls": accum.stt_whisper_calls,
            "tts_chars": accum.tts_chars,
            "tts_calls": accum.tts_calls,
            "tts_cancelled_calls": accum.tts_cancelled_calls,
            "est_stt_cost": _calc_stt_cost(accum.stt_sarvam_billed_seconds),
            "est_tts_cost": _calc_tts_cost(accum.tts_chars),
        }
        _append_event(event)
        logger.info(
            "[CREDITS] Call ended: %s | STT: %.1fs billed=%ds (%d calls Sarvam / %d Whisper) | "
            "TTS: %d chars (%d calls, %d barge-in) | est ₹%.4f",
            call_id,
            accum.stt_sarvam_seconds, accum.stt_sarvam_billed_seconds,
            accum.stt_sarvam_calls, accum.stt_whisper_calls,
            accum.tts_chars, accum.tts_calls, accum.tts_cancelled_calls,
            _calc_stt_cost(accum.stt_sarvam_billed_seconds) + _calc_tts_cost(accum.tts_chars),
        )

    # ------------------------------------------------------------------
    # Per-event recording (async — acquires lock for in-memory state)
    # ------------------------------------------------------------------

    async def record_stt(
        self,
        call_id: str | None,
        audio_seconds: float,
        provider: str = "sarvam",
    ) -> None:
        """Record one STT API call that succeeded (HTTP 200).

        audio_seconds is the raw duration. For Sarvam, billing rounds up
        to the nearest second per request — billed_seconds = ceil(audio_seconds).
        """
        billed_seconds = _ceil_seconds(audio_seconds) if provider == "sarvam" else audio_seconds
        ts = _now_iso()
        async with self._lock:
            if provider == "sarvam":
                self._s_stt_sarvam_sec += audio_seconds
                self._s_stt_sarvam_billed_sec += billed_seconds
                self._s_stt_sarvam_calls += 1
                if call_id and call_id in self._active_calls:
                    self._active_calls[call_id].stt_sarvam_seconds += audio_seconds
                    self._active_calls[call_id].stt_sarvam_billed_seconds += billed_seconds
                    self._active_calls[call_id].stt_sarvam_calls += 1
            else:
                self._s_stt_whisper_sec += audio_seconds
                self._s_stt_whisper_calls += 1
                if call_id and call_id in self._active_calls:
                    self._active_calls[call_id].stt_whisper_seconds += audio_seconds
                    self._active_calls[call_id].stt_whisper_calls += 1
        _append_event({
            "type": "stt_event",
            "call_id": call_id,
            "ts": ts,
            "provider": provider,
            "audio_seconds": round(audio_seconds, 3),
            "billed_seconds": billed_seconds,
        })

    async def record_tts(
        self,
        call_id: str | None,
        chars: int,
        completed: bool = True,
    ) -> None:
        """Record one TTS API call. completed=False means barge-in cancelled it."""
        ts = _now_iso()
        async with self._lock:
            self._s_tts_chars += chars
            self._s_tts_calls += 1
            if not completed:
                self._s_tts_cancelled += 1
            if call_id and call_id in self._active_calls:
                self._active_calls[call_id].tts_chars += chars
                self._active_calls[call_id].tts_calls += 1
                if not completed:
                    self._active_calls[call_id].tts_cancelled_calls += 1
        _append_event({
            "type": "tts_event",
            "call_id": call_id,
            "ts": ts,
            "chars": chars,
            "completed": completed,
        })

    # ------------------------------------------------------------------
    # Summary accessors
    # ------------------------------------------------------------------

    def get_call_summary(self, call_id: str) -> dict | None:
        """Return in-progress accumulator for an active call, or None."""
        accum = self._active_calls.get(call_id)
        if accum is None:
            return None
        return {
            "call_id": call_id,
            "start_time": accum.start_time,
            "stt_sarvam_seconds": round(accum.stt_sarvam_seconds, 3),
            "stt_sarvam_billed_seconds": round(accum.stt_sarvam_billed_seconds, 3),
            "stt_sarvam_calls": accum.stt_sarvam_calls,
            "stt_whisper_seconds": round(accum.stt_whisper_seconds, 3),
            "stt_whisper_calls": accum.stt_whisper_calls,
            "tts_chars": accum.tts_chars,
            "tts_calls": accum.tts_calls,
            "tts_cancelled_calls": accum.tts_cancelled_calls,
            "est_stt_cost": _calc_stt_cost(accum.stt_sarvam_billed_seconds),
            "est_tts_cost": _calc_tts_cost(accum.tts_chars),
        }

    def get_session_summary(self) -> dict:
        """Return totals since process start."""
        return {
            "call_count": self._s_call_count,
            "stt_sarvam_seconds": round(self._s_stt_sarvam_sec, 3),
            "stt_sarvam_billed_seconds": round(self._s_stt_sarvam_billed_sec, 3),
            "stt_sarvam_calls": self._s_stt_sarvam_calls,
            "stt_whisper_seconds": round(self._s_stt_whisper_sec, 3),
            "stt_whisper_calls": self._s_stt_whisper_calls,
            "tts_chars": self._s_tts_chars,
            "tts_calls": self._s_tts_calls,
            "tts_cancelled_calls": self._s_tts_cancelled,
            "est_stt_cost": _calc_stt_cost(self._s_stt_sarvam_billed_sec),
            "est_tts_cost": _calc_tts_cost(self._s_tts_chars),
        }


# ------------------------------------------------------------------
# File I/O helpers — module-level so they don't touch instance state
# ------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.datetime.utcnow().isoformat(timespec="milliseconds") + "Z"


def _log_path(date: datetime.date | None = None) -> Path:
    d = date or datetime.date.today()
    return _CREDIT_LOG_DIR / f"{d.isoformat()}.jsonl"


def _append_event(event: dict) -> None:
    """Append one JSON line to today's log. Never raises."""
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.debug("[CREDITS] Event write failed: %s", exc)
