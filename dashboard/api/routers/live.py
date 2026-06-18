"""Live call feed over WebSocket.

MVP: streams the tail of the runtime log (where call_bridge logs live transcript +
stage transitions) to the browser, cross-process and with zero pipeline changes.
Audio monitoring / richer events can layer on top of the same socket later.

Env LESCALL_LOG points at the log to tail (default phase14_runtime/logs/latest/ws_audio_receiver.log).
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from shared.db.database import _REPO_ROOT

router = APIRouter()

LOG_PATH = Path(os.environ.get(
    "LESCALL_LOG",
    _REPO_ROOT / "phase14_runtime" / "logs" / "latest" / "ws_audio_receiver.log",
))


@router.websocket("/live/ws")
async def live_ws(ws: WebSocket) -> None:
    await ws.accept()
    # Preferred path: Redis pub/sub (multi-node — sees every call). Falls back to
    # tailing the local runtime log when Redis isn't configured/reachable.
    try:
        from shared.events import subscribe, REDIS_URL, CHANNEL
        if REDIS_URL:
            await ws.send_json({"type": "status", "message": "connected",
                                "source": "redis", "channel": CHANNEL})
            async for event in subscribe():
                await ws.send_json({"type": "event", **event})
            return
    except WebSocketDisconnect:
        return
    except Exception:
        # Redis unavailable -> fall through to log tailing below.
        pass
    try:
        # Seek to end; stream new lines as they are appended.
        f = None
        if LOG_PATH.exists():
            f = LOG_PATH.open("r", errors="replace")
            f.seek(0, os.SEEK_END)
        await ws.send_json({"type": "status", "message": "connected",
                            "log": str(LOG_PATH), "tailing": f is not None})
        while True:
            if f is None:
                if LOG_PATH.exists():
                    f = LOG_PATH.open("r", errors="replace")
                    f.seek(0, os.SEEK_END)
                else:
                    await asyncio.sleep(1.0)
                    continue
            line = f.readline()
            if line:
                await ws.send_json({"type": "log", "line": line.rstrip()})
            else:
                await asyncio.sleep(0.4)
    except WebSocketDisconnect:
        return
