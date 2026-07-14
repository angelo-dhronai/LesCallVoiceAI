"""Minimal FreeSWITCH inbound-ESL client (stdlib only).

Replaces `fs_cli -x "<command>"` subprocess calls — fs_cli does not exist
inside the app containers, but FreeSWITCH's event socket (127.0.0.1:8021)
is reachable from them (host networking). Auth + one `api` command per
connection: simple, stateless, no daemon.

    from shared.fs_esl import fs_api
    body = fs_api("originate {...}sofia/gateway/... &park()")
"""

from __future__ import annotations

import os
import socket


HOST = os.environ.get("FS_ESL_HOST", "127.0.0.1")
PORT = int(os.environ.get("FS_ESL_PORT", "8021"))
PASSWORD = os.environ.get("FS_ESL_PASSWORD", "ClueCon")


class ESLError(RuntimeError):
    pass


def _read_frame(f) -> tuple[dict, bytes]:
    """One ESL frame: MIME-ish headers, blank line, optional body."""
    headers: dict[str, str] = {}
    while True:
        line = f.readline()
        if not line:
            raise ESLError("connection closed by FreeSWITCH")
        line = line.strip()
        if not line:
            break
        if b":" in line:
            k, v = line.split(b":", 1)
            headers[k.decode().strip().lower()] = v.decode().strip()
    body = b""
    n = int(headers.get("content-length", 0) or 0)
    while len(body) < n:
        chunk = f.read(n - len(body))
        if not chunk:
            break
        body += chunk
    return headers, body


def fs_api(command: str, timeout: float = 60.0) -> str:
    """Run one `api` command; returns the raw response body (e.g.
    '+OK <uuid>' or '-ERR ...'). Raises ESLError on transport/auth failure —
    never on a command-level -ERR (callers parse that themselves, exactly
    like they parsed fs_cli output)."""
    with socket.create_connection((HOST, PORT), timeout=10) as s:
        s.settimeout(timeout)
        f = s.makefile("rb")
        headers, _ = _read_frame(f)
        if headers.get("content-type") != "auth/request":
            raise ESLError(f"unexpected greeting: {headers}")
        s.sendall(f"auth {PASSWORD}\n\n".encode())
        headers, _ = _read_frame(f)
        if "+OK" not in headers.get("reply-text", ""):
            raise ESLError(f"ESL auth failed: {headers.get('reply-text')}")
        s.sendall(f"api {command}\n\n".encode())
        # Skip any interleaved non-response frames (none expected — we never
        # subscribe to events — but be tolerant).
        for _ in range(5):
            headers, body = _read_frame(f)
            if headers.get("content-type") == "api/response":
                return body.decode(errors="replace")
        raise ESLError("no api/response frame received")
