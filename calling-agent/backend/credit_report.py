#!/usr/bin/env python3
"""
Sarvam Credit Usage Report — reads from credit_log/YYYY-MM-DD.jsonl files.

Usage:
  python credit_report.py                  # today's summary (default)
  python credit_report.py --today          # today's summary
  python credit_report.py --call <id>      # full breakdown for one call_id
  python credit_report.py --history 7      # last N days as a table
  python credit_report.py --cumulative     # all-time totals across every log file
  python credit_report.py --watch          # live refresh (5s interval)

Reads SARVAM_CREDIT_LOG_DIR env var (default: /home/angelo/lescall/credit_log).
Reads SARVAM_STT_PRICE_PER_MIN and SARVAM_TTS_PRICE_PER_1K_CHARS for cost estimates.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

_DEFAULT_LOG_DIR = "/home/angelo/lescall/credit_log"
LOG_DIR = Path(os.environ.get("SARVAM_CREDIT_LOG_DIR", _DEFAULT_LOG_DIR))

# Pricing comes from the one rate card (shared/sarvam_pricing.py) so this
# report, the live tracker and the dashboard cannot drift apart.
_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
from shared import sarvam_pricing as _pricing  # noqa: E402

STT_PRICE_PER_MIN = _pricing.STT_PRICE_PER_MIN
TTS_PRICE_PER_1K = _pricing.TTS_PRICE_PER_1K_CHARS


def _cost_str(billed_stt_sec: float, tts_chars: int) -> str:
    cost = _pricing.stt_cost(billed_stt_sec) + _pricing.tts_cost(tts_chars)
    return f"est. ₹{cost:.4f}"


def _log_path(date: datetime.date) -> Path:
    return LOG_DIR / f"{date.isoformat()}.jsonl"


def _read_events(date: datetime.date) -> list[dict]:
    path = _log_path(date)
    if not path.exists():
        return []
    events = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return events


def _read_all_events() -> list[dict]:
    all_events: list[dict] = []
    if not LOG_DIR.exists():
        return all_events
    for p in sorted(LOG_DIR.glob("*.jsonl")):
        with p.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        all_events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    return all_events


# -----------------------------------------------------------------------
# Aggregation
# -----------------------------------------------------------------------

def _agg_from_events(events: list[dict]) -> dict:
    """Aggregate a list of events into totals."""
    import math
    calls: set[str] = set()
    stt_sarvam_sec = 0.0
    stt_sarvam_billed_sec = 0.0
    stt_sarvam_calls = 0
    stt_whisper_sec = 0.0
    stt_whisper_calls = 0
    tts_chars = 0
    tts_billed_chars = 0
    tts_calls = 0
    tts_cancelled = 0

    for ev in events:
        t = ev.get("type")
        cid = ev.get("call_id")
        if cid:
            calls.add(cid)

        if t == "stt_event":
            sec = float(ev.get("audio_seconds", 0))
            # Use pre-computed billed_seconds if present; fall back to ceiling
            billed = float(ev.get("billed_seconds", math.ceil(sec)))
            if ev.get("provider") == "sarvam":
                stt_sarvam_sec += sec
                stt_sarvam_billed_sec += billed
                stt_sarvam_calls += 1
            else:
                stt_whisper_sec += sec
                stt_whisper_calls += 1
        elif t == "tts_event":
            chars = int(ev.get("chars", 0))
            tts_chars += chars
            # billed_chars is what was actually POSTed (barge-in stops the
            # part loop early). Absent on pre-2026-08-06 events — those fall
            # back to the intended count, which is an upper bound.
            tts_billed_chars += int(ev.get("billed_chars", chars))
            tts_calls += 1
            if not ev.get("completed", True):
                tts_cancelled += 1

    return {
        "call_count": len(calls),
        "stt_sarvam_sec": stt_sarvam_sec,
        "stt_sarvam_billed_sec": stt_sarvam_billed_sec,
        "stt_sarvam_calls": stt_sarvam_calls,
        "stt_whisper_sec": stt_whisper_sec,
        "stt_whisper_calls": stt_whisper_calls,
        "tts_chars": tts_chars,
        "tts_billed_chars": tts_billed_chars,
        "tts_calls": tts_calls,
        "tts_cancelled": tts_cancelled,
    }


def _call_detail(events: list[dict], call_id: str) -> dict | None:
    """Extract per-call aggregated detail."""
    relevant = [ev for ev in events if ev.get("call_id") == call_id]
    if not relevant:
        return None
    agg = _agg_from_events(relevant)
    start = next((ev.get("ts") for ev in relevant if ev.get("type") == "call_start"), "—")
    end = next((ev.get("ts") for ev in relevant if ev.get("type") == "call_end"), "—")
    agg.update({"call_id": call_id, "start_time": start, "end_time": end})
    return agg


# -----------------------------------------------------------------------
# Rendering
# -----------------------------------------------------------------------

_SEP = "─" * 62


def _print_summary(title: str, agg: dict) -> None:
    print(f"\n{_SEP}")
    print(f"  {title}")
    print(_SEP)
    print(f"  Calls              : {agg['call_count']}")
    raw = agg["stt_sarvam_sec"]
    billed = agg["stt_sarvam_billed_sec"]
    overhead = billed - raw
    print(f"  STT (Sarvam)       : {raw:.1f}s actual  →  {billed:.0f}s billed  (+{overhead:.1f}s ceil overhead)")
    print(f"                       {agg['stt_sarvam_calls']} API requests @ ₹30/hr")
    if agg["stt_sarvam_calls"] > 0:
        avg = raw / agg["stt_sarvam_calls"]
        print(f"                       avg {avg:.2f}s/request")
    if agg["stt_whisper_calls"]:
        print(f"  STT (Whisper fback): {agg['stt_whisper_sec']:.1f}s  ({agg['stt_whisper_calls']} API calls)")
    print(f"  TTS (Bulbul v3)    : {agg['tts_chars']:,} chars  ({agg['tts_calls']} requests @ ₹30/10K chars)")
    if agg["tts_calls"] > 0:
        pct = agg["tts_cancelled"] / agg["tts_calls"] * 100
        print(f"  TTS barge-in cancel: {agg['tts_cancelled']} ({pct:.0f}% of requests — unsent tails NOT billed)")
    stt_cost = _pricing.stt_cost(billed)
    tts_cost = _pricing.tts_cost(agg["tts_billed_chars"])
    whisper_cost = _pricing.whisper_cost(agg["stt_whisper_sec"])
    print(f"  ─────────────────────────────────────────────────────────")
    print(f"  STT cost           : ₹{stt_cost:.4f}")
    print(f"  TTS cost           : ₹{tts_cost:.4f}")
    if whisper_cost:
        print(f"  Whisper cost       : ₹{whisper_cost:.4f}")
    print(f"  Total estimate     : ₹{stt_cost + tts_cost + whisper_cost:.4f}")
    print(_SEP)


def _print_call_detail(call_id: str, agg: dict) -> None:
    print(f"\n{_SEP}")
    print(f"  Call: {call_id}")
    print(_SEP)
    print(f"  Started            : {agg.get('start_time', '—')}")
    print(f"  Ended              : {agg.get('end_time', '—')}")
    raw = agg["stt_sarvam_sec"]
    billed = agg["stt_sarvam_billed_sec"]
    print(f"  STT (Sarvam)       : {raw:.2f}s actual → {billed:.0f}s billed  ({agg['stt_sarvam_calls']} requests)")
    if agg["stt_whisper_calls"]:
        print(f"  STT (Whisper fback): {agg['stt_whisper_sec']:.2f}s  ({agg['stt_whisper_calls']} requests)")
    print(f"  TTS chars          : {agg['tts_chars']:,}  ({agg['tts_calls']} requests)")
    print(f"  TTS barge-in cancel: {agg['tts_cancelled']}")
    stt_cost = _pricing.stt_cost(billed)
    tts_cost = _pricing.tts_cost(agg["tts_billed_chars"])
    whisper_cost = _pricing.whisper_cost(agg["stt_whisper_sec"])
    print(f"  STT cost           : ₹{stt_cost:.4f}")
    print(f"  TTS cost           : ₹{tts_cost:.4f}")
    if whisper_cost:
        print(f"  Whisper cost       : ₹{whisper_cost:.4f}")
    print(f"  Total estimate     : ₹{stt_cost + tts_cost + whisper_cost:.4f}")
    print(_SEP)


def _print_history(days: int) -> None:
    today = datetime.date.today()
    rows = []
    for delta in range(days - 1, -1, -1):
        d = today - datetime.timedelta(days=delta)
        events = _read_events(d)
        agg = _agg_from_events(events)
        rows.append((d.isoformat(), agg))

    print(f"\n{_SEP}")
    print(f"  {'Date':<12} {'Calls':>5} {'STT billed':>11} {'TTS chars':>10} {'Barge-in':>8} {'₹ Cost':>9}")
    print("  " + "─" * 60)
    for date_str, agg in rows:
        cost = (_pricing.stt_cost(agg["stt_sarvam_billed_sec"])
                + _pricing.tts_cost(agg["tts_billed_chars"])
                + _pricing.whisper_cost(agg["stt_whisper_sec"]))
        print(
            f"  {date_str:<12} {agg['call_count']:>5} {agg['stt_sarvam_billed_sec']:>9.0f}s"
            f" {agg['tts_chars']:>10,} {agg['tts_cancelled']:>8} ₹{cost:>7.4f}"
        )
    print(_SEP)


# -----------------------------------------------------------------------
# CLI entry points
# -----------------------------------------------------------------------

def cmd_today() -> None:
    events = _read_events(datetime.date.today())
    agg = _agg_from_events(events)
    _print_summary(f"Today — {datetime.date.today().isoformat()}", agg)


def cmd_call(call_id: str) -> None:
    all_events = _read_all_events()
    agg = _call_detail(all_events, call_id)
    if agg is None:
        print(f"\nNo events found for call_id: {call_id}")
        return
    _print_call_detail(call_id, agg)
    # Print individual STT/TTS events for fine-grained breakdown
    relevant = [ev for ev in all_events if ev.get("call_id") == call_id]
    print(f"\n  {'#':<3} {'Type':<11} {'TS':<28} {'Detail'}")
    print("  " + "─" * 58)
    for i, ev in enumerate(relevant, 1):
        t = ev.get("type", "")
        ts = ev.get("ts", "")[:26]
        if t == "stt_event":
            detail = f"{ev.get('provider','?')} {ev.get('audio_seconds',0):.2f}s"
        elif t == "tts_event":
            status = "ok" if ev.get("completed", True) else "barge-in"
            detail = f"{ev.get('chars',0)} chars [{status}]"
        else:
            detail = ""
        print(f"  {i:<3} {t:<11} {ts:<28} {detail}")
    print()


def cmd_history(days: int) -> None:
    _print_history(days)


def cmd_cumulative() -> None:
    all_events = _read_all_events()
    agg = _agg_from_events(all_events)
    _print_summary("Cumulative (all time)", agg)


def cmd_watch() -> None:
    print("Watching credit_log/ — refreshing every 5s. Ctrl+C to stop.")
    try:
        while True:
            # Clear terminal
            os.system("clear" if os.name != "nt" else "cls")
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Live credit usage\n")
            cmd_today()
            time.sleep(5)
    except KeyboardInterrupt:
        print("\nStopped.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sarvam credit usage report",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument("--today", action="store_true", help="Today's summary (default)")
    grp.add_argument("--call", metavar="CALL_ID", help="Detail for one call")
    grp.add_argument("--history", metavar="N", type=int, default=None, help="Last N days table")
    grp.add_argument("--cumulative", action="store_true", help="All-time totals")
    grp.add_argument("--watch", action="store_true", help="Live refresh every 5s")
    args = parser.parse_args()

    if not LOG_DIR.exists():
        print(f"\nNo credit log directory found at: {LOG_DIR}")
        print("Logs are created automatically once the pipeline handles a call.")
        sys.exit(0)

    if args.call:
        cmd_call(args.call)
    elif args.history is not None:
        cmd_history(args.history)
    elif args.cumulative:
        cmd_cumulative()
    elif args.watch:
        cmd_watch()
    else:
        cmd_today()


if __name__ == "__main__":
    main()
