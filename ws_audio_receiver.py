"""
ws_audio_receiver.py — Phase 15 Audio Format Normalization.

This process receives audio from FreeSWITCH via WebSocket,
normalizes it (PCMU -> PCM16, 8k -> 16k), and forwards it to the
AI Pipeline.
"""

from __future__ import annotations

import argparse
import asyncio
import audioop
import base64
import json
import logging
import os
import struct
import time
import uuid as uuidlib
import wave

from conversation_call_bridge import call_bridge

# ---------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------

INPUT_CODEC = os.environ.get("TELEPHONY_INPUT_CODEC", "pcmu").lower()
TARGET_SR = int(os.environ.get("TELEPHONY_TARGET_SR", "16000"))

PHASE15_DEBUG_DIR = "/tmp/phase15_audio_debug"
DEBUG_CAPTURE_SECONDS = 5

HOST_DEFAULT = "0.0.0.0"
PORT_DEFAULT = 9001

DEFAULT_SAMPLE_RATE_HZ = 8000
DEFAULT_BYTES_PER_SAMPLE = 2

EXPECTED_CHUNK_MS = 20
GAP_STALL_MS = 200
RX_LOG_EVERY_N_FRAMES = 50

# ---------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s.%(msecs)03d %(levelname)-5s "
        "[%(threadName)s] %(message)s"
    ),
    datefmt="%H:%M:%S",
)

log = logging.getLogger("wsaudio")

# ---------------------------------------------------------------------
# DEBUG AUDIO DUMPER
# ---------------------------------------------------------------------


class _Phase15AudioDebugger:

    def __init__(self, conn_id: str, sample_rate: int):

        self.conn_id = conn_id
        self.sample_rate = sample_rate

        self.capture_limit_bytes = 32000 * DEBUG_CAPTURE_SECONDS
        self.captured_bytes = 0

        self.raw_fp = None
        self.first_frame_logged = False

        os.makedirs(PHASE15_DEBUG_DIR, exist_ok=True)

        self.raw_path = os.path.join(
            PHASE15_DEBUG_DIR,
            f"raw_{conn_id}.raw",
        )

        self.wav_path = os.path.join(
            PHASE15_DEBUG_DIR,
            f"wav_{conn_id}.wav",
        )

    def process_frame(self, data: bytes):

        if self.captured_bytes >= self.capture_limit_bytes:

            if self.raw_fp:
                self.close()

            return

        if not self.raw_fp:

            self.raw_fp = open(self.raw_path, "wb")

        self.raw_fp.write(data)

        self.captured_bytes += len(data)

        if not self.first_frame_logged:

            self.log_metadata(data)

            self.first_frame_logged = True

    def log_metadata(self, data: bytes):

        size = len(data)

        if size == 0:
            return

        ff_ratio = data.count(b"\xff") / size
        zero_ratio = data.count(b"\x00") / size

        is_ulaw_like = size == 160

        codec_guess = "pcmu" if is_ulaw_like else "pcm16"

        try:

            if is_ulaw_like:

                lin_data = audioop.ulaw2lin(data, 2)

                rms = audioop.rms(lin_data, 2)

                min_v, max_v = audioop.minmax(lin_data, 2)

            else:

                rms = audioop.rms(data, 2)

                min_v, max_v = audioop.minmax(data, 2)

        except Exception:

            rms = -1
            min_v = -1
            max_v = -1

        log.info("[AUDIO_DEBUG] conn_id=%s", self.conn_id)
        log.info("[AUDIO_DEBUG] codec_guess=%s", codec_guess)
        log.info("[AUDIO_DEBUG] frame_bytes=%d", size)
        log.info("[AUDIO_DEBUG] rms=%d", rms)
        log.info("[AUDIO_DEBUG] min=%d", min_v)
        log.info("[AUDIO_DEBUG] max=%d", max_v)
        log.info(
            "[AUDIO_DEBUG] ulaw_like=%s ff_ratio=%.2f zero_ratio=%.2f",
            is_ulaw_like,
            ff_ratio,
            zero_ratio,
        )

    def close(self):

        if not self.raw_fp:
            return

        try:

            self.raw_fp.close()

            self.raw_fp = None

            self.generate_wav()

        except Exception:
            pass

    def generate_wav(self):

        try:

            with open(self.raw_path, "rb") as f:
                raw_data = f.read()

            with wave.open(self.wav_path, "wb") as wf:

                wf.setnchannels(1)

                wf.setsampwidth(2)

                wf.setframerate(self.sample_rate)

                wf.writeframes(raw_data)

            log.info(
                "[AUDIO_DEBUG] Generated WAV: %s",
                self.wav_path,
            )

        except Exception as exc:

            log.error(
                "[AUDIO_DEBUG] WAV generation failed: %r",
                exc,
            )

