#!/usr/bin/env python3
"""
Verify Sarvam AI API key and connectivity.

Usage:
    cd calling-agent
    python verify_sarvam.py

Checks:
  1. SARVAM_API_KEY exists in environment (loaded from .env)
  2. Prints masked key (first 6 + last 4 chars)
  3. Makes a minimal POST to the STT endpoint with 1s of silence
  4. Reports HTTP status and whether auth is accepted
"""
from __future__ import annotations

import os
import struct
import sys
import wave
import io

# Load .env from calling-agent directory
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass  # dotenv optional — rely on shell environment

import httpx


SARVAM_STT_URL = "https://api.sarvam.ai/speech-to-text"
SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech/stream"


def _mask(key: str) -> str:
    if len(key) <= 10:
        return "***"
    return key[:6] + "…" + key[-4:]


def _make_silence_wav(duration_s: float = 0.5, sample_rate: int = 16000) -> bytes:
    """Return a minimal WAV containing silence (PCM16 mono)."""
    n_samples = int(sample_rate * duration_s)
    pcm = bytes(n_samples * 2)  # 16-bit = 2 bytes per sample

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def main() -> None:
    # ── 1. Key presence check ────────────────────────────────────────────────
    key = os.environ.get("SARVAM_API_KEY", "").strip()
    print("─" * 60)
    if not key:
        print("FAIL  SARVAM_API_KEY: not set")
        print()
        print("Fix: add the following line to calling-agent/.env")
        print("  SARVAM_API_KEY=sk_<your-key>")
        sys.exit(1)

    print(f"OK    SARVAM_API_KEY: {_mask(key)}")

    # ── 2. STT connectivity check ────────────────────────────────────────────
    print()
    print("Testing STT endpoint …")
    wav_data = _make_silence_wav()
    try:
        resp = httpx.post(
            SARVAM_STT_URL,
            headers={"api-subscription-key": key},
            data={"language_code": "en-IN", "model": "saarika:v2.5"},
            files={"file": ("silence.wav", wav_data, "audio/wav")},
            timeout=10.0,
        )
        status = resp.status_code
        if status == 200:
            transcript = resp.json().get("transcript", "")
            print(f"OK    STT HTTP {status} — transcript: {transcript!r}")
        elif status == 403:
            print(f"FAIL  STT HTTP {status} — invalid or expired API key")
            print(f"      Response: {resp.text[:300]}")
            sys.exit(1)
        elif status == 429:
            print(f"WARN  STT HTTP {status} — rate limited (key is valid)")
        else:
            print(f"WARN  STT HTTP {status} — {resp.text[:200]}")
    except httpx.TimeoutException:
        print("FAIL  STT request timed out")
        sys.exit(1)
    except httpx.ConnectError as exc:
        print(f"FAIL  STT connect error: {exc}")
        sys.exit(1)

    # ── 3. TTS connectivity check ────────────────────────────────────────────
    print()
    print("Testing TTS endpoint …")
    try:
        with httpx.stream(
            "POST",
            SARVAM_TTS_URL,
            headers={
                "api-subscription-key": key,
                "Content-Type": "application/json",
            },
            json={
                "text": "Hello.",
                "target_language_code": "en-IN",
                "speaker": "simran",
                "model": "bulbul:v2",
                "speech_sample_rate": 16000,
                "output_audio_codec": "linear16",
            },
            timeout=10.0,
        ) as resp:
            status = resp.status_code
            first_bytes = b""
            for chunk in resp.iter_bytes(256):
                first_bytes = chunk
                break
            if status == 200:
                print(f"OK    TTS HTTP {status} — received {len(first_bytes)} bytes of audio")
            elif status == 403:
                body = resp.read()
                print(f"FAIL  TTS HTTP {status} — invalid or expired API key")
                print(f"      Response: {body[:300]}")
                sys.exit(1)
            elif status == 429:
                print(f"WARN  TTS HTTP {status} — rate limited (key is valid)")
            else:
                body = resp.read()
                print(f"WARN  TTS HTTP {status} — {body[:200]}")
    except httpx.TimeoutException:
        print("FAIL  TTS request timed out")
        sys.exit(1)
    except httpx.ConnectError as exc:
        print(f"FAIL  TTS connect error: {exc}")
        sys.exit(1)

    print()
    print("─" * 60)
    print("All checks passed.")


if __name__ == "__main__":
    main()
