#!/usr/bin/env python3
"""
LesCall Dashboard Server

Standalone operational dashboard for the voice AI calling platform.
- Read-only observer of logs, credit_log, and recordings
- Runtime controller: start / stop / restart the calling agent via API
- SSE streams for live logs and command output

Port: 8080  (nginx proxies port 80 → 8080)
Start: bash /home/angelo/lescall/dashboard/start.sh --bg
"""
from __future__ import annotations

import asyncio
import csv
import datetime
import io
import json
import logging
import math
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

LESCALL_ROOT  = Path("/home/angelo/lescall")
CREDIT_LOG_DIR = LESCALL_ROOT / "credit_log"
RECORDINGS_DIR = LESCALL_ROOT / "recordings"
RUNTIME_DIR   = LESCALL_ROOT / "phase14_runtime"
LOGS_LATEST   = RUNTIME_DIR / "logs" / "latest"
STATIC_DIR    = Path(__file__).parent / "static"
OUTBOUND_FILE = Path(__file__).parent / "outbound_contacts.json"
LIVE_STATE_FILE = RUNTIME_DIR / "live_calls_state.json"
SARVAM_CREDITS_FILE = Path(__file__).parent / "sarvam_credits.json"
API_KEY_POOL_FILE = Path(__file__).parent / "api_key_pool.json"
API_KEY_STATE_FILE = Path(__file__).parent / "api_key_state.json"
CALLING_AGENT_ENV_FILE = LESCALL_ROOT / "calling-agent" / ".env"
LOW_CREDIT_THRESHOLD = 15

CALL_LINK_FILE   = Path(__file__).parent / "call_link.json"
FOLLOWUPS_FILE   = Path(__file__).parent / "follow_ups.json"
MANUAL_FOLLOWUPS_FILE = Path(__file__).parent / "manual_followups.json"
MINUTES_QUOTA_FILE = Path(__file__).parent / "minutes_quota.json"
ANALYSIS_STORE_FILE = Path(__file__).parent / "call_analysis.json"
FOLLOWUP_AUTOMATION_FILE = Path(__file__).parent / "follow_up_automation.json"
AUTOMATION_POLL_SECONDS = 30
CONVERSATION_PY  = LESCALL_ROOT / "calling-agent" / "backend" / "app" / "voice" / "conversation.py"

# A dial is considered matched to a subsequent pipeline call if the pipeline
# call started within this many seconds of the dial being placed.
OUTBOUND_MATCH_WINDOW_SECONDS = 90

START_SCRIPT  = RUNTIME_DIR / "start_all.sh"
STOP_SCRIPT   = RUNTIME_DIR / "stop_all.sh"

# A call with no call_end older than this is considered stale, not "active"
ACTIVE_CALL_TIMEOUT_MINUTES = 20

# Outbound dialing config — override via env vars if needed
OUTBOUND_CALLER_ID  = os.environ.get("OUTBOUND_CALLER_ID",  "912264231897")
OUTBOUND_GATEWAY    = os.environ.get("OUTBOUND_GATEWAY",    "16584853209835561.zt.plivo.com")
# Dialplan extension added in /etc/freeswitch/dialplan/default/outbound_ai.xml
OUTBOUND_DP_EXT     = "outbound_ai_connect"
OUTBOUND_DP_CTX     = "default"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("dashboard")

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="LesCall Dashboard", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

_img_dir = STATIC_DIR / "img"
_img_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ---------------------------------------------------------------------------
# Runtime control state
# ---------------------------------------------------------------------------

class CmdOutputHub:
    """Broadcast command output lines to all connected SSE clients.
    New subscribers receive recent history so they see a full run even if
    they connect mid-way through.
    """
    MAX_HISTORY = 500

    def __init__(self) -> None:
        self._subs: list[asyncio.Queue[dict | None]] = []
        self._history: list[dict] = []
        self.running = False
        self.last_cmd: str = ""
        self.last_exit: int | None = None

    def subscribe(self) -> asyncio.Queue[dict | None]:
        q: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=300)
        for item in self._history[-200:]:
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                pass
        self._subs.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subs.remove(q)
        except ValueError:
            pass

    def publish(self, item: dict) -> None:
        self._history.append(item)
        if len(self._history) > self.MAX_HISTORY:
            self._history = self._history[-self.MAX_HISTORY:]
        for q in list(self._subs):
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    q.put_nowait(item)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    def clear(self) -> None:
        self._history.clear()


_cmd_hub = CmdOutputHub()
_cmd_lock = asyncio.Lock()


async def _exec_stream(script: Path, label: str) -> None:
    """Run a shell script and broadcast every output line to _cmd_hub.

    stop_all.sh is run via sudo so it can kill root-owned processes
    (services started from a root shell before the dashboard took over).
    Once services are managed by the dashboard they run as angelo and
    sudo is not needed — but it is harmless.
    """
    _cmd_hub.clear()
    _cmd_hub.running = True
    _cmd_hub.last_cmd = label
    _cmd_hub.last_exit = None

    _cmd_hub.publish({"type": "cmd_start", "cmd": label, "ts": _now_iso()})
    try:
        # Use sudo so the script can kill root-owned processes and bind/free
        # ports regardless of whether prior services were started as root.
        cmd = ["sudo", "bash", str(script)]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(LESCALL_ROOT),
        )
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip("\n")
            _cmd_hub.publish({"type": "line", "text": line, "ts": _now_iso()})
        await proc.wait()
        _cmd_hub.last_exit = proc.returncode
        _cmd_hub.publish({
            "type": "cmd_end",
            "exit_code": proc.returncode,
            "ok": proc.returncode == 0,
            "ts": _now_iso(),
        })
    except Exception as exc:
        _cmd_hub.publish({"type": "error", "text": str(exc), "ts": _now_iso()})
        _cmd_hub.last_exit = -1
    finally:
        _cmd_hub.running = False


# ---------------------------------------------------------------------------
# Credit log helpers
# ---------------------------------------------------------------------------

_credit_cache: dict[str, tuple[float, list[dict]]] = {}
_CREDIT_CACHE_TTL = 30.0


def _load_credit_log(date: datetime.date) -> list[dict]:
    key = date.isoformat()
    cached = _credit_cache.get(key)
    if cached and (time.monotonic() - cached[0]) < _CREDIT_CACHE_TTL:
        return cached[1]
    path = CREDIT_LOG_DIR / f"{key}.jsonl"
    events: list[dict] = []
    if path.exists():
        try:
            with path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            events.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        except Exception as exc:
            logger.warning("credit_log read error %s: %s", path, exc)
    _credit_cache[key] = (time.monotonic(), events)
    return events


def _load_credit_range(start: datetime.date, end: datetime.date) -> list[dict]:
    result = []
    d = start
    while d <= end:
        result.extend(_load_credit_log(d))
        d += datetime.timedelta(days=1)
    return result


def _aggregate_calls(events: list[dict]) -> list[dict]:
    starts: dict[str, str] = {}
    calls: list[dict] = []
    for ev in events:
        t = ev.get("type")
        if t == "call_start":
            starts[ev["call_id"]] = ev.get("ts", "")
        elif t == "call_end":
            cid = ev["call_id"]
            start_ts = starts.pop(cid, ev.get("start_time", ""))
            start_ts = start_ts or ev.get("start_time", "")
            end_ts = ev.get("ts", "")
            duration = 0.0
            try:
                if start_ts and end_ts:
                    sdt = datetime.datetime.fromisoformat(start_ts.replace("Z", "+00:00"))
                    edt = datetime.datetime.fromisoformat(end_ts.replace("Z", "+00:00"))
                    duration = max((edt - sdt).total_seconds(), 0.0)
            except Exception:
                pass
            calls.append({
                "call_id": cid,
                "start_time": start_ts,
                "end_time": end_ts,
                "duration_seconds": round(duration, 1),
                "stt_sarvam_seconds": ev.get("stt_sarvam_seconds", 0.0),
                "stt_sarvam_billed_seconds": ev.get("stt_sarvam_billed_seconds", 0.0),
                "stt_sarvam_calls": ev.get("stt_sarvam_calls", 0),
                "stt_whisper_seconds": ev.get("stt_whisper_seconds", 0.0),
                "stt_whisper_calls": ev.get("stt_whisper_calls", 0),
                "tts_chars": ev.get("tts_chars", 0),
                "tts_calls": ev.get("tts_calls", 0),
                "tts_cancelled_calls": ev.get("tts_cancelled_calls", 0),
                "est_stt_cost": ev.get("est_stt_cost", 0.0),
                "est_tts_cost": ev.get("est_tts_cost", 0.0),
                "est_total_cost": round(
                    ev.get("est_stt_cost", 0.0) + ev.get("est_tts_cost", 0.0), 6
                ),
            })
    calls.sort(key=lambda c: c["end_time"], reverse=True)
    return calls


def _get_active_calls_from_log(events: list[dict]) -> list[dict]:
    starts: dict[str, str] = {}
    ended: set[str] = set()
    has_activity: set[str] = set()   # call_ids with real STT/TTS events
    for ev in events:
        t = ev.get("type")
        cid = ev.get("call_id")
        if not cid:
            continue
        if t == "call_start":
            starts[cid] = ev.get("ts", "")
        elif t == "call_end":
            ended.add(cid)
        elif t in ("stt_event", "tts_event"):
            has_activity.add(cid)

    now = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
    timeout = datetime.timedelta(minutes=ACTIVE_CALL_TIMEOUT_MINUTES)
    grace = datetime.timedelta(seconds=30)   # brand-new calls get a grace period
    active = []
    for cid, ts in starts.items():
        if cid in ended:
            continue
        try:
            start_dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            age = now - start_dt
        except Exception:
            continue
        # Drop stale orphans older than timeout
        if age > timeout:
            continue
        # Drop quick-crash calls: older than grace period and zero real activity
        if age > grace and cid not in has_activity:
            continue
        active.append({"call_id": cid, "start_time": ts})
    return active


def _summarize_range(events: list[dict]) -> dict:
    calls = _aggregate_calls(events)
    stt_cost = sum(c["est_stt_cost"] for c in calls)
    tts_cost = sum(c["est_tts_cost"] for c in calls)
    return {
        "call_count": len(calls),
        "est_stt_cost": round(stt_cost, 4),
        "est_tts_cost": round(tts_cost, 4),
        "est_total_cost": round(stt_cost + tts_cost, 4),
        "stt_sarvam_billed_seconds": round(
            sum(c["stt_sarvam_billed_seconds"] for c in calls), 1),
        "tts_chars": sum(c["tts_chars"] for c in calls),
    }


def _in_range(ts: str, start_date: datetime.date, end_date: datetime.date) -> bool:
    if not ts:
        return False
    try:
        d = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).date()
        return start_date <= d <= end_date
    except Exception:
        return False


def _compute_kpis(events: list[dict], contacts: list[dict],
                   start_date: datetime.date, end_date: datetime.date) -> dict:
    """KPIs for the Overview tab. Every number here is derived from real
    stored data; where the data genuinely doesn't exist (inbound missed
    calls — the pipeline only ever logs calls that connected), the field is
    explicitly flagged rather than estimated.
    """
    calls = _aggregate_calls(events)
    inbound_calls = 0
    outbound_connected = 0
    minutes_used = 0.0
    for c in calls:
        minutes_used += c.get("duration_seconds", 0.0) / 60.0
        direction = _classify_direction(c.get("start_time", ""))["direction"]
        if direction == "outbound":
            outbound_connected += 1
        else:
            inbound_calls += 1

    outbound_attempts_in_range = []
    for c in contacts:
        for h in c.get("call_history", []):
            if _in_range(h.get("ts", ""), start_date, end_date):
                outbound_attempts_in_range.append(h)
    outbound_answered = sum(1 for h in outbound_attempts_in_range if h.get("call_status") == "answered")
    outbound_missed = sum(1 for h in outbound_attempts_in_range if h.get("call_status") != "answered")

    appointments_booked = sum(
        1 for c in contacts
        if c.get("disposition") == "site_visit_booked" and _in_range(c.get("last_called_at", ""), start_date, end_date)
    )
    follow_ups_required = sum(
        1 for c in contacts
        if c.get("disposition") in ("to_contact_later", "in_doubt")
        and _in_range(c.get("last_called_at", ""), start_date, end_date)
    )
    follow_ups_required += _count_ai_followups_in_range(start_date, end_date)

    return {
        "total_calls": inbound_calls + len(outbound_attempts_in_range),
        "answered_calls": inbound_calls + outbound_answered,
        "missed_calls": outbound_missed,
        "missed_calls_note": "Outbound only — inbound calls are only logged once connected to the AI pipeline, so a true inbound 'missed call' count isn't available without telephony-side event logging.",
        "inbound_calls": inbound_calls,
        "outbound_calls": len(outbound_attempts_in_range),
        "appointments_booked": appointments_booked,
        "follow_ups_required": follow_ups_required,
        "minutes_used": round(minutes_used, 1),
    }


