"""
ws_audio_receiver.py — Phase 9 dedicated audio-stream receiver.

This is a SEPARATE process from socket_server.py (the outbound-ESL
controller). It listens for the websocket connection that
mod_audio_stream opens when uuid_audio_stream <uuid> start ws://... is
dispatched. Every binary frame it receives is raw L16 PCM (16-bit
signed little-endian, mono, 8 kHz unless configured otherwise). It
does NOT do STT/AI/TTS — Phase 9 scope is strictly transport
validation and STREAM SUMMARY diagnostics.

Why a separate process?
-----------------------
* Keeps the validated outbound-ESL controller (socket_server.py)
  byte-for-byte unchanged. A receiver crash cannot brick playback,
  recording, the ESL handshake, or RTP.
* Lets the receiver run its own asyncio loop without colliding with
  the threaded ESL handler.
* Future streaming-STT workers attach to this receiver, not to the
  ESL controller.

Wire format produced by mod_audio_stream
----------------------------------------
* Binary frames: contiguous L16 PCM samples (no RTP, no JSON wrap).
  At mono/8000, 16000 bytes/s. At mono/16000, 32000 bytes/s.
* Text frames: JSON status/control messages emitted by the module
  itself or by `uuid_audio_stream <uuid> send_text "..."`. We log
  but do not act on them in Phase 9.

Per-connection logging contract (matches Phase 7/8 quality)
-----------------------------------------------------------
* On connect:    "WS CONNECT" with peer, path, headers we care about.
* During stream: rate-limited "WS RX" lines, byte counter, gap
  detection.
* On disconnect: STREAM SUMMARY block — same shape as SESSION SUMMARY
  in socket_server.py: checkpoints, deltas, counters, verdict.

Optional raw chunk save
-----------------------
If the env var WS_AUDIO_SAVE_DIR is set, every received binary frame
is appended to a per-connection .raw file. The .raw is interleaved
L16 little-endian; convert with:
    sox -t raw -r 8000 -e signed -b 16 -c 1 in.raw out.wav

Scope NOT covered (deliberately, per Phase 9 STEP 6)
----------------------------------------------------
* STT, LLM, TTS.
* Bidirectional audio (returning audio TO the caller).
* Interruption/barge-in detection.
* Anything that changes the audio bytes received.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import struct
import time
import uuid as uuidlib

import websockets


# ---------------------------------------------------------------------
# Phase 10: optional STT-forward hook. Strictly additive. When the
# env var STT_FORWARD_HOST is empty (default), the hook is INERT and
# the receiver behaves byte-for-byte identical to Phase 9. When set,
# each WS session opens ONE TCP connection to the STT worker
# (stt_stream_worker.py) and forwards a HELLO frame followed by
# length-prefixed AUDIO frames per binary chunk. All forward I/O is
# wrapped in try/except so an STT-side failure cannot disrupt the
# WS receive loop, the STREAM SUMMARY, or the raw chunk save.
# ---------------------------------------------------------------------

STT_FORWARD_HOST = os.environ.get("STT_FORWARD_HOST", "")
STT_FORWARD_PORT = int(os.environ.get("STT_FORWARD_PORT", "9002"))

# Frame types — must mirror stt_stream_worker.py.
_STT_FRAME_HELLO = 0x01
_STT_FRAME_AUDIO = 0x02
_STT_FRAME_BYE = 0x03


def _stt_pack(ftype: int, payload: bytes) -> bytes:
    return bytes([ftype]) + struct.pack("<I", len(payload)) + payload


class _STTForwarder:
    """Per-WS-session forwarder. Best-effort: any failure switches
    the forwarder into a permanent disabled state for this session
    and increments a counter. The WS receive loop is unaffected."""

    def __init__(self, conn_id: str, sample_rate: int,
                 host: str, port: int) -> None:
        self.conn_id = conn_id
        self.sample_rate = sample_rate
        self.host = host
        self.port = port
        self.writer: "asyncio.StreamWriter | None" = None
        self.connected = False
        self.disabled = False
        self.errors = 0
        self.bytes_forwarded = 0
        self.frames_forwarded = 0

    async def open(self) -> None:
        if self.disabled or self.connected:
            return
        try:
            _r, w = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=1.0)
            self.writer = w
            hello = (f"conn_id={self.conn_id};sr="
                     f"{self.sample_rate}").encode("utf-8")
            w.write(_stt_pack(_STT_FRAME_HELLO, hello))
            await w.drain()
            self.connected = True
            log.info("STT FORWARD open conn_id=%s -> %s:%d",
                     self.conn_id, self.host, self.port)
        except Exception as exc:
            self.disabled = True
            self.errors += 1
            log.warning("STT FORWARD open failed conn_id=%s "
                        "err=%r — STT disabled for this session",
                        self.conn_id, exc)

    def forward_audio(self, pcm_bytes: bytes) -> None:
        if self.disabled or not self.connected or self.writer is None:
            return
        try:
            self.writer.write(_stt_pack(_STT_FRAME_AUDIO, pcm_bytes))
            self.bytes_forwarded += len(pcm_bytes)
            self.frames_forwarded += 1
        except Exception as exc:
            self.disabled = True
            self.errors += 1
            log.warning("STT FORWARD write failed conn_id=%s "
                        "err=%r — STT disabled for remainder of "
                        "session", self.conn_id, exc)

    async def close(self) -> None:
        if self.writer is None:
            return
        try:
            try:
                self.writer.write(
                    _stt_pack(_STT_FRAME_BYE, b""))
                await self.writer.drain()
            except Exception:
                pass
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception:
                pass
            log.info("STT FORWARD close conn_id=%s frames=%d "
                     "bytes=%d errors=%d",
                     self.conn_id, self.frames_forwarded,
                     self.bytes_forwarded, self.errors)
        except Exception as exc:
            log.warning("STT FORWARD close crashed conn_id=%s "
                        "err=%r", self.conn_id, exc)
        finally:
            self.writer = None
            self.connected = False


HOST_DEFAULT = "0.0.0.0"
PORT_DEFAULT = 9001

# Expected sample-rate / sample-width when mod_audio_stream is
# configured "mono 8000". Used only for the bytes/sec sanity check
# and "expected_pps" math in STREAM SUMMARY. Override via --sample-rate
# / --bytes-per-sample if you launch with mono 16000.
DEFAULT_SAMPLE_RATE_HZ = 8000
DEFAULT_BYTES_PER_SAMPLE = 2

# A single L16 frame from mod_audio_stream is typically ~20 ms of
# audio (= 320 bytes at 8 kHz). Frames much larger than this hint at
# upstream batching; frames much smaller hint at stream stalls. The
# "expected_chunk_size_bytes" math below uses these to flag anomalies.
EXPECTED_CHUNK_MS = 20

# Gap (no binary frame received) larger than this many milliseconds
# is logged as a STALL event in the per-connection summary.
GAP_STALL_MS = 200

# Rate-limit the live "WS RX" log line. We log once per Nth binary
# frame so a 30s call doesn't spam ~1500 lines, but the structured
# summary at end-of-call still has full byte counts.
RX_LOG_EVERY_N_FRAMES = 50


logging.basicConfig(
    level=logging.INFO,
    format=("%(asctime)s.%(msecs)03d %(levelname)-5s "
            "[%(threadName)s] %(message)s"),
    datefmt="%H:%M:%S",
)
log = logging.getLogger("wsaudio")


def _fmt_t(cp: dict, key: str) -> str:
    v = cp.get(key)
    return f"{v:.3f}s" if v is not None else "—"


def _fmt_delta(cp: dict, a: str, b: str) -> str:
    va = cp.get(a)
    vb = cp.get(b)
    if va is None or vb is None:
        return "—"
    return f"+{(vb - va) * 1000:.1f}ms"


def _classify_stream(cp: dict, counters: dict,
                     expected_bps: int) -> str:
    """Mirror of socket_server.classify(): pure function turning
    counters into a single-line verdict for the STREAM SUMMARY."""
    if "T0_ws_open" not in cp:
        return ("INCONCLUSIVE: connection never reached "
                "websocket-open state")
    if counters["binary_frames"] == 0:
        if counters["text_frames"] == 0:
            return ("CASE WS-A — websocket opened but received "
                    "zero frames before close. mod_audio_stream "
                    "reachable but audio bug likely never attached "
                    "or called stop immediately.")
        return ("CASE WS-B — only text frames received, zero binary "
                "frames. Module opened control plane but did not "
                "deliver any media. Check sample-rate negotiation "
                "and channel codec.")
    duration = (cp.get("T2_ws_close") or cp.get("T1_last_binary")
                or 0.0) - cp.get("T0_ws_open", 0.0)
    if duration <= 0.0:
        return ("CASE WS-C — frames received but duration was "
                "non-positive; clock anomaly.")
    observed_bps = counters["binary_bytes"] / duration
    ratio = observed_bps / expected_bps if expected_bps > 0 else 0.0
    if ratio < 0.5:
        return (f"CASE WS-D — bytes/sec={observed_bps:.0f} is "
                f"<50% of expected {expected_bps} for the "
                f"configured sample-rate. Frames arrived but the "
                f"stream is starved (channel idle, codec mismatch, "
                f"or provider RTP loss).")
    if ratio > 1.6:
        return (f"CASE WS-E — bytes/sec={observed_bps:.0f} is "
                f">160% of expected {expected_bps}. Either the "
                f"configured sample-rate is wrong, or the module "
                f"is forwarding stereo/mixed when this receiver "
                f"was configured mono.")
    if counters["stalls"] > 0:
        return (f"CASE WS-F — stream completed (bytes/sec ratio "
                f"{ratio:.2f}) but had {counters['stalls']} "
                f"stalls > {GAP_STALL_MS}ms. Connectivity OK; "
                f"intermittent media loss.")
    return (f"CASE WS-G — STREAM HEALTHY  bytes/sec={observed_bps:.0f} "
            f"(ratio {ratio:.2f} of {expected_bps} expected, no "
            f"stalls). Live audio fork validated end-to-end.")


def log_stream_summary(conn_id: str, cp: dict, counters: dict,
                       sample_rate_hz: int,
                       bytes_per_sample: int) -> None:
    """STREAM SUMMARY block — same shape and quality bar as the
    SESSION SUMMARY block in socket_server.py."""
    expected_bps = sample_rate_hz * bytes_per_sample
    duration = ((cp.get("T2_ws_close") or 0.0)
                - cp.get("T0_ws_open", 0.0))
    log.info("---------- STREAM SUMMARY -------------")
    log.info("conn_id=%s  duration=%.3fs", conn_id, duration)
    log.info("checkpoints:")
    for key in ("T0_ws_open", "T1_first_binary", "T1_last_binary",
                "T2_ws_close"):
        log.info("    %-28s %s", key, _fmt_t(cp, key))
    log.info("deltas:")
    log.info("    T0->T1  open -> first audio       %s",
             _fmt_delta(cp, "T0_ws_open", "T1_first_binary"))
    log.info("    T1->T1' first -> last audio       %s",
             _fmt_delta(cp, "T1_first_binary", "T1_last_binary"))
    log.info("    T1'->T2 last audio -> close       %s",
             _fmt_delta(cp, "T1_last_binary", "T2_ws_close"))
    log.info("counters: %s", counters)
    if duration > 0.0:
        observed_bps = counters["binary_bytes"] / duration
        observed_pps = counters["binary_frames"] / duration
        log.info("rates:")
        log.info("    %-28s %.1f", "binary frames/sec",
                 observed_pps)
        log.info("    %-28s %.0f", "binary bytes/sec",
                 observed_bps)
        log.info("    %-28s %d (= %d Hz * %d B/sample mono)",
                 "expected bytes/sec", expected_bps,
                 sample_rate_hz, bytes_per_sample)
        ratio = (observed_bps / expected_bps
                 if expected_bps > 0 else 0.0)
        log.info("    %-28s %.2f", "ratio observed/expected",
                 ratio)
    log.info("VERDICT: %s",
             _classify_stream(cp, counters, expected_bps))
    log.info("---------------------------------------")


async def handle_connection(websocket, path: str, args) -> None:
    """One connection = one call's audio stream.

    Phase 12 (P12-UUID-BRIDGE): if the WS request path carries the
    FS Channel-Call-UUID as the first path segment (set by
    socket_server.py's build_audio_stream_payload as
    `ws://host:port/<uuid>`), we use that FS UUID verbatim as the
    per-session conn_id. The orchestrator's playback registry on
    socket_server.py is keyed by this same FS UUID, so the round
    trip caller -> STT -> orchestrator -> playback_request -> ESL
    correctly maps back to the originating call. The path segment
    encoding is dictated by mod_audio_stream's strict URL
    validator, which rejects any '?' query string.

    If no uuid is present (Phase-9 standalone usage, hand-driven
    ws clients) we fall back to the random 8-char hex id so
    existing behavior is preserved."""
    fs_uuid = ""
    try:
        from urllib.parse import urlsplit
        # path may be e.g. '/db9df1dc-fbaf-...' or '/db9df1dc/...'
        # or '' or '/'. urlsplit handles either form.
        sp = urlsplit(path or "")
        seg = (sp.path or "").lstrip("/").split("/", 1)[0].strip()
        # FS uuids are 36-char dash-hex; accept anything alnum/dash
        # of plausible length so smoke tests with custom ids work.
        if seg and 8 <= len(seg) <= 80 and all(
                (c.isalnum() or c in "-_.") for c in seg):
            fs_uuid = seg
    except Exception:
        fs_uuid = ""
    conn_id = fs_uuid if fs_uuid else uuidlib.uuid4().hex[:8]
    t0 = time.monotonic()
    cp: dict = {"T0_ws_open": 0.0}
    counters = {
        "binary_frames": 0,
        "binary_bytes": 0,
        "text_frames": 0,
        "stalls": 0,
        "max_gap_ms": 0.0,
        "min_chunk_bytes": None,
        "max_chunk_bytes": 0,
    }
    expected_chunk = int(
        args.sample_rate * args.bytes_per_sample
        * EXPECTED_CHUNK_MS / 1000)

    peer = "?"
    try:
        peer = "%s:%d" % websocket.remote_address[:2]
    except Exception:
        pass

    log.info("=== WS CONNECT conn_id=%s peer=%s path=%s ===",
             conn_id, peer, path)
    headers = getattr(websocket, "request_headers", {}) or {}
    for hk in ("Sec-WebSocket-Protocol", "User-Agent",
               "X-Channel-UUID", "Origin"):
        if hk in headers:
            log.info("    hdr  %-22s %s", hk, headers[hk])

    out_fp = None
    if args.save_dir:
        try:
            os.makedirs(args.save_dir, exist_ok=True)
            out_path = os.path.join(args.save_dir,
                                    f"{conn_id}.raw")
            out_fp = open(out_path, "wb")
            log.info("    raw chunk save -> %s", out_path)
        except OSError as exc:
            log.error("save_dir setup failed: %r — continuing "
                      "without raw save", exc)

    # Phase 10: optional STT forwarder. INERT when STT_FORWARD_HOST
    # is empty. Open is async and best-effort; failure here flips
    # `disabled` and the WS receive loop continues unaffected.
    stt_fwd: "_STTForwarder | None" = None
    if STT_FORWARD_HOST:
        stt_fwd = _STTForwarder(
            conn_id=conn_id, sample_rate=args.sample_rate,
            host=STT_FORWARD_HOST, port=STT_FORWARD_PORT)
        try:
            await stt_fwd.open()
        except Exception as exc:
            log.warning("STT FORWARD open exception conn_id=%s "
                        "err=%r — continuing without STT",
                        conn_id, exc)
            stt_fwd = None

    last_binary_t = None
    try:
        async for msg in websocket:
            now = time.monotonic() - t0
            if isinstance(msg, (bytes, bytearray)):
                size = len(msg)
                counters["binary_frames"] += 1
                counters["binary_bytes"] += size
                if (counters["min_chunk_bytes"] is None
                        or size < counters["min_chunk_bytes"]):
                    counters["min_chunk_bytes"] = size
                if size > counters["max_chunk_bytes"]:
                    counters["max_chunk_bytes"] = size

                if "T1_first_binary" not in cp:
                    cp["T1_first_binary"] = now
                    log.info("WS RX[first] conn_id=%s size=%dB "
                             "(expected ~%dB per %dms chunk)",
                             conn_id, size, expected_chunk,
                             EXPECTED_CHUNK_MS)
                cp["T1_last_binary"] = now

                if last_binary_t is not None:
                    gap_ms = (now - last_binary_t) * 1000.0
                    if gap_ms > counters["max_gap_ms"]:
                        counters["max_gap_ms"] = gap_ms
                    if gap_ms > GAP_STALL_MS:
                        counters["stalls"] += 1
                        log.warning(
                            "WS STALL conn_id=%s gap=%.1fms "
                            "frame#%d  (threshold %dms)",
                            conn_id, gap_ms,
                            counters["binary_frames"], GAP_STALL_MS)
                last_binary_t = now

                if (counters["binary_frames"]
                        % RX_LOG_EVERY_N_FRAMES == 0):
                    elapsed = now - cp.get("T1_first_binary",
                                            now)
                    bps = (counters["binary_bytes"] / elapsed
                           if elapsed > 0 else 0.0)
                    log.info("WS RX  conn_id=%s frames=%d "
                             "bytes=%d  inst_bps=%.0f",
                             conn_id, counters["binary_frames"],
                             counters["binary_bytes"], bps)

                if out_fp is not None:
                    out_fp.write(msg)

                # Phase 10: fan-out to STT worker. forward_audio()
                # is non-blocking (writer.write buffers in the
                # asyncio transport) and self-isolating: any
                # exception flips the forwarder's `disabled` flag
                # and never re-enters the WS receive path.
                if stt_fwd is not None:
                    stt_fwd.forward_audio(bytes(msg))
            else:
                # Text frame — control message from mod_audio_stream
                # (e.g. JSON status, or echo of send_text). We log
                # raw and continue.
                counters["text_frames"] += 1
                preview = (msg[:240] if isinstance(msg, str)
                           else repr(msg)[:240])
                log.info("WS RX[text] conn_id=%s frame#%d "
                         "body=%s",
                         conn_id, counters["text_frames"], preview)
    except websockets.exceptions.ConnectionClosed as exc:
        log.info("WS CLOSE conn_id=%s code=%s reason=%r",
                 conn_id, exc.code, exc.reason)
    except Exception as exc:
        log.error("WS ERROR conn_id=%s %r", conn_id, exc)
    finally:
        cp["T2_ws_close"] = time.monotonic() - t0
        if out_fp is not None:
            try:
                out_fp.close()
            except OSError:
                pass
        # Phase 10: graceful close of the STT forward channel.
        # Wrapped: must not break STREAM SUMMARY emission.
        if stt_fwd is not None:
            try:
                await stt_fwd.close()
            except Exception as exc:
                log.warning("STT FORWARD close failed conn_id=%s "
                            "err=%r", conn_id, exc)
        try:
            log_stream_summary(conn_id, cp, counters,
                               args.sample_rate,
                               args.bytes_per_sample)
        except Exception as exc:
            log.error("STREAM SUMMARY render failed: %r", exc)


async def main_async(args) -> None:
    log.info("Phase-10 ws_audio_receiver listening on ws://%s:%d  "
             "(mono %dHz %dbit  save_dir=%s  stt_forward=%s)",
             args.host, args.port, args.sample_rate,
             args.bytes_per_sample * 8,
             args.save_dir or "(disabled)",
             (f"{STT_FORWARD_HOST}:{STT_FORWARD_PORT}"
              if STT_FORWARD_HOST else "(disabled)"))

    async def _entry(ws, path):
        await handle_connection(ws, path, args)

    async with websockets.serve(
        _entry, args.host, args.port,
        max_size=2**20,
        ping_interval=None,
    ):
        await asyncio.Future()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default=HOST_DEFAULT)
    p.add_argument("--port", type=int, default=PORT_DEFAULT)
    p.add_argument("--sample-rate", type=int,
                   default=DEFAULT_SAMPLE_RATE_HZ,
                   help="match the value passed to "
                        "uuid_audio_stream (8000 or 16000)")
    p.add_argument("--bytes-per-sample", type=int,
                   default=DEFAULT_BYTES_PER_SAMPLE)
    p.add_argument("--save-dir",
                   default=os.environ.get("WS_AUDIO_SAVE_DIR", ""))
    args = p.parse_args()
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        log.info("shutdown requested")


if __name__ == "__main__":
    main()
