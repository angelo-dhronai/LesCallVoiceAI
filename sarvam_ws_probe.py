"""
sarvam_ws_probe.py — Phase 11A.1 standalone Sarvam WebSocket diagnostic.

A self-contained probe to isolate the *provider* protocol from the rest
of the realtime telephony pipeline. Reproduces the socket lifecycle that
SarvamStreamingSTTAdapter (in stt_stream_worker.py) executes, but
WITHOUT touching:

    socket_server.py    ws_audio_receiver.py    FreeSWITCH / dialplan
    mod_audio_stream    queueing                ESL flow

Goal: determine EXACTLY why Sarvam currently rejects our handshake with
HTTP 403 (websockets.exceptions.InvalidStatusCode: 403).

The probe runs a *test matrix* of plausible permutations
(URL path, auth header, query params, audio framing) and for each
permutation prints a fixed diagnostic block:

    ================================================
    ATTEMPT N
    ================================================
    URL:
    HEADERS:
    QUERY:
    CONNECTED:
    HTTP STATUS:
    SERVER RESPONSE:
    FIRST FRAME:
    TRANSCRIPT RECEIVED:
    CLOSE CODE:
    CLOSE REASON:
    LATENCY:
    ================================================

so the operator can compare attempt-by-attempt and spot which knob
flipped a 403 into a 101 Switching Protocols.

Usage
-----
    python3 sarvam_ws_probe.py --api-key $SARVAM_API_KEY
    python3 sarvam_ws_probe.py --api-key $SARVAM_API_KEY --sample-rate 16000
    python3 sarvam_ws_probe.py --api-key $SARVAM_API_KEY --wav /path/to.wav
    python3 sarvam_ws_probe.py --api-key $SARVAM_API_KEY --verbose
    python3 sarvam_ws_probe.py --api-key $SARVAM_API_KEY \
        --only-attempt 3                 # run a single attempt by index

The probe never modifies any other file. It is read-only on the
filesystem (except for stdout) and stateless across runs.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import math
import os
import socket
import ssl
import struct
import sys
import time
import wave
from typing import Any, Optional
from urllib.parse import urlencode, urlparse

import websockets
import websockets.exceptions


# ---------------------------------------------------------------------
# logging
# ---------------------------------------------------------------------

log = logging.getLogger("probe")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s.%(msecs)03d %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )
    # websockets' own debug is extremely useful for handshake forensics.
    logging.getLogger("websockets").setLevel(
        logging.DEBUG if verbose else logging.WARNING)


# ---------------------------------------------------------------------
# audio sources
# ---------------------------------------------------------------------

def synth_sine_pcm(sample_rate: int, duration_s: float = 1.0,
                   freq_hz: float = 440.0,
                   amplitude: int = 12000) -> bytes:
    """Generate mono 16-bit little-endian PCM of a sine wave.

    Default amplitude (12000) is well above any reasonable VAD floor so
    Sarvam's saaras VAD sees this as speech-like signal energy. The
    point isn't intelligibility (nothing will transcribe to real
    words); the point is to give the provider audio that won't be
    dropped by silence detection."""
    n = int(sample_rate * duration_s)
    out = []
    two_pi_f_over_sr = 2.0 * math.pi * freq_hz / float(sample_rate)
    for i in range(n):
        v = int(amplitude * math.sin(two_pi_f_over_sr * i))
        if v > 32767:
            v = 32767
        elif v < -32768:
            v = -32768
        out.append(v)
    return struct.pack("<" + "h" * n, *out)


def load_wav_pcm(path: str) -> tuple[bytes, int]:
    """Read a mono 16-bit PCM WAV. Returns (pcm_bytes, sample_rate)."""
    with wave.open(path, "rb") as w:
        if w.getnchannels() != 1:
            raise ValueError(
                f"WAV {path} has {w.getnchannels()} channels; expected 1")
        if w.getsampwidth() != 2:
            raise ValueError(
                f"WAV {path} sampwidth={w.getsampwidth()}; expected 2")
        sr = w.getframerate()
        n = w.getnframes()
        pcm = w.readframes(n)
    return pcm, sr


def chunk_pcm(pcm: bytes, sample_rate: int,
              chunk_ms: int = 100) -> list[bytes]:
    """Split mono L16 PCM into ~chunk_ms slices."""
    bytes_per_chunk = (sample_rate // 1000) * chunk_ms * 2
    if bytes_per_chunk <= 0:
        return [pcm] if pcm else []
    out = []
    for off in range(0, len(pcm), bytes_per_chunk):
        out.append(pcm[off:off + bytes_per_chunk])
    return out


# ---------------------------------------------------------------------
# attempt result + reporter
# ---------------------------------------------------------------------

def _mask_key(k: str) -> str:
    if not k:
        return "(empty)"
    if len(k) <= 8:
        return k[:2] + "***"
    return f"{k[:6]}...{k[-4:]}  (len={len(k)})"


def _mask_headers(headers: list[tuple[str, str]],
                  mask_keys: tuple[str, ...]) -> list[tuple[str, str]]:
    out = []
    for k, v in headers:
        if k.lower() in [m.lower() for m in mask_keys]:
            out.append((k, _mask_key(v)))
        else:
            out.append((k, v))
    return out


class AttemptResult:
    __slots__ = (
        "idx", "name", "base_url", "query", "headers_masked",
        "connected", "http_status", "server_response",
        "first_frame", "transcript", "close_code",
        "close_reason", "latency_ms", "frames_sent",
        "frames_received", "error",
    )

    def __init__(self, idx: int, name: str) -> None:
        self.idx = idx
        self.name = name
        self.base_url: str = ""
        self.query: dict = {}
        self.headers_masked: list[tuple[str, str]] = []
        self.connected: bool = False
        self.http_status: Optional[int] = None
        self.server_response: str = ""
        self.first_frame: str = ""
        self.transcript: list[str] = []
        self.close_code: Optional[int] = None
        self.close_reason: str = ""
        self.latency_ms: Optional[float] = None
        self.frames_sent: int = 0
        self.frames_received: int = 0
        self.error: Optional[str] = None

    def render(self) -> str:
        full_url = self.base_url
        if self.query:
            full_url = self.base_url + "?" + urlencode(self.query)
        lines = []
        lines.append("=" * 64)
        lines.append(f"ATTEMPT {self.idx}: {self.name}")
        lines.append("=" * 64)
        lines.append(f"URL:               {full_url}")
        lines.append("HEADERS:")
        for k, v in self.headers_masked:
            lines.append(f"    {k}: {v}")
        lines.append("QUERY:")
        for k, v in self.query.items():
            lines.append(f"    {k}={v}")
        lines.append(
            f"CONNECTED:         {'yes' if self.connected else 'no'}")
        lines.append(
            f"HTTP STATUS:       "
            f"{self.http_status if self.http_status is not None else '-'}")
        lines.append(
            f"SERVER RESPONSE:   "
            f"{self.server_response or '(none)'}")
        lines.append(
            f"FIRST FRAME:       "
            f"{self.first_frame or '(none)'}")
        if self.transcript:
            lines.append("TRANSCRIPT RECEIVED:")
            for t in self.transcript:
                lines.append(f"    {t}")
        else:
            lines.append("TRANSCRIPT RECEIVED: (none)")
        lines.append(
            f"CLOSE CODE:        "
            f"{self.close_code if self.close_code is not None else '-'}")
        lines.append(
            f"CLOSE REASON:      {self.close_reason or '(none)'}")
        lines.append(
            f"LATENCY:           "
            f"{f'{self.latency_ms:.1f} ms' if self.latency_ms is not None else '-'}")
        lines.append(
            f"FRAMES sent={self.frames_sent} recv={self.frames_received}")
        if self.error:
            lines.append(f"ERROR:             {self.error}")
        lines.append("=" * 64)
        return "\n".join(lines)


# ---------------------------------------------------------------------
# raw HTTPS upgrade probe (captures the JSON error body that the
# websockets library throws away before we can read it)
# ---------------------------------------------------------------------

def raw_upgrade_probe(url: str,
                      headers: list[tuple[str, str]],
                      timeout_s: float = 6.0) -> dict:
    """Perform a literal HTTP/1.1 Upgrade: websocket request and read
    the full response (status line + headers + body). Used when the
    server returns a non-101 status and we need the body to understand
    *why* — websockets.connect raises before exposing it.

    Returns: {status, reason, headers (list[tuple]), body (str)}
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = path + "?" + parsed.query

    ws_key = base64.b64encode(os.urandom(16)).decode("ascii")
    req_headers = [
        ("Host", host),
        ("Upgrade", "websocket"),
        ("Connection", "Upgrade"),
        ("Sec-WebSocket-Key", ws_key),
        ("Sec-WebSocket-Version", "13"),
        ("User-Agent", "sarvam-ws-probe/1.0"),
    ] + list(headers)

    req = f"GET {path} HTTP/1.1\r\n"
    for k, v in req_headers:
        req += f"{k}: {v}\r\n"
    req += "\r\n"

    sock = socket.create_connection((host, port), timeout=timeout_s)
    try:
        if parsed.scheme == "wss":
            ctx = ssl.create_default_context()
            sock = ctx.wrap_socket(sock, server_hostname=host)
        sock.sendall(req.encode("ascii"))
        sock.settimeout(timeout_s)
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
            if len(buf) > 65536:
                break
        # split header/body
        if b"\r\n\r\n" in buf:
            head, _, body = buf.partition(b"\r\n\r\n")
        else:
            head, body = buf, b""
        # try to drain a bounded body
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                body += chunk
                if len(body) > 65536:
                    break
        except (socket.timeout, OSError):
            pass

        head_text = head.decode("iso-8859-1", "replace")
        lines = head_text.split("\r\n")
        status_line = lines[0] if lines else ""
        parts = status_line.split(" ", 2)
        try:
            status = int(parts[1]) if len(parts) >= 2 else -1
        except ValueError:
            status = -1
        reason = parts[2] if len(parts) >= 3 else ""
        resp_headers: list[tuple[str, str]] = []
        for line in lines[1:]:
            if ":" in line:
                k, _, v = line.partition(":")
                resp_headers.append((k.strip(), v.strip()))
        body_text = body.decode("utf-8", "replace")
        return {
            "status": status,
            "reason": reason,
            "headers": resp_headers,
            "body": body_text,
        }
    finally:
        try:
            sock.close()
        except Exception:
            pass