# ---------------------------------------------------------------------------
# Call direction linking (outbound dial -> pipeline call)
# ---------------------------------------------------------------------------
#
# Outbound dials and inbound/pipeline calls are recorded in two completely
# separate stores (outbound_contacts.json vs credit_log/recordings) with no
# shared call_id — the FreeSWITCH originate command used for outbound calls
# never surfaces the resulting call_id back to the dashboard. To still
# distinguish inbound vs outbound for KPIs/charts, every dial attempt is
# logged here with its timestamp; a pipeline call is attributed to "outbound"
# if it started within OUTBOUND_MATCH_WINDOW_SECONDS of a logged dial. This
# is a timestamp heuristic, not an exact link — callers of
# `_classify_direction` should treat the result as best-effort.

def _load_call_link() -> list[dict]:
    if not CALL_LINK_FILE.exists():
        return []
    try:
        return json.loads(CALL_LINK_FILE.read_text())
    except Exception:
        return []


def _save_call_link(entries: list[dict]) -> None:
    CALL_LINK_FILE.write_text(json.dumps(entries, indent=2))


def _record_dial(phone: str, contact_id: str, name: str) -> None:
    entries = _load_call_link()
    entries.append({
        "phone": phone, "contact_id": contact_id, "name": name,
        "dialed_at": _now_iso(),
    })
    # Keep a bounded, recent window — only needed to match calls placed
    # within OUTBOUND_MATCH_WINDOW_SECONDS, so ancient entries are dead weight.
    entries = entries[-5000:]
    _save_call_link(entries)


def _classify_direction(start_time_iso: str) -> dict:
    """Best-effort: was this pipeline call likely outbound-originated?"""
    if not start_time_iso:
        return {"direction": "inbound", "contact_id": None, "name": None, "phone": None}
    try:
        start_dt = datetime.datetime.fromisoformat(start_time_iso.replace("Z", "+00:00"))
    except Exception:
        return {"direction": "inbound", "contact_id": None, "name": None, "phone": None}
    best = None
    best_delta = None
    for entry in _load_call_link():
        try:
            dial_dt = datetime.datetime.fromisoformat(entry["dialed_at"].replace("Z", "+00:00"))
        except Exception:
            continue
        delta = (start_dt - dial_dt).total_seconds()
        if 0 <= delta <= OUTBOUND_MATCH_WINDOW_SECONDS:
            if best_delta is None or delta < best_delta:
                best, best_delta = entry, delta
    if best:
        return {"direction": "outbound", "contact_id": best.get("contact_id"),
                "name": best.get("name"), "phone": best.get("phone")}
    return {"direction": "inbound", "contact_id": None, "name": None, "phone": None}


# ---------------------------------------------------------------------------
# Minutes quota (user-reported — same pattern as Sarvam credits: no provider
# quota API exists, so the total is a manually-synced number, not invented)
# ---------------------------------------------------------------------------

def _load_minutes_quota() -> dict:
    if not MINUTES_QUOTA_FILE.exists():
        return {"total_minutes": None, "updated_at": None}
    try:
        return json.loads(MINUTES_QUOTA_FILE.read_text())
    except Exception:
        return {"total_minutes": None, "updated_at": None}


def _save_minutes_quota(data: dict) -> None:
    MINUTES_QUOTA_FILE.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Recordings helpers
# ---------------------------------------------------------------------------

_recordings_cache: tuple[float, list[dict]] | None = None
_RECORDINGS_CACHE_TTL = 15.0  # short TTL so new calls appear quickly


def _scan_recordings(limit: int = 500) -> list[dict]:
    global _recordings_cache
    now = time.monotonic()
    if _recordings_cache and (now - _recordings_cache[0]) < _RECORDINGS_CACHE_TTL:
        return _recordings_cache[1]
    entries: list[str] = []
    try:
        with os.scandir(RECORDINGS_DIR) as it:
            for entry in it:
                if entry.is_dir():
                    entries.append(entry.name)
    except Exception:
        return []

    def _sort_key(name: str) -> str:
        m = re.match(r"^.+_(\d{8})_(\d{6})$", name)
        return (m.group(1) + m.group(2)) if m else "0"

    entries.sort(key=_sort_key, reverse=True)
    entries = entries[:limit]
    records = []
    for name in entries:
        meta_path = RECORDINGS_DIR / name / "metadata.json"
        rec: dict[str, Any] = {"folder": name, "call_id": None,
                                "caller": name.split("_")[0]}
        m = re.match(r"^(.+)_(\d{8})_(\d{6})$", name)
        if m:
            rec["caller"] = m.group(1)
            try:
                dt = datetime.datetime.strptime(m.group(2) + m.group(3),
                                                "%Y%m%d%H%M%S")
                rec["folder_dt"] = dt.isoformat()
            except ValueError:
                rec["folder_dt"] = ""
        if meta_path.exists():
            try:
                with meta_path.open() as f:
                    rec.update(json.load(f))
            except Exception:
                pass
        records.append(rec)
    _recordings_cache = (now, records)
    return records


def _is_recording_complete(folder: str) -> bool:
    """metadata.json is written exactly once, in CallAuditSession.close() —
    its presence is the signal a call is fully wrapped up and safe to read."""
    return (RECORDINGS_DIR / folder / "metadata.json").exists()


def _load_analysis_store() -> dict[str, dict]:
    """folder -> analysis dict. Stored in the dashboard's own directory, not
    inside recordings/<folder>/ — those directories are root-owned (call_audit.py
    runs as root) and not writable by the dashboard process, which runs as an
    unprivileged user. Keeping AI-analysis output in a dashboard-owned file
    also keeps it consistent with every other dashboard-managed store
    (call_link.json, follow_ups.json, outbound_contacts.json)."""
    if not ANALYSIS_STORE_FILE.exists():
        return {}
    try:
        return json.loads(ANALYSIS_STORE_FILE.read_text())
    except Exception:
        return {}


def _save_analysis_store(store: dict[str, dict]) -> None:
    ANALYSIS_STORE_FILE.write_text(json.dumps(store, indent=2))


def _scan_analyses() -> dict[str, dict]:
    return _load_analysis_store()


def _count_ai_followups_in_range(start_date: datetime.date, end_date: datetime.date) -> int:
    count = 0
    for folder, analysis in _scan_analyses().items():
        if not analysis.get("follow_up_needed"):
            continue
        m = re.match(r"^.+_(\d{8})_(\d{6})$", folder)
        if not m:
            continue
        try:
            dt = datetime.datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            continue
        if start_date <= dt <= end_date:
            count += 1
    return count


def _read_transcript(folder: str) -> list[dict]:
    path = RECORDINGS_DIR / folder / "transcript.txt"
    if not path.exists():
        return []
    messages = []
    current_ts = ""
    try:
        with path.open(encoding="utf-8") as f:
            content = f.read()
        for block in re.split(r"\n\n+", content.strip()):
            block = block.strip()
            if not block:
                continue
            lines = block.split("\n")
            ts_match = re.match(r"^\[(\d+:\d+\.\d+)\]$", lines[0].strip())
            if ts_match:
                current_ts = ts_match.group(1)
                text_line = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
            else:
                text_line = block
            if not text_line:
                continue
            idx = text_line.find(":")
            if idx > 0:
                speaker = text_line[:idx].strip()
                text = text_line[idx + 1:].strip()
            else:
                speaker = "Unknown"
                text = text_line
            messages.append({"speaker": speaker, "text": text,
                              "timestamp": current_ts})
    except Exception as exc:
        logger.warning("transcript read error %s: %s", folder, exc)
    return messages


# ---------------------------------------------------------------------------
# Health / service status helpers
# ---------------------------------------------------------------------------

def _check_port(port: int) -> bool:
    try:
        result = subprocess.run(["ss", "-ltn"], capture_output=True,
                                text=True, timeout=2)
        return f":{port} " in result.stdout
    except Exception:
        return False


def _check_pid(pid_str: str) -> bool:
    try:
        return os.path.exists(f"/proc/{int(pid_str.strip())}")
    except (ValueError, TypeError):
        return False


def _get_runtime_health() -> dict:
    services: dict[str, dict] = {}
    run_dir = LOGS_LATEST
    if run_dir.is_symlink() or run_dir.is_dir():
        for pid_file in run_dir.glob("*.pid"):
            if pid_file.stem == "call_bridge_filter":
                continue
            try:
                pid_str = pid_file.read_text().strip()
                services[pid_file.stem] = {
                    "pid": pid_str,
                    "running": _check_pid(pid_str),
                }
            except Exception:
                services[pid_file.stem] = {"pid": "?", "running": False}

    ports = {str(p): _check_port(p) for p in [9000, 9001]}
    all_up = (
        bool(services)
        and all(s["running"] for s in services.values())
        and all(ports.values())
    )
    return {
        "services": services,
        "ports": ports,
        "overall": "healthy" if all_up else "degraded",
        "checked_at": _now_iso(),
        "logs_dir": str(run_dir) if run_dir.exists() else None,
        "cmd_running": _cmd_hub.running,
        "cmd_last": _cmd_hub.last_cmd,
        "cmd_exit": _cmd_hub.last_exit,
    }


# ---------------------------------------------------------------------------
# Log streaming helpers
# ---------------------------------------------------------------------------

_LOG_PATTERNS = {
    "error":   re.compile(r"ERROR|CRITICAL|Failed|failed|Error|Exception|Traceback",
                          re.IGNORECASE),
    "stt":     re.compile(r"\[STT|transcript|stt_stream_worker", re.IGNORECASE),
    "ai":      re.compile(
        r"\[LLM\]|\[TTS\]|\[ENGINE\]|\[CONVERSATION\]|\[PIPELINE\].*Agent|\[PIPELINE\].*greeting",
        re.IGNORECASE),
    "credits": re.compile(r"\[CREDITS\]"),
    "call":    re.compile(r"\[CALL_BRIDGE\]|\[AUDIT\]|\[SESSION"),
}
_LOG_COLORS = {
    "error": "#f85149", "stt": "#a5a0f8", "ai": "#3fb950",
    "credits": "#d29922", "call": "#38bdf8", "system": "#7d8590",
}


def _categorize_line(line: str) -> tuple[str, str]:
    for cat, pat in _LOG_PATTERNS.items():
        if pat.search(line):
            return cat, _LOG_COLORS[cat]
    return "system", _LOG_COLORS["system"]