# ---------------------------------------------------------------------
# STT FORWARDER
# ---------------------------------------------------------------------

STT_FORWARD_HOST = os.environ.get("STT_FORWARD_HOST", "")
STT_FORWARD_PORT = int(os.environ.get("STT_FORWARD_PORT", "9002"))

_STT_FRAME_HELLO = 0x01
_STT_FRAME_AUDIO = 0x02
_STT_FRAME_BYE = 0x03


def _stt_pack(ftype: int, payload: bytes) -> bytes:

    return bytes([ftype]) + struct.pack("<I", len(payload)) + payload


class _STTForwarder:

    def __init__(
        self,
        conn_id: str,
        sample_rate: int,
        host: str,
        port: int,
    ):

        self.conn_id = conn_id
        self.sample_rate = sample_rate
        self.host = host
        self.port = port

        self.writer = None

        self.connected = False
        self.disabled = False

    async def open(self):

        if self.disabled or self.connected:
            return

        try:

            _, w = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=1.0,
            )

            self.writer = w

            hello = (
                f"conn_id={self.conn_id};sr={self.sample_rate}"
            ).encode("utf-8")

            w.write(_stt_pack(_STT_FRAME_HELLO, hello))

            await w.drain()

            self.connected = True

            log.info(
                "STT FORWARD open conn_id=%s -> %s:%d",
                self.conn_id,
                self.host,
                self.port,
            )

        except Exception as exc:

            self.disabled = True

            log.warning(
                "STT FORWARD open failed conn_id=%s err=%r",
                self.conn_id,
                exc,
            )

    def forward_audio(self, pcm_bytes: bytes):

        if (
            self.disabled
            or not self.connected
            or self.writer is None
        ):
            return

        try:

            self.writer.write(
                _stt_pack(_STT_FRAME_AUDIO, pcm_bytes)
            )

        except Exception as exc:

            self.disabled = True

            log.warning(
                "STT FORWARD write failed conn_id=%s err=%r",
                self.conn_id,
                exc,
            )

    async def close(self):

        if self.writer is None:
            return

        try:

            try:

                self.writer.write(
                    _stt_pack(_STT_FRAME_BYE, b"")
                )

                await self.writer.drain()

            except Exception:
                pass

            self.writer.close()

            try:
                await self.writer.wait_closed()
            except Exception:
                pass

        finally:

            self.writer = None
            self.connected = False

# ---------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------


def _fmt_t(cp: dict, key: str) -> str:

    v = cp.get(key)

    return f"{v:.3f}s" if v is not None else "—"


def _fmt_delta(cp: dict, a: str, b: str) -> str:

    va = cp.get(a)
    vb = cp.get(b)

    if va is None or vb is None:
        return "—"

    return f"+{(vb - va) * 1000:.1f}ms"


# ---------------------------------------------------------------------
# CONNECTION HANDLER
# ---------------------------------------------------------------------