# ---------------------------------------------------------------------
# core probe routine
# ---------------------------------------------------------------------

# Sentinel values for the test matrix.
_MASK_HEADER_NAMES = (
    "Api-Subscription-Key", "api-subscription-key",
    "x-api-key", "X-API-Key", "Authorization",
    "x-subscription-key",
)


async def run_attempt(
    *,
    idx: int,
    name: str,
    base_url: str,
    query: dict,
    headers: list[tuple[str, str]],
    api_key: str,
    audio_chunks: list[bytes],
    sample_rate: int,
    audio_payload_builder,
    flush_payload: Optional[dict],
    connect_timeout_s: float,
    recv_window_s: float,
    subprotocols: Optional[list[str]] = None,
) -> AttemptResult:
    """Perform a single connect+stream cycle and capture every signal.

    The matrix below feeds one of these per permutation. Each attempt
    is fully independent — fresh socket, fresh state.
    """

    res = AttemptResult(idx, name)
    res.base_url = base_url
    res.query = dict(query)
    res.headers_masked = _mask_headers(headers, _MASK_HEADER_NAMES)

    full_url = base_url + ("?" + urlencode(query) if query else "")

    log.info("--- attempt %d (%s) ---", idx, name)
    log.info("connecting to %s", full_url)
    log.debug("headers: %s", res.headers_masked)

    t0 = time.monotonic()
    ws = None
    try:
        connect_kwargs: dict[str, Any] = {
            "extra_headers": headers,
            "open_timeout": connect_timeout_s,
            "ping_interval": None,
            "max_size": 2 ** 20,
            # keep deflate default — Sarvam handles it.
        }
        if subprotocols:
            connect_kwargs["subprotocols"] = subprotocols
        ws = await websockets.connect(full_url, **connect_kwargs)
        res.connected = True
        res.http_status = 101
        res.latency_ms = (time.monotonic() - t0) * 1000.0
        log.info("CONNECTED rtt=%.1fms", res.latency_ms)
    except websockets.exceptions.InvalidStatusCode as exc:
        res.connected = False
        res.http_status = getattr(exc, "status_code", None)
        # The websockets library raises before reading the body.
        # Re-issue the same request via a raw HTTPS upgrade to capture
        # the body Sarvam is actually returning.
        try:
            raw = raw_upgrade_probe(full_url, headers)
            body = raw["body"].strip()
            res.server_response = (
                f"HTTP {raw['status']} {raw['reason']} | "
                f"body: {body[:400]}")
            log.warning("HANDSHAKE REJECTED status=%s body=%s",
                        res.http_status, body[:400])
        except Exception as raw_exc:
            hdrs = getattr(exc, "response_headers", None)
            if hdrs is not None:
                res.server_response = (
                    "; ".join(f"{k}={v}" for k, v in hdrs.items()))
            else:
                res.server_response = repr(exc)
            log.warning("HANDSHAKE REJECTED status=%s err=%r "
                        "(raw probe fallback failed: %r)",
                        res.http_status, exc, raw_exc)
        res.error = repr(exc)
        return res
    except (websockets.exceptions.InvalidHandshake,
            ConnectionRefusedError, OSError, asyncio.TimeoutError) as exc:
        res.connected = False
        res.error = repr(exc)
        res.server_response = repr(exc)
        log.warning("CONNECT FAILED err=%r", exc)
        return res
    except Exception as exc:
        res.connected = False
        res.error = repr(exc)
        res.server_response = repr(exc)
        log.warning("CONNECT FAILED (unexpected) err=%r", exc)
        return res

    try:
        # --- streaming phase ---
        # Bound the receive task by recv_window_s starting from open.
        deadline = time.monotonic() + recv_window_s

        async def _recv_loop():
            try:
                while True:
                    timeout = deadline - time.monotonic()
                    if timeout <= 0:
                        return
                    try:
                        raw = await asyncio.wait_for(
                            ws.recv(), timeout=timeout)
                    except asyncio.TimeoutError:
                        return
                    res.frames_received += 1
                    text = raw if isinstance(raw, str) else (
                        f"<binary {len(raw)}B>")
                    if not res.first_frame:
                        res.first_frame = (text[:200]
                                           if isinstance(text, str)
                                           else text)
                    log.info("RECV[%d]: %s",
                             res.frames_received, text[:300]
                             if isinstance(text, str) else text)
                    # Heuristic: any 'transcript' field is a transcript
                    if isinstance(raw, str):
                        try:
                            obj = json.loads(raw)
                        except Exception:
                            obj = None
                        if isinstance(obj, dict):
                            data = obj.get("data") or {}
                            t = (data.get("transcript")
                                 if isinstance(data, dict) else None)
                            if t:
                                res.transcript.append(t)
            except websockets.exceptions.ConnectionClosed:
                return
            except Exception as exc:
                log.warning("recv loop crashed: %r", exc)
                return

        recv_task = asyncio.create_task(_recv_loop())

        # send all chunks
        for chunk in audio_chunks:
            payload = audio_payload_builder(chunk, sample_rate)
            try:
                await ws.send(json.dumps(payload))
                res.frames_sent += 1
            except websockets.exceptions.ConnectionClosed as exc:
                log.warning("send aborted (closed): %r", exc)
                break
            # pace at ~realtime
            await asyncio.sleep(0.05)

        # explicit flush so VAD finalises any in-flight utterance
        if flush_payload is not None:
            try:
                await ws.send(json.dumps(flush_payload))
            except Exception as exc:
                log.debug("flush send failed: %r", exc)

        # wait for recv window to expire OR connection to close
        try:
            await asyncio.wait_for(
                recv_task, timeout=max(0.1, deadline - time.monotonic()))
        except asyncio.TimeoutError:
            recv_task.cancel()
            try:
                await recv_task
            except (asyncio.CancelledError, Exception):
                pass

    finally:
        try:
            await ws.close()
        except Exception:
            pass
        # capture close metadata
        try:
            res.close_code = ws.close_code
            res.close_reason = ws.close_reason or ""
        except Exception:
            pass

    return res


