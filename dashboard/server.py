#!/usr/bin/env python3
"""
LesCall Dashboard Server

Standalone operational dashboard for the Pramukh Group voice AI platform.
- Read-only observer of logs, credit_log, and recordings
- Runtime controller: start / stop / restart the calling agent via API
- SSE streams for live logs and command output

Port: 8080  (nginx proxies port 80 → 8080)
Start: bash /home/angelo/lescall/dashboard/start.sh --bg
"""
from __future__ import annotations

import asyncio
import datetime
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
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
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
            calls.append({
                "call_id": cid,
                "start_time": start_ts or ev.get("start_time", ""),
                "end_time": ev.get("ts", ""),
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
async def overview() -> JSONResponse:
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

    return JSONResponse({
        "today":        _summarize_range(today_events),
        "week":         _summarize_range(week_events),
        "month":        _summarize_range(month_events),
        "active_calls": len(_get_active_calls_from_log(today_events)),
        "runtime_status": _get_runtime_health()["overall"],
        "hourly_calls": [{"hour": h, "count": hourly[h]} for h in range(24)],
    })


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


@app.get("/api/calls/history")
async def call_history(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    search: str = Query(""),
    days: int = Query(7, ge=1, le=90),
) -> JSONResponse:
    end_date   = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=days - 1)
    events = _load_credit_range(start_date, end_date)
    calls  = _aggregate_calls(events)
    if search:
        sl = search.lower()
        calls = [c for c in calls if sl in c.get("call_id", "").lower()]
    total = len(calls)
    page_calls = calls[(page - 1) * limit: page * limit]
    return JSONResponse({"calls": page_calls, "total": total, "page": page,
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


def _parse_uploaded_file(filename: str, raw: bytes) -> tuple[list[str], int]:
    """Return (list_of_phone_strings, parse_error_count)."""
    phones: list[str] = []
    errors = 0
    fname = filename.lower()

    if fname.endswith(".xlsx") or fname.endswith(".xls"):
        try:
            import openpyxl
            import io
            wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    for cell in row:
                        if cell is None:
                            continue
                        val = re.sub(r"[^\d+]", "", str(cell))
                        if 7 <= len(re.sub(r"\D", "", val)) <= 15:
                            phones.append(val)
        except Exception as e:
            logger.warning("xlsx parse error: %r", e)
            errors += 1
    else:
        # CSV or TXT — decode and split by common delimiters
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception:
            text = ""
        # Treat each token that looks like a phone number as a candidate
        for token in re.split(r"[\r\n,;\t|]+", text):
            token = token.strip()
            if not token:
                continue
            # Strip common non-phone chars but keep +, -, space temporarily
            candidate = re.sub(r"[^\d+\- ]", "", token)
            digits = re.sub(r"\D", "", candidate)
            if 7 <= len(digits) <= 15:
                phones.append("+" + digits if not candidate.startswith("+") else candidate.replace(" ", "").replace("-", ""))
            elif token:
                errors += 1

    return phones, errors


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

    phones, parse_errors = _parse_uploaded_file(filename, raw)
    if not phones:
        return JSONResponse({"ok": False, "reason": f"No valid phone numbers found in file. parse_errors={parse_errors}"})

    contacts = _load_outbound()
    existing_digits = {re.sub(r"\D", "", c["phone"]) for c in contacts}
    added, dupes = 0, 0
    for ph in phones:
        digits = re.sub(r"\D", "", ph)
        if digits in existing_digits:
            dupes += 1
            continue
        contacts.append(_make_contact(ph))
        existing_digits.add(digits)
        added += 1
        time.sleep(0.001)  # ensure unique IDs

    _save_outbound(contacts)
    return JSONResponse({"ok": True, "added": added, "duplicates": dupes,
                          "parse_errors": parse_errors, "total_in_file": len(phones)})


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