async def _tail_file(path: Path, tail_lines: int = 100) -> AsyncIterator[str]:
    if not path.exists():
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            "tail", "-n", str(tail_lines), str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        for line in stdout.decode(errors="replace").splitlines():
            yield line
    except Exception:
        pass
    try:
        proc = await asyncio.create_subprocess_exec(
            "tail", "-F", "-n", "0", str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        assert proc.stdout is not None
        while True:
            line_bytes = await asyncio.wait_for(proc.stdout.readline(), timeout=30.0)
            if not line_bytes:
                break
            yield line_bytes.decode(errors="replace").rstrip("\n")
    except asyncio.TimeoutError:
        pass
    except asyncio.CancelledError:
        raise
    except Exception:
        pass


async def _log_event_stream(categories: set[str]) -> AsyncIterator[str]:
    log_files = [LOGS_LATEST / "ws_audio_receiver.log",
                 LOGS_LATEST / "socket_server.log"]
    queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=500)

    async def feed(path: Path) -> None:
        async for line in _tail_file(path):
            try:
                queue.put_nowait(line)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(line)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    tasks = [asyncio.create_task(feed(p)) for p in log_files]
    try:
        while True:
            try:
                line = await asyncio.wait_for(queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                yield 'data: {"type":"keepalive"}\n\n'
                continue
            if not line or not line.strip():
                continue
            cat, color = _categorize_line(line)
            if categories and cat not in categories and "all" not in categories:
                continue
            yield f"data: {json.dumps({'type':'log','category':cat,'color':color,'line':line,'ts':_now_iso()})}\n\n"
    except asyncio.CancelledError:
        pass
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.datetime.utcnow().isoformat(timespec="milliseconds") + "Z"


# ---------------------------------------------------------------------------
# Routes — static
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def root() -> HTMLResponse:
    p = STATIC_DIR / "index.html"
    return HTMLResponse(p.read_text(encoding="utf-8") if p.exists()
                        else "<h1>index.html not found</h1>", status_code=200 if p.exists() else 500)


# ---------------------------------------------------------------------------
# Routes — runtime control
# ---------------------------------------------------------------------------

@app.get("/api/runtime/status")
async def runtime_status() -> JSONResponse:
    return JSONResponse(_get_runtime_health())


@app.post("/api/runtime/start")
async def runtime_start() -> JSONResponse:
    if _cmd_hub.running:
        return JSONResponse({"ok": False, "reason": "A command is already running"}, status_code=409)
    if not START_SCRIPT.exists():
        return JSONResponse({"ok": False, "reason": f"start script not found: {START_SCRIPT}"}, status_code=500)
    async with _cmd_lock:
        if _cmd_hub.running:
            return JSONResponse({"ok": False, "reason": "already running"}, status_code=409)
        asyncio.create_task(_exec_stream(START_SCRIPT, "start"))
    return JSONResponse({"ok": True, "msg": "Starting calling agent…"})


@app.post("/api/runtime/stop")
async def runtime_stop() -> JSONResponse:
    if _cmd_hub.running:
        return JSONResponse({"ok": False, "reason": "A command is already running"}, status_code=409)
    if not STOP_SCRIPT.exists():
        return JSONResponse({"ok": False, "reason": f"stop script not found: {STOP_SCRIPT}"}, status_code=500)
    async with _cmd_lock:
        if _cmd_hub.running:
            return JSONResponse({"ok": False, "reason": "already running"}, status_code=409)
        asyncio.create_task(_exec_stream(STOP_SCRIPT, "stop"))
    return JSONResponse({"ok": True, "msg": "Stopping calling agent…"})


@app.post("/api/runtime/restart")
async def runtime_restart() -> JSONResponse:
    if _cmd_hub.running:
        return JSONResponse({"ok": False, "reason": "A command is already running"}, status_code=409)

    async def _do_restart() -> None:
        await _exec_stream(STOP_SCRIPT, "stop")
        await asyncio.sleep(1.5)
        await _exec_stream(START_SCRIPT, "start")

    asyncio.create_task(_do_restart())
    return JSONResponse({"ok": True, "msg": "Restarting calling agent…"})


@app.get("/api/runtime/output/stream")
async def runtime_output_stream(request: Request) -> StreamingResponse:
    async def generator() -> AsyncIterator[str]:
        q = _cmd_hub.subscribe()
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(q.get(), timeout=20.0)
                    yield f"data: {json.dumps(item)}\n\n"
                except asyncio.TimeoutError:
                    yield 'data: {"type":"keepalive"}\n\n'
        except asyncio.CancelledError:
            pass
        finally:
            _cmd_hub.unsubscribe(q)

    return StreamingResponse(generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------------
# Routes — data APIs
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health() -> JSONResponse:
    return JSONResponse(_get_runtime_health())


@app.get("/api/overview")
async def overview(period: str = Query("today")) -> JSONResponse:
    today = datetime.date.today()
    week_start  = today - datetime.timedelta(days=6)
    month_start = today - datetime.timedelta(days=29)

    today_events = _load_credit_log(today)
    week_events  = _load_credit_range(week_start, today)
    month_events = _load_credit_range(month_start, today)

    hourly: dict[int, int] = {h: 0 for h in range(24)}
    for ev in today_events:
        if ev.get("type") == "call_end":
            try:
                dt = datetime.datetime.fromisoformat(
                    ev.get("ts", "").replace("Z", "+00:00"))
                hourly[dt.hour] += 1
            except Exception:
                pass

    period_starts = {"today": today, "week": week_start, "month": month_start}
    kpi_start = period_starts.get(period, today)
    contacts = _load_outbound()
    period_events = {"today": today_events, "week": week_events, "month": month_events}.get(period, today_events)

    # Calls Over Time — daily volume, last 14 days, all-pipeline calls (inbound + outbound-connected)
    daily_calls = []
    for i in range(13, -1, -1):
        d = today - datetime.timedelta(days=i)
        daily_calls.append({"date": d.isoformat(), "count": len(_aggregate_calls(_load_credit_log(d)))})

    # Call Type / Status Distribution for the selected period
    kpis = _compute_kpis(period_events, contacts, kpi_start, today)
    call_type_distribution = {"inbound": kpis["inbound_calls"], "outbound": kpis["outbound_calls"]}
    call_status_distribution = {
        "answered": kpis["answered_calls"], "missed": kpis["missed_calls"], "failed": 0,
    }

    quota = _load_minutes_quota()
    total_minutes = quota.get("total_minutes")
    remaining_minutes = (round(total_minutes - kpis["minutes_used"], 1)
                          if total_minutes is not None else None)

    return JSONResponse({
        "today":        _summarize_range(today_events),
        "week":         _summarize_range(week_events),
        "month":        _summarize_range(month_events),
        "active_calls": len(_get_active_calls_from_log(today_events)),
        "runtime_status": _get_runtime_health()["overall"],
        "hourly_calls": [{"hour": h, "count": hourly[h]} for h in range(24)],
        "kpis": kpis,
        "minutes_quota": {"total_minutes": total_minutes, "remaining_minutes": remaining_minutes},
        "charts": {
            "calls_over_time": daily_calls,
            "call_type_distribution": call_type_distribution,
            "call_status_distribution": call_status_distribution,
        },
    })


@app.get("/api/minutes/quota")
async def minutes_quota_get() -> JSONResponse:
    return JSONResponse(_load_minutes_quota())


@app.post("/api/minutes/quota")
async def minutes_quota_set(request: Request) -> JSONResponse:
    body = await request.json()
    try:
        total = float(body.get("total_minutes"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "reason": "total_minutes must be a number"}, status_code=400)
    data = {"total_minutes": total, "updated_at": _now_iso()}
    _save_minutes_quota(data)
    return JSONResponse({"ok": True, **data})


@app.get("/api/calls/active")
async def active_calls() -> JSONResponse:
    today = datetime.date.today()
    events = _load_credit_log(today)
    active = _get_active_calls_from_log(events)
    now = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
    result = []
    for call in active:
        start_ts = call.get("start_time", "")
        dur = 0.0
        try:
            if start_ts:
                s = datetime.datetime.fromisoformat(start_ts.replace("Z", "+00:00"))
                dur = (now - s).total_seconds()
        except Exception:
            pass
        result.append({"call_id": call["call_id"], "start_time": start_ts,
                        "duration_seconds": round(dur, 0)})
    return JSONResponse({"active_calls": result, "count": len(result)})


def _read_live_state() -> dict:
    if not LIVE_STATE_FILE.exists():
        return {"updated_at": None, "calls": []}
    try:
        with LIVE_STATE_FILE.open() as f:
            return json.load(f)
    except Exception:
        return {"updated_at": None, "calls": []}


@app.get("/api/calls/live-state")
async def calls_live_state() -> JSONResponse:
    return JSONResponse(_read_live_state())


async def _live_state_event_stream() -> AsyncIterator[str]:
    last_mtime: float | None = None
    while True:
        try:
            mtime = LIVE_STATE_FILE.stat().st_mtime if LIVE_STATE_FILE.exists() else None
        except Exception:
            mtime = None
        if mtime != last_mtime:
            last_mtime = mtime
            yield f"data: {json.dumps({'type': 'live_state', **_read_live_state()})}\n\n"
        else:
            yield 'data: {"type":"keepalive"}\n\n'
        await asyncio.sleep(1.0)


@app.get("/api/calls/live-state/stream")
async def calls_live_state_stream(request: Request) -> StreamingResponse:
    async def generator() -> AsyncIterator[str]:
        async for chunk in _live_state_event_stream():
            if await request.is_disconnected():
                break
            yield chunk

    return StreamingResponse(generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


_recording_phone_cache: tuple[float, dict[str, str]] | None = None
_RECORDING_PHONE_CACHE_TTL = 120  # seconds


def _build_recording_phone_map() -> dict[str, str]:
    """Scan recording metadata.json files and build {call_id: caller_phone} map.
    Phone is extracted from folder name (segment before first underscore) only
    if it has >= 7 digits — skips extension-style short numbers."""
    global _recording_phone_cache
    now = time.monotonic()
    if _recording_phone_cache and now - _recording_phone_cache[0] < _RECORDING_PHONE_CACHE_TTL:
        return _recording_phone_cache[1]
    result: dict[str, str] = {}
    try:
        for folder_path in RECORDINGS_DIR.iterdir():
            if not folder_path.is_dir():
                continue
            folder = folder_path.name
            phone_candidate = folder.split("_")[0]
            if not phone_candidate.isdigit() or len(phone_candidate) < 7:
                continue
            meta_path = folder_path / "metadata.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text())
                cid = meta.get("call_id")
                if cid:
                    result[cid] = phone_candidate
            except Exception:
                pass
    except Exception:
        pass
    _recording_phone_cache = (now, result)
    return result


def _build_call_activity(start_date: datetime.date, end_date: datetime.date) -> list[dict]:
    """Unified Call Activity feed: pipeline calls (always 'answered' — the
    pipeline only logs calls that connected) plus outbound dial attempts
    that never connected (no_answer/busy/declined/failed), which never reach
    credit_log at all. direction is best-effort (see _classify_direction)."""
    events = _load_credit_range(start_date, end_date)
    # Original per-call fields (stt/tts seconds & costs) are preserved as-is —
    # the existing Call History table already renders them — with call_type/
    # status/contact fields layered on top, additive only.
    _ZERO_COST_FIELDS = {"stt_sarvam_seconds": 0.0, "stt_sarvam_billed_seconds": 0.0,
                          "stt_sarvam_calls": 0, "stt_whisper_seconds": 0.0, "stt_whisper_calls": 0,
                          "tts_chars": 0, "tts_calls": 0, "tts_cancelled_calls": 0,
                          "est_stt_cost": 0.0, "est_tts_cost": 0.0, "est_total_cost": 0.0}
    rows = []
    phone_map = _build_recording_phone_map()
    for c in _aggregate_calls(events):
        link = _classify_direction(c.get("start_time", ""))
        contact_name = link.get("name") or ""
        contact_phone = link.get("phone") or ""
        if not contact_phone and link["direction"] == "inbound":
            contact_phone = phone_map.get(c.get("call_id", ""), "")
        rows.append({
            **c,
            "call_type": link["direction"],
            "contact_name": contact_name, "contact_phone": contact_phone,
            "status": "answered",
        })
    for contact in _load_outbound():
        for h in contact.get("call_history", []):
            if h.get("call_status") == "answered":
                continue  # represented by its matched pipeline row above
            if not _in_range(h.get("ts", ""), start_date, end_date):
                continue
            rows.append({
                "call_id": None, "end_time": h.get("ts", ""), "start_time": h.get("ts", ""),
                "duration_seconds": 0.0,
                **_ZERO_COST_FIELDS,
                "call_type": "outbound",
                "contact_name": contact.get("name", ""), "contact_phone": contact.get("phone", ""),
                "status": h.get("call_status", "failed"),
            })
    rows.sort(key=lambda r: r["end_time"], reverse=True)
    return rows


@app.get("/api/calls/history")
async def call_history(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    search: str = Query(""),
    days: int = Query(7, ge=1, le=90),
    call_type: str = Query("all"),
    status: str = Query("all"),
) -> JSONResponse:
    end_date   = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=days - 1)
    rows = _build_call_activity(start_date, end_date)
    if call_type != "all":
        rows = [r for r in rows if r["call_type"] == call_type]
    if status != "all":
        wanted = {"missed": ("no_answer", "busy", "declined")}.get(status, (status,))
        rows = [r for r in rows if r["status"] in wanted]
    if search:
        sl = search.lower()
        rows = [r for r in rows if sl in (r.get("call_id") or "").lower()
                or sl in (r.get("contact_name") or "").lower()
                or sl in (r.get("contact_phone") or "").lower()]
    total = len(rows)
    page_rows = rows[(page - 1) * limit: page * limit]
    return JSONResponse({"calls": page_rows, "total": total, "page": page,
                         "limit": limit, "pages": math.ceil(total / limit) if total else 1})


@app.get("/api/calls/recordings")
async def recordings_list(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    search: str = Query(""),
) -> JSONResponse:
    records = _scan_recordings(limit=2000)
    if search:
        s = search.lower()
        records = [r for r in records
                   if s in r.get("folder", "").lower()
                   or s in r.get("call_id", "").lower()
                   or s in r.get("caller", "").lower()]
    total = len(records)
    return JSONResponse({
        "recordings": records[(page - 1) * limit: page * limit],
        "total": total, "page": page, "limit": limit,
        "pages": math.ceil(total / limit) if total else 1,
    })


@app.get("/api/calls/recordings/{folder}/transcript")
async def recording_transcript(folder: str) -> JSONResponse:
    if "/" in folder or ".." in folder:
        return JSONResponse({"error": "invalid folder"}, status_code=400)
    meta: dict = {}
    meta_path = RECORDINGS_DIR / folder / "metadata.json"
    if meta_path.exists():
        try:
            with meta_path.open() as f:
                meta = json.load(f)
        except Exception:
            pass
    return JSONResponse({"folder": folder, "metadata": meta,
                         "messages": _read_transcript(folder)})


# ---------------------------------------------------------------------------
# AI Call Analysis — on demand, per selected call, after it has completed.
#
# This reads only transcript.txt (already on disk once a call ends) and
# makes a single fresh LLM call; it never touches the live conversation
# engine, CallState, or any file written by call_audit.py. Results are
# cached to recordings/<folder>/analysis.json so the same call is never
# re-billed unless the user explicitly re-runs it.
# ---------------------------------------------------------------------------

_ANALYSIS_MODEL = os.environ.get("DASHBOARD_ANALYSIS_MODEL", "gpt-4o-mini")
_analysis_client: Any = None


def _get_analysis_client() -> Any:
    global _analysis_client
    if _analysis_client is None:
        from openai import AsyncOpenAI
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key and CALLING_AGENT_ENV_FILE.exists():
            m = re.search(r'^OPENAI_API_KEY=(.*)$', CALLING_AGENT_ENV_FILE.read_text(), re.MULTILINE)
            api_key = m.group(1).strip() if m else ""
        _analysis_client = AsyncOpenAI(api_key=api_key, timeout=30.0)
    return _analysis_client


_ANALYSIS_SYSTEM_PROMPT = """You analyze a phone call transcript between a sales agent and a prospect/customer. Respond ONLY with a JSON object with these exact keys:
{
  "summary": "2-3 sentence plain-language summary of the call",
  "customer_intent": "short phrase describing what the customer wants",
  "key_points": ["short bullet", "short bullet"],
  "customer_questions": ["question the customer literally asked, if any"],
  "suggested_next_action": "what the agent/business should do next",
  "sentiment": "positive" | "neutral" | "negative",
  "lead_quality": "hot" | "warm" | "cold",
  "objections": ["objection raised, if any"],
  "topics": ["topic discussed"],
  "products_mentioned": ["product/project name mentioned, if any"],
  "follow_up_needed": true | false,
  "follow_up_reason": "why a follow-up is/isn't needed",
  "follow_up_priority": "high" | "medium" | "low",
  "recommended_follow_up_date": "YYYY-MM-DD or null"
}
Use null/empty values honestly if the transcript is too short or unclear to judge something — never invent specifics that aren't supported by the transcript."""


async def _analyze_transcript(folder: str) -> dict:
    messages = _read_transcript(folder)
    if not messages:
        raise ValueError("No transcript available for this call")
    transcript_text = "\n".join(f"{m['speaker']}: {m['text']}" for m in messages)
    client = _get_analysis_client()
    response = await client.chat.completions.create(
        model=_ANALYSIS_MODEL,
        messages=[
            {"role": "system", "content": _ANALYSIS_SYSTEM_PROMPT},
            {"role": "user", "content": f"Transcript:\n{transcript_text}"},
        ],
        response_format={"type": "json_object"},
        max_tokens=600,
        temperature=0.2,
    )
    result = json.loads(response.choices[0].message.content)
    result["_analyzed_at"] = _now_iso()
    result["_model"] = _ANALYSIS_MODEL
    return result


@app.get("/api/analysis/eligible")
async def analysis_eligible(
    page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=200),
    search: str = Query(""),
) -> JSONResponse:
    """Calls that have finished (metadata.json exists) and so are safe to
    analyze, annotated with whether an analysis already exists."""
    records = [r for r in _scan_recordings(limit=2000) if _is_recording_complete(r["folder"])]
    analyses = _scan_analyses()
    for r in records:
        r["analyzed"] = r["folder"] in analyses
    if search:
        s = search.lower()
        records = [r for r in records if s in r.get("folder", "").lower() or s in r.get("caller", "").lower()]
    total = len(records)
    return JSONResponse({
        "recordings": records[(page - 1) * limit: page * limit],
        "total": total, "page": page, "limit": limit,
        "pages": math.ceil(total / limit) if total else 1,
    })


@app.get("/api/calls/recordings/{folder}/analysis")
async def get_analysis(folder: str) -> JSONResponse:
    if "/" in folder or ".." in folder:
        return JSONResponse({"error": "invalid folder"}, status_code=400)
    store = _load_analysis_store()
    if folder not in store:
        return JSONResponse({"analyzed": False, "analysis": None})
    return JSONResponse({"analyzed": True, "analysis": store[folder]})


@app.post("/api/analysis/run")
async def run_analysis(request: Request) -> JSONResponse:
    body = await request.json()
    folders = body.get("folders") or []
    force = bool(body.get("force", False))
    if not isinstance(folders, list) or not folders:
        return JSONResponse({"ok": False, "reason": "folders (list) is required"}, status_code=400)

    store = _load_analysis_store()
    results: dict[str, Any] = {}
    for folder in folders:
        if "/" in folder or ".." in folder:
            results[folder] = {"ok": False, "error": "invalid folder"}
            continue
        if not _is_recording_complete(folder):
            results[folder] = {"ok": False, "error": "call not yet complete"}
            continue
        if folder in store and not force:
            results[folder] = {"ok": True, "analysis": store[folder], "cached": True}
            continue
        try:
            analysis = await _analyze_transcript(folder)
            store[folder] = analysis
            results[folder] = {"ok": True, "analysis": analysis, "cached": False}
        except Exception as exc:
            logger.error("analysis failed for %s: %r", folder, exc)
            results[folder] = {"ok": False, "error": str(exc)}
    _save_analysis_store(store)
    return JSONResponse({"ok": True, "results": results})


# ---------------------------------------------------------------------------
# Agent Settings — READ-ONLY introspection of the live agent's configuration.
#
# The agent's identity/prompt/pitch/objection-handling text is hardcoded as
# Python string literals inside conversation.py's _build_system_prompt() —
# there is no separate prompt/pitch/knowledge-base file today. Editing that
# live conversation engine is out of scope here (it drives real calls); this
# section only reads conversation.py as plain text and extracts the static
# literal lines for display, so the dashboard has *something* to show in
# this tab without risking the call pipeline. It never imports or executes
# conversation.py.
# ---------------------------------------------------------------------------

def _extract_quoted_lines_after(text: str, marker: str) -> list[str]:
    """Given the raw source text, find `marker` and return every following
    quoted string literal up to the next blank "" entry or section marker.
    Skips f-string lines (they contain live CallState, not static config)."""
    idx = text.find(marker)
    if idx == -1:
        return []
    rest = text[idx + len(marker):]
    lines = []
    for line in rest.splitlines()[1:]:
        stripped = line.strip()
        if stripped in ('"",', '""'):
            break
        if "━━" in stripped:
            break
        m = re.match(r'^"(.*)",?$', stripped)
        if not m:
            if lines:
                break
            continue
        content = m.group(1)
        if "{" in content:
            continue  # dynamic (f-string) line, not static config
        lines.append(content)
    return lines


def _introspect_agent_config() -> dict:
    if not CONVERSATION_PY.exists():
        return {"available": False, "reason": "conversation.py not found"}
    try:
        text = CONVERSATION_PY.read_text(encoding="utf-8")
    except Exception as exc:
        return {"available": False, "reason": str(exc)}

    identity_lines = _extract_quoted_lines_after(text, '"━━ AGENT IDENTITY ━━",')
    brand_lines = _extract_quoted_lines_after(text, '"━━ BRAND ━━",')
    portfolio_lines = _extract_quoted_lines_after(text, '"━━ PROJECT PORTFOLIO ━━",')
    guidelines_lines = _extract_quoted_lines_after(text, '"━━ CONVERSATION GUIDELINES ━━",')
    objections_lines = _extract_quoted_lines_after(text, '"━━ OBJECTION HANDLING ━━",')
    firm_rules_lines = _extract_quoted_lines_after(text, '"━━ FIRM RULES ━━",')

    greeting_match = re.search(r'"Hello! Namaste[^"]*"', text)
    name_match = re.search(r"You are ([\w\s]+?) — a (.+?) inbound sales agent", " ".join(identity_lines))

    return {
        "available": True,
        "agent_name": name_match.group(1).strip() if name_match else None,
        "agent_tone": name_match.group(2).strip() if name_match else None,
        "greeting": greeting_match.group(0).strip('"') if greeting_match else None,
        "identity": identity_lines,
        "brand": brand_lines,
        "prompt_guidelines": guidelines_lines,
        "firm_rules": firm_rules_lines,
        "pitch": {
            "project_portfolio": portfolio_lines,
            "objection_handling": objections_lines,
        },
        "source_file": str(CONVERSATION_PY),
        "editable": False,
        "editable_note": ("Read-only for now — this is generated directly from the live "
                          "conversation engine's source code, not a separate config file. "
                          "Editing support (separate prompt/pitch/knowledge-base files the "
                          "engine loads at runtime) is planned for a future update."),
    }


@app.get("/api/agent/config")
async def agent_config() -> JSONResponse:
    return JSONResponse(_introspect_agent_config())


@app.get("/api/analytics/conversation")
async def conversation_analytics(folders: str = Query("")) -> JSONResponse:
    """Aggregates AI Call Analysis results. If folders is provided (comma-separated),
    only those folders are included; otherwise all analyzed calls are returned."""
    all_analyses = _scan_analyses()
    if folders.strip():
        folder_set = {f.strip() for f in folders.split(",") if f.strip()}
        analyses = {k: v for k, v in all_analyses.items() if k in folder_set}
    else:
        analyses = all_analyses
    n = len(analyses)

    def _counter(key: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for a in analyses.values():
            v = a.get(key)
            if v:
                out[v] = out.get(v, 0) + 1
        return out

    def _list_counter(key: str) -> list[dict]:
        out: dict[str, int] = {}
        for a in analyses.values():
            for item in (a.get(key) or []):
                if item:
                    out[item] = out.get(item, 0) + 1
        return sorted([{"text": k, "count": v} for k, v in out.items()],
                      key=lambda x: x["count"], reverse=True)[:15]

    sentiment = _counter("sentiment")
    lead_quality = _counter("lead_quality")
    objections = _list_counter("objections")
    topics = _list_counter("topics")
    products = _list_counter("products_mentioned")
    questions = _list_counter("customer_questions")

    recommendations = []
    if objections:
        recommendations.append(f"Most common objection: \"{objections[0]['text']}\" ({objections[0]['count']} of {n} analyzed calls).")
    if products:
        recommendations.append(f"Highest customer interest: \"{products[0]['text']}\" ({products[0]['count']} mentions across analyzed calls).")
    if lead_quality.get("hot", 0):
        recommendations.append(f"{lead_quality['hot']} of {n} analyzed calls are classified as hot leads — ready to buy.")
    if sentiment.get("negative", 0) and n:
        pct = round(sentiment["negative"] / n * 100)
        recommendations.append(f"{pct}% of analyzed calls had negative sentiment.")
    if not recommendations:
        recommendations.append("Not enough analyzed calls yet to surface a pattern — analyze more calls from the AI Call Analysis tab.")

    trends: dict[str, dict] = {}
    for folder, a in analyses.items():
        m = re.match(r"^.+_(\d{8})_(\d{6})$", folder)
        day = m.group(1) if m else "unknown"
        day_fmt = f"{day[:4]}-{day[4:6]}-{day[6:8]}" if m else "unknown"
        bucket = trends.setdefault(day_fmt, {"positive": 0, "neutral": 0, "negative": 0,
                                              "hot": 0, "warm": 0, "cold": 0})
        if a.get("sentiment") in bucket:
            bucket[a["sentiment"]] += 1
        if a.get("lead_quality") in bucket:
            bucket[a["lead_quality"]] += 1

    return JSONResponse({
        "analyzed_call_count": n,
        "sentiment": sentiment,
        "lead_intelligence": lead_quality,
        "common_objections": objections,
        "topic_analysis": topics,
        "product_interest": products,
        "top_customer_questions": questions,
        "ai_recommendations": recommendations,
        "trends": [{"date": d, **vals} for d, vals in sorted(trends.items())],
    })


@app.get("/api/calls/transcripts/search")
async def transcripts_search(q: str = Query(..., min_length=1), limit: int = Query(50, ge=1, le=200)) -> JSONResponse:
    """Full-text search across transcript.txt files. There's no structured
    product/pricing/budget/competitor/location field anywhere in the data —
    only the raw transcript text — so all of those are just keyword search
    against transcript content today."""
    ql = q.lower()
    matches = []
    for r in _scan_recordings(limit=2000):
        messages = _read_transcript(r["folder"])
        text = " ".join(m["text"] for m in messages).lower()
        if ql in text:
            matches.append(r)
        if len(matches) >= limit:
            break
    return JSONResponse({"recordings": matches, "total": len(matches), "query": q})


@app.get("/api/calls/recordings/{folder}/audio")
async def recording_audio(folder: str, download: int = Query(0)) -> FileResponse:
    if "/" in folder or ".." in folder:
        return JSONResponse({"error": "invalid folder"}, status_code=400)
    audio_path = RECORDINGS_DIR / folder / "caller_audio.wav"
    if not audio_path.exists():
        return JSONResponse({"error": "no audio for this recording"}, status_code=404)
    headers = {}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{folder}.wav"'
    return FileResponse(str(audio_path), media_type="audio/wav", headers=headers)


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 10:
        return key[:2] + "…" + key[-2:]
    return key[:6] + "…" + key[-4:]


def _read_active_api_key() -> str:
    if not CALLING_AGENT_ENV_FILE.exists():
        return ""
    m = re.search(r'^SARVAM_API_KEY=(.*)$', CALLING_AGENT_ENV_FILE.read_text(), re.MULTILINE)
    return m.group(1).strip() if m else ""


def _write_active_api_key(new_key: str) -> None:
    text = CALLING_AGENT_ENV_FILE.read_text() if CALLING_AGENT_ENV_FILE.exists() else ""
    if re.search(r'^SARVAM_API_KEY=.*$', text, re.MULTILINE):
        text = re.sub(r'^SARVAM_API_KEY=.*$', f'SARVAM_API_KEY={new_key}', text, flags=re.MULTILINE)
    else:
        text = text.rstrip("\n") + f"\nSARVAM_API_KEY={new_key}\n"
    CALLING_AGENT_ENV_FILE.write_text(text)


def _load_api_key_pool() -> list[str]:
    if not API_KEY_POOL_FILE.exists():
        return []
    try:
        return json.loads(API_KEY_POOL_FILE.read_text()).get("pool", [])
    except Exception:
        return []


def _save_api_key_pool(pool: list[str]) -> None:
    API_KEY_POOL_FILE.write_text(json.dumps({"pool": pool}, indent=2))


def _load_key_state() -> dict:
    if not API_KEY_STATE_FILE.exists():
        return {"restart_required": False, "last_changed_at": None}
    try:
        return json.loads(API_KEY_STATE_FILE.read_text())
    except Exception:
        return {"restart_required": False, "last_changed_at": None}


def _save_key_state(state: dict) -> None:
    API_KEY_STATE_FILE.write_text(json.dumps(state, indent=2))


def _apply_new_key(new_key: str, *, total_credits: float | None = None) -> dict:
    """Write a new active key to .env, reset the tracked credit balance to
    full, and mark a restart as required (the running agent process only
    reads SARVAM_API_KEY once, at startup — a key swap here doesn't reach it
    until the process is relaunched)."""
    _write_active_api_key(new_key)
    current = _load_sarvam_credits()
    total = total_credits if total_credits is not None else current.get("total_credits", 100)
    data = {"total_credits": total, "remaining_credits": total, "updated_at": _now_iso()}
    _save_sarvam_credits(data)
    _save_key_state({"restart_required": True, "last_changed_at": _now_iso()})
    return data


def _maybe_auto_rotate_key() -> dict | None:
    """If credits are exhausted and a backup key is queued, auto-promote it.
    Called as a side effect of polling /api/sarvam/credits so it self-drives
    off the dashboard's existing polling cadence — no separate background
    daemon needed. Still requires a manual restart to take effect on live
    calls (see _apply_new_key)."""
    credits = _load_sarvam_credits()
    if credits.get("remaining_credits", 0) > 0:
        return None
    pool = _load_api_key_pool()
    if not pool:
        return None
    next_key = pool.pop(0)
    _save_api_key_pool(pool)
    _apply_new_key(next_key)
    return {"rotated_to": _mask_key(next_key), "pool_remaining": len(pool)}


def _load_sarvam_credits() -> dict:
    """User-reported Sarvam account credit balance.

    Sarvam exposes no quota API (see credit_tracker.py's own docstring), so
    this is a manually-synced number, not derived from the ₹ usage estimate
    elsewhere on this tab — there's no documented ₹-per-credit conversion
    rate to derive it from, and inventing one would misrepresent a real
    account balance as a computed fact.
    """
    if not SARVAM_CREDITS_FILE.exists():
        return {"total_credits": 100, "remaining_credits": 100, "updated_at": None}
    try:
        return json.loads(SARVAM_CREDITS_FILE.read_text())
    except Exception:
        return {"total_credits": 100, "remaining_credits": 100, "updated_at": None}


def _save_sarvam_credits(data: dict) -> None:
    SARVAM_CREDITS_FILE.write_text(json.dumps(data, indent=2))


@app.get("/api/sarvam/credits")
async def sarvam_credits() -> JSONResponse:
    rotation = _maybe_auto_rotate_key()
    d = _load_sarvam_credits()
    total = d.get("total_credits", 100)
    remaining = d.get("remaining_credits", total)
    used = max(total - remaining, 0)
    percent_used = round((used / total) * 100, 1) if total else 0.0
    exhausted = remaining <= 0
    pool_count = len(_load_api_key_pool())
    key_state = _load_key_state()
    return JSONResponse({
        "total_credits": total, "remaining_credits": remaining,
        "used_credits": used, "percent_used": percent_used,
        "updated_at": d.get("updated_at"),
        "low": 0 < remaining <= LOW_CREDIT_THRESHOLD,
        "exhausted": exhausted,
        "needs_new_key": exhausted and pool_count == 0 and rotation is None,
        "pool_count": pool_count,
        "restart_required": key_state.get("restart_required", False),
        "auto_rotated": rotation,
    })


@app.post("/api/sarvam/credits")
async def sarvam_credits_update(request: Request) -> JSONResponse:
    body = await request.json()
    try:
        remaining = float(body.get("remaining_credits"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "reason": "remaining_credits must be a number"}, status_code=400)
    current = _load_sarvam_credits()
    total = current.get("total_credits", 100)
    if body.get("total_credits") is not None:
        try:
            total = float(body["total_credits"])
        except (TypeError, ValueError):
            return JSONResponse({"ok": False, "reason": "total_credits must be a number"}, status_code=400)
    data = {"total_credits": total, "remaining_credits": remaining, "updated_at": _now_iso()}
    _save_sarvam_credits(data)
    return JSONResponse({"ok": True, **data})


@app.get("/api/sarvam/keys")
async def api_keys_info() -> JSONResponse:
    active = _read_active_api_key()
    pool = _load_api_key_pool()
    state = _load_key_state()
    return JSONResponse({
        "active_key_masked": _mask_key(active) if active else None,
        "pool": [_mask_key(k) for k in pool],
        "pool_count": len(pool),
        "restart_required": state.get("restart_required", False),
        "last_changed_at": state.get("last_changed_at"),
    })


@app.post("/api/sarvam/keys/replace")
async def api_keys_replace(request: Request) -> JSONResponse:
    body = await request.json()
    new_key = (body.get("api_key") or "").strip()
    if not new_key:
        return JSONResponse({"ok": False, "reason": "api_key is required"}, status_code=400)
    _apply_new_key(new_key)
    return JSONResponse({"ok": True, "masked_key": _mask_key(new_key), "restart_required": True})


@app.post("/api/sarvam/keys/bulk")
async def api_keys_bulk_add(request: Request) -> JSONResponse:
    body = await request.json()
    raw_keys = body.get("keys") or []
    if not isinstance(raw_keys, list):
        return JSONResponse({"ok": False, "reason": "keys must be a list"}, status_code=400)
    cleaned = [k.strip() for k in raw_keys if isinstance(k, str) and k.strip()]
    pool = _load_api_key_pool()
    active = _read_active_api_key()
    added = 0
    for k in cleaned:
        if k != active and k not in pool:
            pool.append(k)
            added += 1
    _save_api_key_pool(pool)
    return JSONResponse({"ok": True, "added": added, "skipped": len(cleaned) - added, "pool_count": len(pool)})


@app.delete("/api/sarvam/keys/pool")
async def api_keys_clear_pool() -> JSONResponse:
    _save_api_key_pool([])
    return JSONResponse({"ok": True, "pool_count": 0})


@app.post("/api/sarvam/keys/ack-restart")
async def api_keys_ack_restart() -> JSONResponse:
    state = _load_key_state()
    state["restart_required"] = False
    _save_key_state(state)
    return JSONResponse({"ok": True})


@app.get("/api/sarvam/usage")
async def sarvam_usage(period: str = Query("today")) -> JSONResponse:
    today = datetime.date.today()
    ranges = {"today": 0, "week": 6, "month": 29}
    days_back = ranges.get(period, 0)
    start = today - datetime.timedelta(days=days_back)
    events = _load_credit_range(start, today)
    calls  = _aggregate_calls(events)
    top10  = sorted(calls, key=lambda c: c["est_total_cost"], reverse=True)[:10]
    daily: dict[str, dict] = {}
    d = start
    while d <= today:
        daily[d.isoformat()] = _summarize_range(_load_credit_log(d))
        d += datetime.timedelta(days=1)
    return JSONResponse({"period": period, "start": start.isoformat(),
                         "end": today.isoformat(), "summary": _summarize_range(events),
                         "daily": daily, "top_calls": top10})


@app.get("/api/sarvam/usage/history")
async def usage_history(days: int = Query(30, ge=1, le=90)) -> JSONResponse:
    today = datetime.date.today()
    result = []
    for i in range(days - 1, -1, -1):
        d = today - datetime.timedelta(days=i)
        s = _summarize_range(_load_credit_log(d))
        result.append({"date": d.isoformat(), **s})
    return JSONResponse({"history": result})


@app.get("/api/logs/stream")
async def log_stream(request: Request,
                     categories: str = Query("all")) -> StreamingResponse:
    cat_set = set(c.strip() for c in categories.split(","))

    async def generator() -> AsyncIterator[str]:
        async for chunk in _log_event_stream(cat_set):
            if await request.is_disconnected():
                break
            yield chunk

    return StreamingResponse(generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/logs/tail")
async def log_tail(lines: int = Query(200, ge=1, le=2000)) -> JSONResponse:
    log_file = LOGS_LATEST / "ws_audio_receiver.log"
    result = []
    if log_file.exists():
        try:
            proc = subprocess.run(["tail", "-n", str(lines), str(log_file)],
                                  capture_output=True, text=True, timeout=5)
            for line in proc.stdout.splitlines():
                if line.strip():
                    cat, color = _categorize_line(line)
                    result.append({"line": line, "category": cat, "color": color})
        except Exception as exc:
            logger.warning("log_tail error: %s", exc)
    return JSONResponse({"lines": result})


# ---------------------------------------------------------------------------
# Outbound contacts
# ---------------------------------------------------------------------------

CALL_STATUSES   = ("not_called", "answered", "no_answer", "busy", "declined", "failed")
DISPOSITIONS    = ("not_classified", "interested", "site_visit_booked", "inquiry",
                   "in_doubt", "to_contact_later", "rejected")

# Single active campaign state (one campaign runs at a time)
_campaign: dict = {
    "running": False,
    "campaign_id": None,
    "total": 0,
    "completed": 0,
    "answered": 0,
    "no_answer": 0,
    "busy": 0,
    "declined": 0,
    "failed": 0,
    "skipped": 0,
    "current_phone": "",
    "current_name": "",
    "started_at": None,
    "stopped": False,
    "error": None,
}
_campaign_task: Any = None


def _load_outbound() -> list[dict]:
    if not OUTBOUND_FILE.exists():
        return []
    try:
        contacts = json.loads(OUTBOUND_FILE.read_text())
    except Exception:
        return []
    # Migrate legacy contacts that only have `status` field
    changed = False
    for c in contacts:
        if "call_status" not in c:
            old = c.pop("status", "not_called")
            c["call_status"] = "answered" if old == "called" else "not_called"
            c.setdefault("disposition", "not_classified")
            c.setdefault("call_count", 1 if old == "called" else 0)
            c.setdefault("last_called_at", None)
            c.setdefault("call_notes", "")
            c.setdefault("call_history", [])
            changed = True
    if changed:
        _save_outbound(contacts)
    return contacts


def _save_outbound(contacts: list[dict]) -> None:
    OUTBOUND_FILE.write_text(json.dumps(contacts, indent=2))


def _make_contact(phone: str, name: str = "", notes: str = "") -> dict:
    return {
        "id": str(int(time.time() * 1000)) + "_" + phone[-4:],
        "phone": phone,
        "name": name,
        "notes": notes,
        "added_at": datetime.datetime.utcnow().isoformat() + "Z",
        "call_status": "not_called",
        "disposition": "not_classified",
        "call_count": 0,
        "last_called_at": None,
        "call_notes": "",
        "call_history": [],
    }


# Common job-title words used to split a free-text "name designation" tail
# when a line has no tab/comma/multi-space delimiter to split on reliably.
_DESIGNATION_KEYWORDS = (
    "manager", "engineer", "director", "lead", "head", "officer", "executive",
    "founder", "co-founder", "ceo", "cto", "coo", "cfo", "vp", "president",
    "chairman", "analyst", "consultant", "developer", "designer", "specialist",
    "associate", "assistant", "coordinator", "supervisor", "administrator",
    "representative", "intern", "owner", "partner", "architect", "scientist",
    "recruiter", "accountant", "advisor", "agent", "broker",
)

# Modifier words that, when immediately preceding a keyword match, belong
# to the designation phrase too (e.g. "project manager", "senior engineer").
_DESIGNATION_MODIFIERS = (
    "project", "senior", "junior", "deputy", "regional", "national", "global",
    "chief", "vice", "principal", "general", "technical", "product", "sales",
    "marketing", "operations", "hr", "it", "finance", "business", "team",
    "group", "area", "branch", "country", "zonal", "vp",
)


def _excel_sci_to_digits(token: str) -> str | None:
    """Recover digits from an Excel scientific-notation cell like
    '9.18077E+11' (Excel renders long numeric phone numbers this way).
    Returns None if the token isn't scientific notation."""
    t = token.strip()
    if not re.match(r"^\d+(\.\d+)?[eE]\+?\d+$", t):
        return None
    try:
        return str(int(float(t)))
    except (ValueError, OverflowError):
        return None


def _normalize_phone_cell(cell: str) -> str:
    """Extract digits from a single cell, recovering Excel sci-notation first."""
    sci = _excel_sci_to_digits(cell)
    return sci if sci is not None else re.sub(r"\D", "", cell)


def _looks_like_header_row(cells: list[str]) -> bool:
    joined = " ".join(c.lower() for c in cells)
    return (any(k in joined for k in ("phone", "number", "mobile", "contact", "designation", "name"))
            and not any(7 <= len(_normalize_phone_cell(c)) <= 15 for c in cells))


def _split_free_text_line(line: str) -> list[str]:
    """Split a single TXT line into cells, trying the most reliable
    delimiter available: tab > comma > 2-or-more spaces. If none of those
    are present, return the whole line as one cell — _row_to_contact's
    single-cell path handles plain single-space free text by tokenizing
    on words itself, anchored on the phone-shaped word."""
    if "\t" in line:
        return [c.strip() for c in line.split("\t")]
    if "," in line:
        return [c.strip() for c in line.split(",")]
    if re.search(r" {2,}", line):
        return [c.strip() for c in re.split(r" {2,}", line)]
    return [line]


def _row_to_contact(cells: list[str]) -> dict | None:
    """Turn a row of cells into {"phone","name","notes"}, or None if no
    phone-shaped value is found.

    Two cases:
    - A single cell (plain space-separated free text, no reliable
      delimiter): tokenize into words, the phone-shaped WORD anchors the
      split, remaining words become name + designation/notes (split on a
      common job-title keyword if one appears).
    - Multiple cells (tab/comma/2+space delimited, i.e. real columns):
      whichever cell normalizes to 7-15 digits is the phone, the next
      non-empty cell is the name, everything else is joined into notes."""
    cells = [c for c in cells if c is not None and c.strip()]
    if not cells:
        return None

    if len(cells) == 1:
        words = cells[0].split()
        phone_i = next((i for i, w in enumerate(words)
                         if 7 <= len(_normalize_phone_cell(w)) <= 15), None)
        if phone_i is None:
            return None
        phone_digits = _normalize_phone_cell(words[phone_i])
        remaining = words[:phone_i] + words[phone_i + 1:]
        split_at = next((i for i, w in enumerate(remaining)
                          if w.strip(".,").lower() in _DESIGNATION_KEYWORDS), None)
        if split_at is not None:
            while (split_at > 0
                   and remaining[split_at - 1].strip(".,").lower() in _DESIGNATION_MODIFIERS):
                split_at -= 1
            name = " ".join(remaining[:split_at]).strip()
            notes = " ".join(remaining[split_at:]).strip()
        else:
            name, notes = " ".join(remaining).strip(), ""
        return {"phone": "+" + phone_digits, "name": name, "notes": notes}

    phone_idx = None
    phone_digits = ""
    for i, c in enumerate(cells):
        digits = _normalize_phone_cell(c)
        if 7 <= len(digits) <= 15:
            phone_idx = i
            phone_digits = digits
            break
    if phone_idx is None:
        return None

    rest = [c.strip() for i, c in enumerate(cells) if i != phone_idx]
    name = rest[0] if rest else ""
    notes = " | ".join(rest[1:]) if len(rest) > 1 else ""
    return {"phone": "+" + phone_digits, "name": name, "notes": notes}


def _parse_uploaded_file(filename: str, raw: bytes) -> tuple[list[dict], int]:
    """Return (list_of_{"phone","name","notes"}, parse_error_count)."""
    rows: list[dict] = []
    errors = 0
    fname = filename.lower()

    if fname.endswith(".xlsx") or fname.endswith(".xls"):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    cells = [str(c).strip() if c is not None else "" for c in row]
                    if not any(cells) or _looks_like_header_row(cells):
                        continue
                    contact = _row_to_contact(cells)
                    if contact:
                        rows.append(contact)
                    else:
                        errors += 1
        except Exception as e:
            logger.warning("xlsx parse error: %r", e)
            errors += 1
    elif fname.endswith(".csv"):
        try:
            text = raw.decode("utf-8-sig", errors="replace")
        except Exception:
            text = ""
        try:
            dialect = csv.Sniffer().sniff(text.splitlines()[0]) if text.strip() else csv.excel
        except csv.Error:
            dialect = csv.excel
        for cells in csv.reader(io.StringIO(text), dialect=dialect):
            cells = [c.strip() for c in cells]
            if not any(cells) or _looks_like_header_row(cells):
                continue
            contact = _row_to_contact(cells)
            if contact:
                rows.append(contact)
            else:
                errors += 1
    else:
        # Plain TXT — one contact per non-empty line.
        try:
            text = raw.decode("utf-8-sig", errors="replace")
        except Exception:
            text = ""
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            cells = _split_free_text_line(line)
            if _looks_like_header_row(cells):
                continue
            contact = _row_to_contact(cells)
            if contact:
                rows.append(contact)
            else:
                errors += 1

    return rows, errors


@app.get("/api/outbound/contacts")
async def outbound_list(
    call_status: str = Query(None),
    disposition: str = Query(None),
    q: str = Query(None),
) -> JSONResponse:
    contacts = _load_outbound()
    if call_status and call_status != "all":
        contacts = [c for c in contacts if c.get("call_status") == call_status]
    if disposition and disposition != "all":
        contacts = [c for c in contacts if c.get("disposition") == disposition]
    if q:
        ql = q.lower()
        contacts = [c for c in contacts if ql in c.get("phone", "") or ql in c.get("name", "").lower()]
    return JSONResponse({"contacts": contacts, "total": len(contacts)})


@app.post("/api/outbound/contacts")
async def outbound_add(request: Request) -> JSONResponse:
    body = await request.json()
    phone = re.sub(r"[^\d+]", "", (body.get("phone") or "").strip())
    if not phone or len(re.sub(r"\D", "", phone)) < 7:
        return JSONResponse({"ok": False, "reason": "phone required (min 7 digits)"}, status_code=400)
    contacts = _load_outbound()
    existing_digits = {re.sub(r"\D", "", c["phone"]) for c in contacts}
    if re.sub(r"\D", "", phone) in existing_digits:
        return JSONResponse({"ok": False, "reason": "number already exists"}, status_code=409)
    contact = _make_contact(phone, (body.get("name") or "").strip(), (body.get("notes") or "").strip())
    contacts.append(contact)
    _save_outbound(contacts)
    return JSONResponse({"ok": True, "contact": contact})


@app.post("/api/outbound/contacts/upload")
async def outbound_upload(request: Request) -> JSONResponse:
    """Accept multipart file upload (CSV / TXT / XLSX) and bulk-add phone numbers."""
    from fastapi import UploadFile
    import io as _io

    content_type = request.headers.get("content-type", "")
    if "multipart" not in content_type:
        return JSONResponse({"ok": False, "reason": "multipart/form-data required"}, status_code=400)

    form = await request.form()
    file_field = form.get("file")
    if not file_field:
        return JSONResponse({"ok": False, "reason": "no file field"}, status_code=400)

    filename = getattr(file_field, "filename", "upload.txt") or "upload.txt"
    raw = await file_field.read()

    rows, parse_errors = _parse_uploaded_file(filename, raw)
    if not rows:
        return JSONResponse({"ok": False, "reason": f"No valid phone numbers found in file. parse_errors={parse_errors}"})

    contacts = _load_outbound()
    existing_digits = {re.sub(r"\D", "", c["phone"]) for c in contacts}
    added, dupes = 0, 0
    for row in rows:
        digits = re.sub(r"\D", "", row["phone"])
        if digits in existing_digits:
            dupes += 1
            continue
        contacts.append(_make_contact(row["phone"], row.get("name", ""), row.get("notes", "")))
        existing_digits.add(digits)
        added += 1
        time.sleep(0.001)  # ensure unique IDs

    _save_outbound(contacts)
    return JSONResponse({"ok": True, "added": added, "duplicates": dupes,
                          "parse_errors": parse_errors, "total_in_file": len(rows)})


@app.delete("/api/outbound/contacts/all")
async def outbound_delete_all() -> JSONResponse:
    _save_outbound([])
    return JSONResponse({"ok": True})


@app.delete("/api/outbound/contacts/{contact_id}")
async def outbound_delete(contact_id: str) -> JSONResponse:
    contacts = _load_outbound()
    before = len(contacts)
    contacts = [c for c in contacts if c["id"] != contact_id]
    if len(contacts) == before:
        return JSONResponse({"ok": False, "reason": "not found"}, status_code=404)
    _save_outbound(contacts)
    return JSONResponse({"ok": True})


@app.patch("/api/outbound/contacts/{contact_id}")
async def outbound_update(contact_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    contacts = _load_outbound()
    for c in contacts:
        if c["id"] == contact_id:
            for field in ("name", "notes", "call_status", "disposition", "call_notes"):
                if field in body:
                    c[field] = (body[field] or "").strip() if isinstance(body[field], str) else body[field]
            break
    else:
        return JSONResponse({"ok": False, "reason": "not found"}, status_code=404)
    _save_outbound(contacts)
    return JSONResponse({"ok": True})


def _load_followups_overrides() -> dict[str, dict]:
    if not FOLLOWUPS_FILE.exists():
        return {}
    try:
        return json.loads(FOLLOWUPS_FILE.read_text())
    except Exception:
        return {}


def _save_followups_overrides(data: dict[str, dict]) -> None:
    FOLLOWUPS_FILE.write_text(json.dumps(data, indent=2))


def _load_manual_followups() -> list[dict]:
    if not MANUAL_FOLLOWUPS_FILE.exists():
        return []
    try:
        return json.loads(MANUAL_FOLLOWUPS_FILE.read_text())
    except Exception:
        return []


def _save_manual_followups(entries: list[dict]) -> None:
    MANUAL_FOLLOWUPS_FILE.write_text(json.dumps(entries, indent=2))


@app.post("/api/followups/manual")
async def manual_followup_create(request: Request) -> JSONResponse:
    body = await request.json()
    phone = (body.get("contact_phone") or "").strip()
    if not phone:
        return JSONResponse({"ok": False, "reason": "contact_phone is required"}, status_code=400)
    import uuid as _uuid
    entry = {
        "id": _uuid.uuid4().hex[:12],
        "contact_name": (body.get("contact_name") or "").strip(),
        "contact_phone": phone,
        "reason": (body.get("reason") or "").strip(),
        "follow_up_date": body.get("follow_up_date") or None,
        "priority": body.get("priority") or "medium",
        "call_id": body.get("call_id") or None,
        "status": "pending",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    entries = _load_manual_followups()
    entries.append(entry)
    _save_manual_followups(entries)
    return JSONResponse({"ok": True, "id": entry["id"], "entry": entry})


@app.patch("/api/followups/manual/{entry_id}")
async def manual_followup_update(entry_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    entries = _load_manual_followups()
    for e in entries:
        if e["id"] == entry_id:
            for field in ("contact_name", "contact_phone", "reason", "follow_up_date", "priority", "status"):
                if field in body:
                    e[field] = body[field]
            e["updated_at"] = _now_iso()
            _save_manual_followups(entries)
            return JSONResponse({"ok": True, "entry": e})
    return JSONResponse({"ok": False, "reason": "not found"}, status_code=404)


@app.delete("/api/followups/manual/{entry_id}")
async def manual_followup_delete(entry_id: str) -> JSONResponse:
    entries = _load_manual_followups()
    new_entries = [e for e in entries if e["id"] != entry_id]
    if len(new_entries) == len(entries):
        return JSONResponse({"ok": False, "reason": "not found"}, status_code=404)
    _save_manual_followups(new_entries)
    return JSONResponse({"ok": True})


@app.get("/api/followups")
async def followups_list(status: str = Query("all")) -> JSONResponse:
    """Merges two real sources of follow-up need:
    1. Outbound contacts whose disposition is to_contact_later/in_doubt
       (set manually, or auto-set after a no_answer/declined call).
    2. Calls that have gone through on-demand AI Call Analysis and were
       flagged follow_up_needed — covers both inbound and outbound calls,
       but only ones a user has chosen to analyze (analysis is on-demand).
    A user-set completion status overrides the default "pending" via
    follow_ups.json, keyed the same way as the entry id below.
    """
    overrides = _load_followups_overrides()
    entries: list[dict] = []

    for c in _load_outbound():
        if c.get("disposition") not in ("to_contact_later", "in_doubt"):
            continue
        key = f"contact:{c['id']}"
        entries.append({
            "id": key, "source": "outbound",
            "contact_name": c.get("name") or "", "contact_phone": c.get("phone", ""),
            "follow_up_required": True,
            "reason": f"Outbound disposition: {c.get('disposition')}",
            "follow_up_date": None,
            "priority": "medium",
            "status": overrides.get(key, {}).get("status", "pending"),
            "updated_at": c.get("last_called_at"),
        })

    for folder, analysis in _scan_analyses().items():
        if not analysis.get("follow_up_needed"):
            continue
        key = f"call:{folder}"
        caller = folder.split("_")[0]
        entries.append({
            "id": key, "source": "ai_analysis",
            "contact_name": "", "contact_phone": caller if caller.isdigit() else "",
            "follow_up_required": True,
            "reason": analysis.get("follow_up_reason") or "",
            "follow_up_date": analysis.get("recommended_follow_up_date"),
            "priority": analysis.get("follow_up_priority") or "medium",
            "status": overrides.get(key, {}).get("status", "pending"),
            "updated_at": analysis.get("_analyzed_at"),
            "folder": folder,
        })

    for m in _load_manual_followups():
        key = f"manual:{m['id']}"
        ov_status = overrides.get(key, {}).get("status")
        entries.append({
            "id": key, "source": "manual",
            "contact_name": m.get("contact_name") or "",
            "contact_phone": m.get("contact_phone") or "",
            "follow_up_required": True,
            "reason": m.get("reason") or "",
            "follow_up_date": m.get("follow_up_date"),
            "priority": m.get("priority") or "medium",
            "status": ov_status or m.get("status") or "pending",
            "updated_at": m.get("updated_at"),
            "call_id": m.get("call_id"),
            "_manual_id": m["id"],
        })

    entries = [e for e in entries if e["status"] != "dismissed"]
    if status != "all":
        entries = [e for e in entries if e["status"] == status]
    entries.sort(key=lambda e: e.get("updated_at") or "", reverse=True)
    return JSONResponse({"followups": entries, "total": len(entries)})


@app.patch("/api/followups/{entry_id}")
async def followups_update(entry_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    new_status = body.get("status")
    if new_status not in ("pending", "completed", "missed", "dismissed"):
        return JSONResponse({"ok": False, "reason": "status must be pending/completed/missed/dismissed"}, status_code=400)
    if entry_id.startswith("manual:"):
        manual_id = entry_id.split(":", 1)[1]
        entries = _load_manual_followups()
        for e in entries:
            if e["id"] == manual_id:
                e["status"] = new_status
                e["updated_at"] = _now_iso()
                _save_manual_followups(entries)
                return JSONResponse({"ok": True})
        return JSONResponse({"ok": False, "reason": "manual entry not found"}, status_code=404)
    overrides = _load_followups_overrides()
    overrides[entry_id] = {"status": new_status, "updated_at": _now_iso()}
    _save_followups_overrides(overrides)
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Follow-Up Automation — schedule an outbound follow-up call for an entry
# already flagged in the Follow-Up Tracker, carrying over what's known about
# the past call (disposition / AI analysis) plus a user-editable note and
# call aim. Dialing reuses the exact same FreeSWITCH originate path as manual
# Outbound Calls (_fs_originate_cmd/_parse_fs_result below) — the live
# conversation engine is untouched, so the call content itself does not
# change; this only automates *placing* the call, not what the agent says.
# ---------------------------------------------------------------------------

def _load_automation_store() -> list[dict]:
    if not FOLLOWUP_AUTOMATION_FILE.exists():
        return []
    try:
        return json.loads(FOLLOWUP_AUTOMATION_FILE.read_text())
    except Exception:
        return []


def _save_automation_store(entries: list[dict]) -> None:
    FOLLOWUP_AUTOMATION_FILE.write_text(json.dumps(entries, indent=2))


def _build_followup_context(followup_id: str) -> dict | None:
    """Pulls together what's already known about why this follow-up exists,
    from whichever real source produced it — never invents call history."""
    if followup_id.startswith("manual:"):
        manual_id = followup_id.split(":", 1)[1]
        entry = next((m for m in _load_manual_followups() if m["id"] == manual_id), None)
        if not entry:
            return None
        notes_parts = []
        if entry.get("reason"):
            notes_parts.append(f"Reason: {entry['reason']}")
        if entry.get("call_id"):
            notes_parts.append(f"Referenced call: {entry['call_id']}")
        if entry.get("follow_up_date"):
            notes_parts.append(f"Target follow-up date: {entry['follow_up_date']}")
        return {
            "contact_name": entry.get("contact_name") or "",
            "contact_phone": entry.get("contact_phone") or "",
            "past_call_context": " ".join(notes_parts) if notes_parts else "Manually added follow-up from call history.",
            "suggested_call_aim": "Follow up with the contact as noted.",
            "linked_contact_id": None,
        }
    if followup_id.startswith("contact:"):
        contact_id = followup_id.split(":", 1)[1]
        contact = next((c for c in _load_outbound() if c["id"] == contact_id), None)
        if not contact:
            return None
        disp = contact.get("disposition", "not_classified")
        aim_by_disp = {
            "to_contact_later": "Re-engage and check current interest/availability.",
            "in_doubt": "Resolve remaining doubts and confirm next step.",
        }
        notes_parts = [f"Outbound disposition from last call: {disp}."]
        if contact.get("notes"):
            notes_parts.append(f"Contact notes: {contact['notes']}")
        if contact.get("call_notes"):
            notes_parts.append(f"Call notes: {contact['call_notes']}")
        return {
            "contact_name": contact.get("name") or "",
            "contact_phone": contact.get("phone", ""),
            "past_call_context": " ".join(notes_parts),
            "suggested_call_aim": aim_by_disp.get(disp, "Re-engage the lead and progress toward a decision."),
            "linked_contact_id": contact_id,
        }
    if followup_id.startswith("call:"):
        folder = followup_id.split(":", 1)[1]
        analysis = _scan_analyses().get(folder)
        if not analysis:
            return None
        caller = folder.split("_")[0]
        notes_parts = [f"Call summary: {analysis.get('summary', '')}",
                       f"Customer intent: {analysis.get('customer_intent', '')}",
                       f"Why follow-up was flagged: {analysis.get('follow_up_reason', '')}"]
        return {
            "contact_name": "", "contact_phone": caller if caller.isdigit() else "",
            "past_call_context": " ".join(p for p in notes_parts if p.strip().split(":")[-1].strip()),
            "suggested_call_aim": analysis.get("suggested_next_action") or "Follow up per AI Call Analysis recommendation.",
            "linked_contact_id": None,
        }
    return None


@app.get("/api/followup-automation")
async def automation_list() -> JSONResponse:
    entries = _load_automation_store()
    entries.sort(key=lambda e: e.get("scheduled_time") or "", reverse=False)
    return JSONResponse({"automations": entries, "total": len(entries)})


@app.post("/api/followup-automation")
async def automation_create(request: Request) -> JSONResponse:
    body = await request.json()
    followup_id = body.get("followup_id")
    if not followup_id:
        return JSONResponse({"ok": False, "reason": "followup_id is required"}, status_code=400)
    ctx = _build_followup_context(followup_id)
    if ctx is None:
        return JSONResponse({"ok": False, "reason": "could not resolve followup_id to a known contact/call"}, status_code=404)

    entries = _load_automation_store()
    if any(e["source_id"] == followup_id and e["status"] == "scheduled" for e in entries):
        return JSONResponse({"ok": False, "reason": "already scheduled for this follow-up"}, status_code=409)

    import uuid as _uuid
    entry = {
        "id": _uuid.uuid4().hex[:10],
        "source_id": followup_id,
        "contact_name": ctx["contact_name"], "contact_phone": ctx["contact_phone"],
        "past_call_context": ctx["past_call_context"],
        "call_aim": body.get("call_aim") or ctx["suggested_call_aim"],
        "script_notes": body.get("script_notes", ""),
        "scheduled_time": body.get("scheduled_time"),
        "linked_contact_id": ctx["linked_contact_id"],
        "status": "scheduled",
        "created_at": _now_iso(),
        "called_at": None,
        "call_outcome": None,
    }
    entries.append(entry)
    _save_automation_store(entries)
    return JSONResponse({"ok": True, "automation": entry})


@app.patch("/api/followup-automation/{automation_id}")
async def automation_update(automation_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    entries = _load_automation_store()
    entry = next((e for e in entries if e["id"] == automation_id), None)
    if not entry:
        return JSONResponse({"ok": False, "reason": "not found"}, status_code=404)
    if entry["status"] in ("calling", "called"):
        return JSONResponse({"ok": False, "reason": "already called or in progress — cannot edit"}, status_code=409)
    for field in ("scheduled_time", "script_notes", "call_aim"):
        if field in body:
            entry[field] = body[field]
    if body.get("status") == "cancelled":
        entry["status"] = "cancelled"
    _save_automation_store(entries)
    return JSONResponse({"ok": True, "automation": entry})


@app.delete("/api/followup-automation/{automation_id}")
async def automation_delete(automation_id: str) -> JSONResponse:
    entries = _load_automation_store()
    before = len(entries)
    entries = [e for e in entries if e["id"] != automation_id]
    if len(entries) == before:
        return JSONResponse({"ok": False, "reason": "not found"}, status_code=404)
    _save_automation_store(entries)
    return JSONResponse({"ok": True})


async def _dial_automation_entry(automation_id: str) -> None:
    """Places the actual outbound call for one automation entry, reusing the
    same FreeSWITCH originate path as manual Outbound Calls. Does not pass
    past_call_context/script_notes/call_aim into the call in any way — those
    exist for the dashboard operator's reference, not the live agent prompt."""
    entries = _load_automation_store()
    entry = next((e for e in entries if e["id"] == automation_id), None)
    if not entry or entry["status"] != "scheduled":
        return
    phone = re.sub(r"[^\d]", "", entry.get("contact_phone", ""))
    if len(phone) < 7:
        entry["status"] = "failed"
        entry["call_outcome"] = "invalid or missing phone number"
        entry["called_at"] = _now_iso()
        _save_automation_store(entries)
        return

    entry["status"] = "calling"
    _save_automation_store(entries)
    _record_dial(phone, entry["id"], entry.get("contact_name", ""))

    try:
        proc = await asyncio.create_subprocess_exec(
            "fs_cli", "-x", _fs_originate_cmd(phone),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=55)
        cs = _parse_fs_result(stdout.decode(), stderr.decode())
    except Exception as exc:
        logger.error("automation dial %s error: %r", phone, exc)
        cs = "failed"

    entries = _load_automation_store()
    entry = next((e for e in entries if e["id"] == automation_id), None)
    if entry:
        entry["status"] = "called"
        entry["call_outcome"] = cs
        entry["called_at"] = _now_iso()
        if entry.get("linked_contact_id"):
            _update_contact_after_call(entry["linked_contact_id"], cs)
        _save_automation_store(entries)
    logger.info("FOLLOWUP_AUTOMATION %s -> %s (%s)", automation_id, phone, cs)


@app.post("/api/followup-automation/{automation_id}/call-now")
async def automation_call_now(automation_id: str) -> JSONResponse:
    entries = _load_automation_store()
    entry = next((e for e in entries if e["id"] == automation_id), None)
    if not entry:
        return JSONResponse({"ok": False, "reason": "not found"}, status_code=404)
    if entry["status"] != "scheduled":
        return JSONResponse({"ok": False, "reason": f"entry is {entry['status']}, not scheduled"}, status_code=409)
    asyncio.create_task(_dial_automation_entry(automation_id))
    return JSONResponse({"ok": True, "msg": "Calling now…"})


async def _automation_scheduler_loop() -> None:
    """Background loop: every AUTOMATION_POLL_SECONDS, dial any entry whose
    scheduled_time has passed. Entries with no scheduled_time set are left
    alone — Call Now is the only way to trigger them."""
    while True:
        try:
            now = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
            for entry in _load_automation_store():
                if entry["status"] != "scheduled" or not entry.get("scheduled_time"):
                    continue
                try:
                    due = datetime.datetime.fromisoformat(entry["scheduled_time"].replace("Z", "+00:00"))
                except Exception:
                    continue
                if due <= now:
                    await _dial_automation_entry(entry["id"])
        except Exception as exc:
            logger.error("automation scheduler tick failed: %r", exc)
        await asyncio.sleep(AUTOMATION_POLL_SECONDS)


@app.on_event("startup")
async def _start_automation_scheduler() -> None:
    asyncio.create_task(_automation_scheduler_loop())


@app.get("/api/outbound/stats")
async def outbound_stats() -> JSONResponse:
    contacts = _load_outbound()
    stats: dict = {"total": len(contacts)}
    for s in CALL_STATUSES:
        stats[s] = sum(1 for c in contacts if c.get("call_status") == s)
    disp: dict = {}
    for d in DISPOSITIONS:
        disp[d] = sum(1 for c in contacts if c.get("disposition") == d)
    return JSONResponse({"stats": stats, "dispositions": disp})


# ---------------------------------------------------------------------------
# Outbound dialing + campaign engine
# ---------------------------------------------------------------------------

def _fs_originate_cmd(phone: str) -> str:
    return (
        f"originate {{"
        f"origination_caller_id_number={OUTBOUND_CALLER_ID},"
        f"originate_timeout=45,"
        f"ignore_early_media=true"
        f"}}sofia/external/{phone}@{OUTBOUND_GATEWAY} "
        f"{OUTBOUND_DP_EXT} XML {OUTBOUND_DP_CTX}"
    )


def _parse_fs_result(stdout: str, stderr: str) -> str:
    """Map fs_cli originate output to a call_status string."""
    out = stdout.strip() + " " + stderr.strip()
    if "+OK" in out:
        return "answered"
    if "NO_ANSWER" in out or "ALLOTTED_TIMEOUT" in out:
        return "no_answer"
    if "USER_BUSY" in out or "BUSY" in out:
        return "busy"
    if any(x in out for x in ("NORMAL_CLEARING", "CALL_REJECTED", "USER_NOT_REGISTERED",
                               "UNALLOCATED", "ORIGINATOR_CANCEL", "SUBSCRIBER_ABSENT")):
        return "declined"
    return "failed"


def _update_contact_after_call(contact_id: str, call_status: str) -> None:
    contacts = _load_outbound()
    now = datetime.datetime.utcnow().isoformat() + "Z"
    for c in contacts:
        if c["id"] != contact_id:
            continue
        c["call_status"] = call_status
        c["last_called_at"] = now
        c["call_count"] = c.get("call_count", 0) + 1
        hist = c.get("call_history", [])
        hist.append({"ts": now, "call_status": call_status})
        c["call_history"] = hist[-20:]
        # Auto-disposition only if user hasn't already set one
        if c.get("disposition") in ("not_classified", None, ""):
            if call_status == "declined":
                c["disposition"] = "rejected"
            elif call_status == "no_answer":
                c["disposition"] = "to_contact_later"
        break
    _save_outbound(contacts)


@app.post("/api/outbound/dial/{contact_id}")
async def outbound_dial(contact_id: str) -> JSONResponse:
    """Trigger a single outbound call. Returns immediately; call runs async."""
    contacts = _load_outbound()
    contact = next((c for c in contacts if c["id"] == contact_id), None)
    if not contact:
        return JSONResponse({"ok": False, "reason": "contact not found"}, status_code=404)

    phone = re.sub(r"[^\d]", "", contact.get("phone", ""))
    if len(phone) < 7:
        return JSONResponse({"ok": False, "reason": "invalid phone number"})

    _record_dial(phone, contact_id, contact.get("name", ""))

    async def _do_dial():
        try:
            proc = await asyncio.create_subprocess_exec(
                "fs_cli", "-x", _fs_originate_cmd(phone),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=55)
            cs = _parse_fs_result(stdout.decode(), stderr.decode())
        except Exception as e:
            logger.error("dial %s error: %r", phone, e)
            cs = "failed"
        _update_contact_after_call(contact_id, cs)
        logger.info("DIAL %s → %s", phone, cs)

    asyncio.create_task(_do_dial())
    return JSONResponse({"ok": True, "phone": phone, "status": "dialing"})


# ── Campaign engine ────────────────────────────────────────────────────────

@app.get("/api/outbound/campaign/status")
async def campaign_status() -> JSONResponse:
    return JSONResponse(dict(_campaign))


@app.post("/api/outbound/campaign/start")
async def campaign_start(request: Request) -> JSONResponse:
    global _campaign, _campaign_task
    if _campaign.get("running"):
        return JSONResponse({"ok": False, "reason": "Campaign already running"}, status_code=409)

    body = await request.json()
    filter_cs   = body.get("call_status_filter", "not_called")   # which contacts to call
    delay_secs  = float(body.get("delay_seconds", 4.0))
    max_calls   = int(body.get("max_calls", 0)) or None

    contacts = _load_outbound()
    if filter_cs == "all":
        to_call = contacts
    else:
        to_call = [c for c in contacts if c.get("call_status") == filter_cs]
    if max_calls:
        to_call = to_call[:max_calls]

    if not to_call:
        return JSONResponse({"ok": False, "reason": "No contacts match the filter"})

    import uuid as _uuid
    cid = _uuid.uuid4().hex[:8]
    _campaign.update({
        "running": True, "campaign_id": cid,
        "total": len(to_call), "completed": 0,
        "answered": 0, "no_answer": 0, "busy": 0,
        "declined": 0, "failed": 0, "skipped": 0,
        "current_phone": "", "current_name": "",
        "started_at": datetime.datetime.utcnow().isoformat() + "Z",
        "stopped": False, "error": None,
    })

    async def _run():
        global _campaign
        try:
            for contact in to_call:
                if _campaign.get("stopped"):
                    break
                contact_id = contact["id"]
                phone = re.sub(r"[^\d]", "", contact.get("phone", ""))
                if len(phone) < 7:
                    _campaign["skipped"] += 1
                    _campaign["completed"] += 1
                    continue

                _campaign["current_phone"] = phone
                _campaign["current_name"] = contact.get("name", "")
                logger.info("CAMPAIGN [%s] dialing %s (%s/%s)",
                            cid, phone, _campaign["completed"] + 1, _campaign["total"])
                _record_dial(phone, contact_id, contact.get("name", ""))
                try:
                    proc = await asyncio.create_subprocess_exec(
                        "fs_cli", "-x", _fs_originate_cmd(phone),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=55)
                    cs = _parse_fs_result(stdout.decode(), stderr.decode())
                except asyncio.TimeoutError:
                    cs = "no_answer"
                except Exception as e:
                    logger.error("campaign dial %s error: %r", phone, e)
                    cs = "failed"

                _update_contact_after_call(contact_id, cs)
                key = cs if cs in _campaign else "failed"
                _campaign[key] = _campaign.get(key, 0) + 1
                _campaign["completed"] += 1

                if not _campaign.get("stopped") and delay_secs > 0:
                    await asyncio.sleep(delay_secs)
        except Exception as e:
            logger.exception("campaign crashed: %r", e)
            _campaign["error"] = str(e)
        finally:
            _campaign["running"] = False
            _campaign["current_phone"] = ""
            _campaign["current_name"] = ""
            logger.info("CAMPAIGN [%s] done: %s", cid, {k: _campaign[k] for k in
                        ("total","completed","answered","no_answer","busy","declined","failed","skipped")})

    _campaign_task = asyncio.create_task(_run())
    return JSONResponse({"ok": True, "campaign_id": cid, "total": len(to_call)})


@app.post("/api/outbound/campaign/stop")
async def campaign_stop() -> JSONResponse:
    _campaign["stopped"] = True
    return JSONResponse({"ok": True, "message": "Stop signal sent"})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8080,
                log_level="info", workers=1)