# ---------------------------------------------------------------------
# audio payload variants
# ---------------------------------------------------------------------

def payload_variant_audio_wav(pcm: bytes, sr: int) -> dict:
    """Current SarvamStreamingSTTAdapter shape."""
    return {
        "audio": {
            "data": base64.b64encode(pcm).decode("ascii"),
            "sample_rate": str(sr),
            "encoding": "audio/wav",
        }
    }


def payload_variant_audio_pcm_codec(pcm: bytes, sr: int) -> dict:
    """Variant: encoding name matches the input_audio_codec query."""
    return {
        "audio": {
            "data": base64.b64encode(pcm).decode("ascii"),
            "sample_rate": str(sr),
            "encoding": "pcm_s16le",
        }
    }


# ---------------------------------------------------------------------
# the test matrix
# ---------------------------------------------------------------------

def build_attempts(args, audio_chunks: list[bytes],
                   sample_rate: int) -> list[dict]:
    """Construct the ordered list of attempt specs.

    Order matters: cheapest checks first (auth, URL), then deeper
    protocol shape variations only if the cheap ones still 403."""

    api_key = args.api_key
    lang = args.language
    base_path = "wss://api.sarvam.ai/speech-to-text/ws"

    # The query params currently used by SarvamStreamingSTTAdapter.
    canonical_query = {
        "model": "saaras:v3",
        "language-code": lang,
        "mode": "transcribe",
        "sample_rate": str(sample_rate),
        "input_audio_codec": "pcm_s16le",
        "vad_signals": "true",
    }

    attempts: list[dict] = []

    # --- 1. baseline: exact replication of stt_stream_worker.py -----
    attempts.append(dict(
        name="baseline (Api-Subscription-Key header, current adapter)",
        base_url=base_path,
        query=dict(canonical_query),
        headers=[("Api-Subscription-Key", api_key)],
        audio_payload_builder=payload_variant_audio_wav,
        flush_payload={"type": "flush"},
    ))

    # --- 2. same as baseline but auth header lowercased --------------
    attempts.append(dict(
        name="lowercase header api-subscription-key",
        base_url=base_path,
        query=dict(canonical_query),
        headers=[("api-subscription-key", api_key)],
        audio_payload_builder=payload_variant_audio_wav,
        flush_payload={"type": "flush"},
    ))

    # --- 3. alternate header name (some Sarvam REST docs use this) --
    attempts.append(dict(
        name="alt header x-api-key",
        base_url=base_path,
        query=dict(canonical_query),
        headers=[("x-api-key", api_key)],
        audio_payload_builder=payload_variant_audio_wav,
        flush_payload={"type": "flush"},
    ))

    # --- 4. Authorization: Bearer <key> (Standard OAuth-style) ------
    attempts.append(dict(
        name="Authorization: Bearer <key>",
        base_url=base_path,
        query=dict(canonical_query),
        headers=[("Authorization", f"Bearer {api_key}")],
        audio_payload_builder=payload_variant_audio_wav,
        flush_payload={"type": "flush"},
    ))

    # --- 5. auth via query param (works on some streaming providers) -
    q5 = dict(canonical_query)
    q5["api-subscription-key"] = api_key
    attempts.append(dict(
        name="auth via query param api-subscription-key",
        base_url=base_path,
        query=q5,
        headers=[],
        audio_payload_builder=payload_variant_audio_wav,
        flush_payload={"type": "flush"},
    ))

    # --- 6. minimal query (drop everything but model+sample_rate) ---
    attempts.append(dict(
        name="minimal query (model+sample_rate only)",
        base_url=base_path,
        query={
            "model": "saaras:v3",
            "sample_rate": str(sample_rate),
        },
        headers=[("Api-Subscription-Key", api_key)],
        audio_payload_builder=payload_variant_audio_wav,
        flush_payload={"type": "flush"},
    ))

    # --- 7. translate endpoint (saaras docs sometimes route here) ---
    attempts.append(dict(
        name="alt endpoint /speech-to-text-translate/ws",
        base_url="wss://api.sarvam.ai/speech-to-text-translate/ws",
        query=dict(canonical_query),
        headers=[("Api-Subscription-Key", api_key)],
        audio_payload_builder=payload_variant_audio_wav,
        flush_payload={"type": "flush"},
    ))

    # --- 8. encoding=pcm_s16le in audio payload ---------------------
    attempts.append(dict(
        name="audio.encoding=pcm_s16le (matches codec)",
        base_url=base_path,
        query=dict(canonical_query),
        headers=[("Api-Subscription-Key", api_key)],
        audio_payload_builder=payload_variant_audio_pcm_codec,
        flush_payload={"type": "flush"},
    ))

    # --- 9. saarika model variant (newer non-translate Sarvam STT) --
    q9 = dict(canonical_query)
    q9["model"] = "saarika:v2"
    attempts.append(dict(
        name="model=saarika:v2",
        base_url=base_path,
        query=q9,
        headers=[("Api-Subscription-Key", api_key)],
        audio_payload_builder=payload_variant_audio_wav,
        flush_payload={"type": "flush"},
    ))

    # --- 10. language-code=hi-IN (some routes require concrete lang) -
    q10 = dict(canonical_query)
    q10["language-code"] = "hi-IN"
    attempts.append(dict(
        name="language-code=hi-IN",
        base_url=base_path,
        query=q10,
        headers=[("Api-Subscription-Key", api_key)],
        audio_payload_builder=payload_variant_audio_wav,
        flush_payload={"type": "flush"},
    ))

    # --- 11. /v1/ prefix (some Sarvam beta routes used /v1/) --------
    attempts.append(dict(
        name="alt path /v1/speech-to-text/ws",
        base_url="wss://api.sarvam.ai/v1/speech-to-text/ws",
        query=dict(canonical_query),
        headers=[("Api-Subscription-Key", api_key)],
        audio_payload_builder=payload_variant_audio_wav,
        flush_payload={"type": "flush"},
    ))

    # --- 12. CONTROL: send NO auth header at all -------------------
    # Compares server body for "missing auth" vs "invalid auth" to tell
    # us whether the header we sent in attempts 1..11 was being read.
    attempts.append(dict(
        name="CONTROL: no auth header (expect missing-auth body)",
        base_url=base_path,
        query=dict(canonical_query),
        headers=[],
        audio_payload_builder=payload_variant_audio_wav,
        flush_payload={"type": "flush"},
    ))

    # --- 13. CONTROL: deliberately wrong key -----------------------
    # If this returns the same body as attempt 1, the real key is
    # being treated identically to garbage — indicating the server
    # never accepted it (key revoked, wrong env, or WS not enabled).
    attempts.append(dict(
        name="CONTROL: deliberately bogus key value",
        base_url=base_path,
        query=dict(canonical_query),
        headers=[("Api-Subscription-Key",
                  "sk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")],
        audio_payload_builder=payload_variant_audio_wav,
        flush_payload={"type": "flush"},
    ))

    return attempts


