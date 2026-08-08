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
import os
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_LOG_DIR = "/home/angelo/lescall/credit_log"
_CREDIT_LOG_DIR = Path(os.environ.get("SARVAM_CREDIT_LOG_DIR", _DEFAULT_LOG_DIR))

# ---------------------------------------------------------------------------
# Pricing lives in shared/sarvam_pricing.py — the single rate card the tracker,
# the offline report and the dashboard all read, so no two of them can disagree
# about what a call cost. Imported by path rather than assumed to be on
# sys.path: this module is loaded both from the repo root (the telephony
# bridge) and from calling-agent/backend (CLI run.py), and only the former has
# `shared` importable by default.
_REPO_ROOT = str(Path(__file__).resolve().parents[4])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
from shared.sarvam_pricing import (  # noqa: E402
    stt_billed_seconds as _ceil_seconds,
    stt_cost as _calc_stt_cost,
    tts_cost as _calc_tts_cost,
    whisper_cost as _calc_whisper_cost,
)
from shared.elevenlabs_pricing import (  # noqa: E402
    stt_cost as _calc_el_stt_cost,
    tts_cost as _calc_el_tts_cost,
)

_PRICING_CONFIGURED = True  # real rates are known; always show estimates


def _usage_fields(a: "_CallAccum") -> dict:
    """The usage + cost block shared by call_end, per-call and session summaries.

    est_tts_cost is computed from tts_billed_chars (what Sarvam received), not
    tts_chars (what we wanted spoken) — see record_tts. est_total_cost is the
    real all-in figure and includes the Whisper fallback, which is a different
    vendor and was previously priced at zero.
    """
    stt = _calc_stt_cost(a.stt_sarvam_billed_seconds)
    # tts_billed_chars stays the ALL-provider total (historical field name,
    # dashboards read it) — the Sarvam share is total minus the EL share.
    tts = _calc_tts_cost(max(0, a.tts_billed_chars - a.tts_el_billed_chars))
    whisper = _calc_whisper_cost(a.stt_whisper_seconds)
    el_stt = _calc_el_stt_cost(a.stt_el_seconds)
    el_tts = _calc_el_tts_cost(a.tts_el_billed_chars)
    return {
        "stt_sarvam_seconds": round(a.stt_sarvam_seconds, 3),
        "stt_sarvam_billed_seconds": round(a.stt_sarvam_billed_seconds, 3),
        "stt_sarvam_calls": a.stt_sarvam_calls,
        "stt_whisper_seconds": round(a.stt_whisper_seconds, 3),
        "stt_whisper_calls": a.stt_whisper_calls,
        "stt_el_seconds": round(a.stt_el_seconds, 3),
        "stt_el_calls": a.stt_el_calls,
        "tts_chars": a.tts_chars,
        "tts_billed_chars": a.tts_billed_chars,
        "tts_calls": a.tts_calls,
        "tts_cancelled_calls": a.tts_cancelled_calls,
        "tts_el_billed_chars": a.tts_el_billed_chars,
        "tts_el_calls": a.tts_el_calls,
        "est_stt_cost": stt,
        "est_tts_cost": tts,
        "est_whisper_cost": whisper,
        "est_el_stt_cost": el_stt,
        "est_el_tts_cost": el_tts,
        "est_total_cost": round(stt + tts + whisper + el_stt + el_tts, 6),
    }


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
    tts_chars: int = 0            # characters the pipeline ASKED to speak
    tts_billed_chars: int = 0     # characters actually SUBMITTED (all providers)
    tts_calls: int = 0
    tts_cancelled_calls: int = 0
    # ElevenLabs share of the above (provider switch 2026-08-07). Sarvam's
    # share is the total minus these — historical field names stay intact.
    stt_el_seconds: float = 0.0
    stt_el_calls: int = 0
    tts_el_chars: int = 0
    tts_el_billed_chars: int = 0
    tts_el_calls: int = 0


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
        self._s_tts_billed_chars: int = 0
        self._s_tts_calls: int = 0
        self._s_tts_cancelled: int = 0
        self._s_call_count: int = 0
        self._s_stt_el_sec: float = 0.0
        self._s_stt_el_calls: int = 0
        self._s_tts_el_chars: int = 0
        self._s_tts_el_billed: int = 0
        self._s_tts_el_calls: int = 0

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
            **_usage_fields(accum),
        }
        _append_event(event)
        logger.info(
            "[CREDITS] Call ended: %s | STT: %.1fs billed=%ds (%d calls Sarvam / %d Whisper) | "
            "TTS: %d chars billed=%d (%d calls, %d barge-in) | est ₹%.4f",
            call_id,
            accum.stt_sarvam_seconds, accum.stt_sarvam_billed_seconds,
            accum.stt_sarvam_calls, accum.stt_whisper_calls,
            accum.tts_chars, accum.tts_billed_chars,
            accum.tts_calls, accum.tts_cancelled_calls,
            event["est_total_cost"],
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
            elif provider == "elevenlabs":
                self._s_stt_el_sec += audio_seconds
                self._s_stt_el_calls += 1
                if call_id and call_id in self._active_calls:
                    self._active_calls[call_id].stt_el_seconds += audio_seconds
                    self._active_calls[call_id].stt_el_calls += 1
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
        billed_chars: int | None = None,
        provider: str = "sarvam",
    ) -> None:
        """Record one TTS utterance. completed=False means barge-in cancelled it.

        `chars` is what the pipeline asked to speak; `billed_chars` is what was
        actually POSTed to Sarvam. They differ on barge-in: speak() synthesizes
        an utterance as a sequence of clause-sized requests and breaks out of
        that loop the moment the caller interrupts, so the tail clauses are
        never sent — and Sarvam bills text it received, not text we intended.
        Charging `chars` there invented cost that was never incurred.

        billed_chars=None means the caller can't distinguish (older callers,
        non-chunked paths) — fall back to `chars`, which is exact whenever the
        utterance completed.
        """
        billed = chars if billed_chars is None else max(0, int(billed_chars))
        ts = _now_iso()
        async with self._lock:
            self._s_tts_chars += chars
            self._s_tts_billed_chars += billed
            self._s_tts_calls += 1
            if not completed:
                self._s_tts_cancelled += 1
            if provider == "elevenlabs":
                self._s_tts_el_chars += chars
                self._s_tts_el_billed += billed
                self._s_tts_el_calls += 1
            if call_id and call_id in self._active_calls:
                self._active_calls[call_id].tts_chars += chars
                self._active_calls[call_id].tts_billed_chars += billed
                self._active_calls[call_id].tts_calls += 1
                if not completed:
                    self._active_calls[call_id].tts_cancelled_calls += 1
                if provider == "elevenlabs":
                    self._active_calls[call_id].tts_el_chars += chars
                    self._active_calls[call_id].tts_el_billed_chars += billed
                    self._active_calls[call_id].tts_el_calls += 1
        _append_event({
            "type": "tts_event",
            "call_id": call_id,
            "ts": ts,
            "chars": chars,
            "billed_chars": billed,
            "completed": completed,
            "provider": provider,
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
            **_usage_fields(accum),
        }

    def get_session_summary(self) -> dict:
        """Return totals since process start."""
        return {
            "call_count": self._s_call_count,
            **_usage_fields(_CallAccum(
                call_id="", start_time="",
                stt_sarvam_seconds=self._s_stt_sarvam_sec,
                stt_sarvam_billed_seconds=self._s_stt_sarvam_billed_sec,
                stt_sarvam_calls=self._s_stt_sarvam_calls,
                stt_whisper_seconds=self._s_stt_whisper_sec,
                stt_whisper_calls=self._s_stt_whisper_calls,
                tts_chars=self._s_tts_chars,
                tts_billed_chars=self._s_tts_billed_chars,
                tts_calls=self._s_tts_calls,
                tts_cancelled_calls=self._s_tts_cancelled,
                stt_el_seconds=self._s_stt_el_sec,
                stt_el_calls=self._s_stt_el_calls,
                tts_el_chars=self._s_tts_el_chars,
                tts_el_billed_chars=self._s_tts_el_billed,
                tts_el_calls=self._s_tts_el_calls,
            )),
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