async def handle_connection(websocket, path: str, args):

    import websockets

    fs_uuid = ""
    caller_number = ""

    try:

        from urllib.parse import urlsplit

        sp = urlsplit(path or "")

        parts = (sp.path or "").lstrip("/").split("/")

        seg = parts[0].strip() if parts else ""

        if (
            seg
            and 8 <= len(seg) <= 80
            and all(
                (c.isalnum() or c in "-_.")
                for c in seg
            )
        ):
            fs_uuid = seg

        # Second segment carries the caller number (digits only, set by
        # socket_server.py from Caller-Caller-ID-Number header).
        if len(parts) > 1 and parts[1].strip().isdigit():
            caller_number = parts[1].strip()

    except Exception:

        fs_uuid = ""
        caller_number = ""

    conn_id = fs_uuid if fs_uuid else uuidlib.uuid4().hex[:8]

    t0 = time.monotonic()

    cp = {"T0_ws_open": 0.0}

    counters = {
        "binary_frames": 0,
        "binary_bytes": 0,
        "text_frames": 0,
        "stalls": 0,
        "max_gap_ms": 0.0,
    }

    log.info(
        "=== WS CONNECT conn_id=%s path=%s ===",
        conn_id,
        path,
    )
    log.info("[CALL_CONNECTED] conn_id=%s WebSocket opened from FreeSWITCH", conn_id)

    # --------------------------------------------------------------
    # START AI SESSION
    # --------------------------------------------------------------

    try:

        await call_bridge.get_or_create(
            conn_id,
            TARGET_SR,
            caller_number=caller_number,
        )

        log.info("[CALL_SESSION_CREATED] conn_id=%s pipeline started", conn_id)
        log.info(
            "[CALL_BRIDGE] session created conn=%s",
            conn_id,
        )

    except Exception as exc:

        log.error(
            "[CALL_BRIDGE] failed conn=%s err=%r",
            conn_id,
            exc,
        )

        return

    # --------------------------------------------------------------
    # DEBUG CAPTURE
    # --------------------------------------------------------------

    p15_debug = _Phase15AudioDebugger(
        conn_id,
        TARGET_SR,
    )

    resample_state = None

    # --------------------------------------------------------------
    # OPTIONAL STT FORWARDER
    # --------------------------------------------------------------

    stt_fwd = None

    if STT_FORWARD_HOST:

        stt_fwd = _STTForwarder(
            conn_id=conn_id,
            sample_rate=TARGET_SR,
            host=STT_FORWARD_HOST,
            port=STT_FORWARD_PORT,
        )

        try:
            await stt_fwd.open()
        except Exception:
            stt_fwd = None

    last_binary_t = None
    # One-shot flag: fires signal_call_active() on the very first binary
    # audio frame. Greeting is NOT generated at WebSocket connect — only
    # when real audio arrives, confirming a live call.
    _call_active_signaled = False

    try:

        async for msg in websocket:

            try:

                now_abs = time.monotonic()

                now = now_abs - t0

                raw_bytes = b""

                # --------------------------------------------------
                # BINARY FRAMES
                # --------------------------------------------------

                if isinstance(msg, (bytes, bytearray)):

                    raw_bytes = bytes(msg)

                # --------------------------------------------------
                # TEXT PAYLOADS
                # --------------------------------------------------

                else:

                    counters["text_frames"] += 1

                    try:

                        text_str = str(msg)

                        if text_str.startswith("{"):

                            payload = json.loads(text_str)

                            if (
                                "event" in payload
                                and payload["event"] == "media"
                            ):

                                raw_bytes = base64.b64decode(
                                    payload["media"]["payload"]
                                )

                    except Exception:
                        pass

                # --------------------------------------------------
                # IGNORE EMPTY
                # --------------------------------------------------

                if not raw_bytes:
                    continue

                size = len(raw_bytes)

                preview = raw_bytes[:8].hex()

                log.debug(
                    "[WS_FRAME] conn=%s bytes=%d preview=%s",
                    conn_id,
                    size,
                    preview,
                )

                counters["binary_frames"] += 1
                counters["binary_bytes"] += size

                if "T1_first_binary" not in cp:

                    cp["T1_first_binary"] = now

                cp["T1_last_binary"] = now

                if last_binary_t is not None:

                    gap_ms = (
                        (now - last_binary_t) * 1000.0
                    )

                    if gap_ms > counters["max_gap_ms"]:
                        counters["max_gap_ms"] = gap_ms

                    if gap_ms > GAP_STALL_MS:
                        counters["stalls"] += 1

                last_binary_t = now

                # --------------------------------------------------
                # DEBUG SAVE
                # --------------------------------------------------

                p15_debug.process_frame(raw_bytes)

                # --------------------------------------------------
                # NORMALIZATION
                # --------------------------------------------------

                norm_msg = raw_bytes

                try:

                    pre_rms = -1

                    if (
                        len(norm_msg) >= 2
                        and len(norm_msg) % 2 == 0
                    ):

                        try:
                            pre_rms = audioop.rms(
                                norm_msg,
                                2,
                            )
                        except Exception:
                            pass

                    log.debug(
                        "[DECODE_PRE] conn=%s bytes=%d rms=%d codec=%s",
                        conn_id,
                        len(norm_msg),
                        pre_rms,
                        INPUT_CODEC,
                    )

                    performed_ulaw_decode = False

                    # ------------------------------------------
                    # DECODE ONLY TRUE 160B PCMU
                    # ------------------------------------------

                    if INPUT_CODEC == "pcmu":

                        if len(norm_msg) == 160:

                            norm_msg = audioop.ulaw2lin(
                                norm_msg,
                                2,
                            )

                            performed_ulaw_decode = True

                            log.debug(
                                "[ULAW_DECODE] conn=%s decoded 160B PCMU -> %dB PCM16",
                                conn_id,
                                len(norm_msg),
                            )

                        else:

                            log.debug(
                                "[ULAW_BYPASS] conn=%s frame=%dB assumed PCM16",
                                conn_id,
                                len(norm_msg),
                            )

                    # ------------------------------------------
                    # VALIDATE PCM
                    # ------------------------------------------

                    if len(norm_msg) == 0:

                        log.warning(
                            "[AUDIO_DROP] empty frame conn=%s",
                            conn_id,
                        )

                        continue

                    if len(norm_msg) % 2 != 0:

                        log.warning(
                            "[AUDIO_DROP] odd pcm bytes=%d conn=%s",
                            len(norm_msg),
                            conn_id,
                        )

                        continue

                    # ------------------------------------------
                    # RESAMPLE
                    # ------------------------------------------

                    if TARGET_SR != args.sample_rate:

                        before_resample = len(norm_msg)

                        norm_msg, resample_state = (
                            audioop.ratecv(
                                norm_msg,
                                2,
                                1,
                                args.sample_rate,
                                TARGET_SR,
                                resample_state,
                            )
                        )

                        log.info(
                            "[RESAMPLE] conn=%s %dB -> %dB",
                            conn_id,
                            before_resample,
                            len(norm_msg),
                        )

                    # ------------------------------------------
                    # FINAL RMS
                    # ------------------------------------------

                    final_rms = audioop.rms(
                        norm_msg,
                        2,
                    )

                    log.debug(
                        "[PCM_LEVEL] conn=%s rms=%d bytes=%d",
                        conn_id,
                        final_rms,
                        len(norm_msg),
                    )

                    # ------------------------------------------
                    # STT FORWARD
                    # ------------------------------------------

                    if stt_fwd is not None:

                        stt_fwd.forward_audio(norm_msg)

                    # ------------------------------------------
                    # SIGNAL CALL ACTIVE (first frame only)
                    # ------------------------------------------

                    if not _call_active_signaled:
                        _call_active_signaled = True
                        log.info("[CALL_ANSWERED] First audio frame received — signaling pipeline conn_id=%s", conn_id)
                        session = call_bridge.sessions.get(conn_id)
                        if session is not None:
                            asyncio.create_task(session.signal_call_active())
                        else:
                            log.warning("[CALL_ANSWERED] Session not found for conn_id=%s", conn_id)

                    # ------------------------------------------
                    # PUSH TO PIPELINE
                    # ------------------------------------------

                    call_bridge.push_audio(
                        conn_id,
                        norm_msg,
                    )

                    queued = 0

                    try:

                        queued = (
                            call_bridge.sessions[conn_id]
                            .source.queue.qsize()
                        )

                    except Exception:
                        pass

                    log.debug(
                        "[AUDIO_PIPE] conn=%s received=%d normalized=%d queued=%d rms=%d",
                        conn_id,
                        size,
                        len(norm_msg),
                        queued,
                        final_rms,
                    )

                except Exception as exc:

                    log.error(
                        "[NORMALIZATION_FATAL] conn=%s err=%r",
                        conn_id,
                        exc,
                    )

            except Exception as exc:

                log.error(
                    "[WS_FRAME_ERROR] conn=%s err=%r",
                    conn_id,
                    exc,
                )

    except websockets.exceptions.ConnectionClosed as exc:

        log.info(
            "[CALL_DISCONNECTED] conn_id=%s code=%s — FreeSWITCH WebSocket closed",
            conn_id,
            exc.code,
        )
        log.info(
            "[MEDIA_STREAM_CLOSED] conn=%s code=%s",
            conn_id,
            exc.code,
        )

    except Exception as exc:

        log.error(
            "[MEDIA_STREAM_FAILED] conn=%s err=%r",
            conn_id,
            exc,
        )

    finally:

        cp["T2_ws_close"] = time.monotonic() - t0

        p15_debug.close()

        if stt_fwd is not None:

            await stt_fwd.close()

        try:

            await call_bridge.remove(conn_id)

        except Exception:
            pass

        log.info(
            "[CALL_DISCONNECTED] conn_id=%s frames=%d bytes=%d — session ended",
            conn_id,
            counters["binary_frames"],
            counters["binary_bytes"],
        )
        log.info(
            "[SESSION_END] conn=%s frames=%d bytes=%d",
            conn_id,
            counters["binary_frames"],
            counters["binary_bytes"],
        )

# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------


async def main_async(args):

    import websockets

    log.info(
        "ws_audio_receiver listening on ws://%s:%d",
        args.host,
        args.port,
    )

    async def _entry(ws):

        path = getattr(ws, "path", None)

        if path is None and hasattr(ws, "request"):

            path = ws.request.path

        await handle_connection(
            ws,
            path,
            args,
        )

    async with websockets.serve(
        _entry,
        args.host,
        args.port,
        max_size=2**20,
        ping_interval=None,
    ):

        await asyncio.Future()


def main():

    p = argparse.ArgumentParser()

    p.add_argument(
        "--host",
        default="0.0.0.0",
    )

    p.add_argument(
        "--port",
        type=int,
        default=9001,
    )

    p.add_argument(
        "--sample-rate",
        type=int,
        default=8000,
    )

    p.add_argument(
        "--bytes-per-sample",
        type=int,
        default=2,
    )

    args = p.parse_args()

    try:

        asyncio.run(main_async(args))

    except KeyboardInterrupt:

        pass


if __name__ == "__main__":

    main()