# ---------------------------------------------------------------------
# main
# ---------------------------------------------------------------------

async def amain(args) -> int:
    api_key = args.api_key or os.environ.get("SARVAM_API_KEY", "").strip()
    if not api_key:
        log.error("No API key. Pass --api-key or set SARVAM_API_KEY.")
        return 2
    args.api_key = api_key

    # Decide audio source.
    if args.wav:
        pcm, sr = load_wav_pcm(args.wav)
        if sr != args.sample_rate:
            log.warning("WAV sample_rate=%d but --sample-rate=%d; "
                        "using WAV's rate %d for this run",
                        sr, args.sample_rate, sr)
        sample_rate = sr
        log.info("loaded WAV path=%s sr=%d bytes=%d duration=%.3fs",
                 args.wav, sr, len(pcm), len(pcm) / 2 / sr)
    else:
        sample_rate = args.sample_rate
        pcm = synth_sine_pcm(sample_rate, duration_s=args.duration_s)
        log.info("synthesised sine pcm sr=%d bytes=%d duration=%.3fs",
                 sample_rate, len(pcm), len(pcm) / 2 / sample_rate)

    audio_chunks = chunk_pcm(pcm, sample_rate, chunk_ms=100)
    log.info("audio split into %d chunks of ~100ms", len(audio_chunks))

    attempts = build_attempts(args, audio_chunks, sample_rate)
    if args.only_attempt is not None:
        if not (1 <= args.only_attempt <= len(attempts)):
            log.error("--only-attempt out of range: 1..%d",
                      len(attempts))
            return 2
        attempts = [attempts[args.only_attempt - 1]]
        # Re-index so the printed ATTEMPT N matches the user's input.
        first_idx = args.only_attempt
    else:
        first_idx = 1

    log.info("API key: %s", _mask_key(api_key))
    log.info("running %d attempt(s) against Sarvam", len(attempts))

    results: list[AttemptResult] = []
    for offset, spec in enumerate(attempts):
        idx = first_idx + offset
        try:
            res = await run_attempt(
                idx=idx,
                name=spec["name"],
                base_url=spec["base_url"],
                query=spec["query"],
                headers=spec["headers"],
                api_key=api_key,
                audio_chunks=audio_chunks,
                sample_rate=sample_rate,
                audio_payload_builder=spec["audio_payload_builder"],
                flush_payload=spec["flush_payload"],
                connect_timeout_s=args.connect_timeout,
                recv_window_s=args.recv_window,
                subprotocols=spec.get("subprotocols"),
            )
        except Exception as exc:
            res = AttemptResult(idx, spec["name"])
            res.error = f"probe driver crashed: {exc!r}"
            log.error("attempt %d driver crashed: %r", idx, exc)
        results.append(res)
        # Print the per-attempt block immediately so the operator can
        # follow live even if a later attempt hangs.
        print(res.render(), flush=True)
        # tiny pause so we don't hammer the provider
        await asyncio.sleep(0.25)

    # ----- final matrix summary -----
    print("\n" + "#" * 64)
    print("# MATRIX SUMMARY")
    print("#" * 64)
    print(f"{'#':>3} {'status':>8} {'frames_recv':>12}  {'transcript?':>12}  name")
    for r in results:
        status = ("CONNECT" if r.connected else
                  (str(r.http_status) if r.http_status else "ERR"))
        print(f"{r.idx:>3} {status:>8} "
              f"{r.frames_received:>12d}  "
              f"{('yes' if r.transcript else 'no'):>12}  "
              f"{r.name}")
    print("#" * 64)
    any_connected = any(r.connected for r in results)
    if any_connected:
        winners = [r for r in results if r.connected]
        print(f"# {len(winners)} attempt(s) reached 101 Switching Protocols.")
        print("# Inspect the attempt blocks above for the working knob.")
    else:
        print("# No attempt completed the WebSocket handshake.")
        print("# Every attempt was rejected before upgrade.")
        print("# Most likely causes (verify in order):")
        print("#   1. WebSocket access not enabled on this API key.")
        print("#   2. Account/key not provisioned for streaming STT.")
        print("#   3. Endpoint/path moved (check Sarvam dashboard).")
        print("#   4. Network egress blocked to api.sarvam.ai:443.")
    print("#" * 64)
    return 0 if any_connected else 1


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=("Standalone Sarvam WebSocket protocol probe. "
                     "Does NOT touch FreeSWITCH, ESL, ws_audio_receiver, "
                     "stt_stream_worker, or any realtime call flow."))
    p.add_argument(
        "--api-key", default=os.environ.get("SARVAM_API_KEY", ""),
        help=("Sarvam API key. Defaults to env SARVAM_API_KEY. "
              "The key is masked in output (first 6 + last 4)."))
    p.add_argument(
        "--sample-rate", type=int, default=8000,
        help="Sample rate in Hz to declare to Sarvam (8000 or 16000).")
    p.add_argument(
        "--language", default="unknown",
        help="language-code query param (default: unknown).")
    p.add_argument(
        "--wav", default=None,
        help=("Path to a mono 16-bit PCM WAV to stream. If omitted, a "
              "1s 440Hz sine wave is synthesised at --sample-rate."))
    p.add_argument(
        "--duration-s", type=float, default=1.0,
        help="Seconds of synthetic sine to generate (when --wav unset).")
    p.add_argument(
        "--connect-timeout", type=float, default=8.0,
        help="WebSocket open_timeout in seconds.")
    p.add_argument(
        "--recv-window", type=float, default=5.0,
        help=("Seconds to keep the socket open after sending audio, "
              "waiting for transcript frames."))
    p.add_argument(
        "--only-attempt", type=int, default=None,
        help=("Run a single attempt by 1-based index (skip the rest). "
              "Useful once you know the variant you want to repeat."))
    p.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable DEBUG-level logs and websockets internal logs.")
    return p.parse_args(argv)


def main() -> int:
    args = parse_args(sys.argv[1:])
    _setup_logging(args.verbose)
    try:
        return asyncio.run(amain(args))
    except KeyboardInterrupt:
        log.info("interrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
