"""
Lescall Live Dashboard — port 8080
Serves call history (recordings/), live log stream, and runtime status.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

RECORDINGS_DIR = Path("/home/angelo/lescall/recordings")
RUNTIME_DIR    = Path("/home/angelo/lescall/phase14_runtime")
DASHBOARD_DIR  = Path(__file__).parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Lescall Dashboard")


# ── helpers ─────────────────────────────────────────────────────────────────

def _latest_log_dir() -> Path | None:
    latest = RUNTIME_DIR / "logs" / "latest"
    if latest.is_symlink() or latest.is_dir():
        return latest.resolve()
    return None


def _parse_metadata(folder: Path) -> dict:
    meta_path = folder / "metadata.json"
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text())
    except Exception:
        return {}


def _parse_transcript(folder: Path) -> list[dict]:
    """Return list of {ts, speaker, text} from transcript.txt."""
    path = folder / "transcript.txt"
    if not path.exists():
        return []
    lines = []
    ts_re = re.compile(r"^\[(\d+:\d+\.\d+)\]")
    current_ts = ""
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = ts_re.match(raw)
        if m:
            current_ts = m.group(1)
            rest = raw[m.end():].strip()
            if rest:
                _push(lines, current_ts, rest)
        elif raw.strip():
            _push(lines, current_ts, raw.strip())
    return lines


def _push(lines: list, ts: str, text: str) -> None:
    if ":" in text:
        speaker, _, body = text.partition(":")
        lines.append({"ts": ts, "speaker": speaker.strip(), "text": body.strip()})
    else:
        lines.append({"ts": ts, "speaker": "?", "text": text})


def _list_calls() -> list[dict]:
    if not RECORDINGS_DIR.exists():
        return []
    calls = []
    for folder in sorted(RECORDINGS_DIR.iterdir(), reverse=True):
        if not folder.is_dir():
            continue
        meta = _parse_metadata(folder)
        if not meta:
            continue
        calls.append({
            "folder":   folder.name,
            "call_id":  meta.get("call_id", folder.name),
            "start":    meta.get("start_time", ""),
            "end":      meta.get("end_time", ""),
            "duration": meta.get("duration_seconds", 0),
            "language": meta.get("language_detected", "?"),
            "stt":      meta.get("stt_provider", "?"),
            "fallback": meta.get("fallback_used", False),
            "turns":    meta.get("transcript_count", 0),
        })
    return calls


def _overview_stats(calls: list[dict]) -> dict:
    total = len(calls)
    if not total:
        return {"total": 0, "avg_duration": 0, "languages": {}, "today": 0}
    today = datetime.now(timezone.utc).date().isoformat()
    today_count = sum(1 for c in calls if c["start"].startswith(today))
    durations = [c["duration"] for c in calls if c["duration"]]
    avg_dur = round(sum(durations) / len(durations), 1) if durations else 0
    langs: dict[str, int] = {}
    for c in calls:
        lang = c["language"] or "?"
        langs[lang] = langs.get(lang, 0) + 1
    return {
        "total":        total,
        "today":        today_count,
        "avg_duration": avg_dur,
        "languages":    langs,
    }


def _runtime_status() -> dict:
    log_dir = _latest_log_dir()
    services = {}
    for name, port in [("socket_server", 9000), ("ws_audio_receiver", 9001)]:
        pid_file = log_dir / f"{name}.pid" if log_dir else None
        pid = None
        alive = False
        if pid_file and pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                alive = Path(f"/proc/{pid}").exists()
            except Exception:
                pass
        # also check port
        if not alive:
            result = subprocess.run(
                ["ss", "-ltn"], capture_output=True, text=True, timeout=2
            )
            alive = f":{port} " in result.stdout
        services[name] = {"pid": pid, "alive": alive, "port": port}
    return {"services": services, "log_dir": str(log_dir) if log_dir else None}


# ── routes ───────────────────────────────────────────────────────────────────

@app.get("/")
async def root() -> HTMLResponse:
    html = (DASHBOARD_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.get("/api/overview")
async def overview():
    calls = _list_calls()
    return {"stats": _overview_stats(calls), "recent": calls[:10]}


@app.get("/api/calls")
async def list_calls():
    return {"calls": _list_calls()}


@app.get("/api/calls/{folder}")
async def get_call(folder: str):
    path = RECORDINGS_DIR / folder
    if not path.is_dir():
        from fastapi import HTTPException
        raise HTTPException(404, "Call not found")
    return {
        "meta":       _parse_metadata(path),
        "transcript": _parse_transcript(path),
    }


@app.get("/api/runtime/status")
async def runtime_status():
    return _runtime_status()


@app.get("/api/runtime/output/stream")
async def log_stream():
    """SSE stream: tail the active ws_audio_receiver.log."""
    log_dir = _latest_log_dir()
    log_file = (log_dir / "ws_audio_receiver.log") if log_dir else None

    async def generate() -> AsyncIterator[str]:
        if not log_file or not log_file.exists():
            yield "data: {\"line\": \"[no active log file]\"}\n\n"
            return
        proc = await asyncio.create_subprocess_exec(
            "tail", "-n", "60", "-f", str(log_file),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            while True:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=30)
                if not line:
                    break
                text = line.decode(errors="replace").rstrip()
                yield f"data: {json.dumps({'line': text})}\n\n"
        except asyncio.TimeoutError:
            yield "data: {\"line\": \"[heartbeat]\"}\n\n"
        except Exception:
            pass
        finally:
            proc.kill()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    """WebSocket mirror of the SSE log stream."""
    await ws.accept()
    log_dir = _latest_log_dir()
    log_file = (log_dir / "ws_audio_receiver.log") if log_dir else None

    if not log_file or not log_file.exists():
        await ws.send_text(json.dumps({"line": "[no active log file]"}))
        await ws.close()
        return

    proc = await asyncio.create_subprocess_exec(
        "tail", "-n", "60", "-f", str(log_file),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        while True:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=25)
            if not line:
                break
            text = line.decode(errors="replace").rstrip()
            await ws.send_text(json.dumps({"line": text}))
    except (WebSocketDisconnect, asyncio.TimeoutError, Exception):
        pass
    finally:
        proc.kill()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
