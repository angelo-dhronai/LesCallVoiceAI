"""Minimal, dependency-free FreeSWITCH inbound Event Socket (ESL) client.

This module lets code run FreeSWITCH API commands over TCP without requiring
the `fs_cli` binary. It is used by the dashboard / dialer to control
FreeSWITCH from inside a container (host networking) where `fs_cli` is not
installed but the FreeSWITCH event socket (default 127.0.0.1:8021) is
reachable.

Pure Python stdlib only (socket). No third-party dependencies.

Config is read from the environment with sensible defaults:
    FS_ESL_HOST     (default 127.0.0.1)
    FS_ESL_PORT     (default 8021)
    FS_ESL_PASSWORD (default ClueCon)

Public API:
    esl_api(command, timeout=10.0) -> tuple[bool, str]
"""

import os
import socket

FS_ESL_HOST = os.environ.get("FS_ESL_HOST", "127.0.0.1")
FS_ESL_PORT = int(os.environ.get("FS_ESL_PORT", "8021"))
FS_ESL_PASSWORD = os.environ.get("FS_ESL_PASSWORD", "ClueCon")


def _find_header_break(buf):
    """Find the end of the header block. FreeSWITCH separates headers from the
    body with a blank line, which may be "\n\n" or "\r\n\r\n". Returns
    (index_of_break, marker_len) or (-1, 0) if not yet present."""
    i_crlf = buf.find(b"\r\n\r\n")
    i_lf = buf.find(b"\n\n")
    if i_crlf != -1 and (i_lf == -1 or i_crlf <= i_lf):
        return i_crlf, 4
    if i_lf != -1:
        return i_lf, 2
    return -1, 0


def _read_message(sock, buf):
    """Read one ESL message from the socket.

    An ESL message is a block of headers terminated by a blank line, optionally
    followed by a body of Content-Length bytes.

    Returns (headers_dict, body_text, remaining_buf).
    """
    # Ensure we have the full header block.
    idx, mlen = _find_header_break(buf)
    while idx == -1:
        chunk = sock.recv(4096)
        if not chunk:
            return {}, "", buf
        buf += chunk
        idx, mlen = _find_header_break(buf)

    header_blob = buf[:idx]
    rest = buf[idx + mlen:]

    headers = {}
    for line in header_blob.replace(b"\r\n", b"\n").split(b"\n"):
        if b":" in line:
            key, _, val = line.partition(b":")
            headers[key.decode(errors="replace").strip()] = val.decode(
                errors="replace"
            ).strip()

    body = b""
    content_length = headers.get("Content-Length")
    if content_length:
        need = int(content_length)
        # Pull the rest of the body off the wire.
        while len(rest) < need:
            chunk = sock.recv(4096)
            if not chunk:
                break
            rest += chunk
        body = rest[:need]
        rest = rest[need:]

    return headers, body.decode(errors="replace"), rest


def esl_api(command: str, timeout: float = 10.0) -> tuple[bool, str]:
    """Run a FreeSWITCH `api` command via the inbound event socket.

    Opens a TCP connection, authenticates, issues `api <command>`, and returns
    the response body. Never raises: any error is returned as (False, repr(e)).

    Returns:
        (True, body_text)  on success
        (False, error_str) on failure
    """
    sock = None
    try:
        sock = socket.create_connection((FS_ESL_HOST, FS_ESL_PORT), timeout=timeout)
        sock.settimeout(timeout)

        buf = b""

        # 1. Initial banner: Content-Type: auth/request
        headers, _body, buf = _read_message(sock, buf)
        if headers.get("Content-Type") != "auth/request":
            return False, "unexpected banner: %r" % (headers,)

        # 2. Authenticate.
        sock.sendall(b"auth %s\n\n" % FS_ESL_PASSWORD.encode())
        headers, body, buf = _read_message(sock, buf)
        reply = headers.get("Reply-Text", "") or body
        if "+OK" not in reply:
            return False, "auth failed"

        # 3. Issue the api command.
        sock.sendall(b"api %s\n\n" % command.encode())

        # 4. Read the api/response. Skip any unrelated event blocks until we
        #    get the command reply (Content-Type: api/response).
        for _ in range(50):
            headers, body, buf = _read_message(sock, buf)
            if not headers:
                return False, "no response"
            ctype = headers.get("Content-Type", "")
            if ctype == "api/response":
                return True, body
            if ctype == "command/reply":
                return True, headers.get("Reply-Text", body)
            # Otherwise it was an event/notice — keep reading.

        return False, "no api/response received"

    except Exception as e:  # noqa: BLE001 - never raise per contract
        return False, repr(e)
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


if __name__ == "__main__":
    ok, out = esl_api("status")
    print("ok=%s" % ok)
    print(out)
