#!/usr/bin/env python3
"""Generate the voice-preview samples served at /api/voices/sample/<speaker>.

One short bilingual line per Bulbul v3 speaker the platform offers, written
to data/voice_samples/<speaker>.wav. Run once (and re-run only when Sarvam
adds a speaker or the sample script changes) — each run costs real TTS
credits (~₹2 for the full set):

    cd /home/angelo/lescall && python3 deploy/generate_voice_samples.py

The speaker list mirrors _MALE_VOICES/_FEMALE_VOICES in
dashboard/agent_copilot_api.py — keep them in sync.
"""
import base64
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "data" / "voice_samples"

# speaker -> (gender, sample line). Curated bulbul:v3 subset the copilot
# offers (the full v3 roster is 37 voices — see voice_catalog.py). Hindi verb
# forms are gendered — दूँगा (m) vs दूँगी (f) — so the line is per-gender.
VOICES = {
    "shubh":  ("male",   "Hello! I'm Shubh. नमस्ते, आपकी कॉल्स पर मैं ऐसा सुनाई दूँगा।"),
    "aditya": ("male",   "Hello! I'm Aditya. नमस्ते, आपकी कॉल्स पर मैं ऐसा सुनाई दूँगा।"),
    "amit":   ("male",   "Hello! I'm Amit. नमस्ते, आपकी कॉल्स पर मैं ऐसा सुनाई दूँगा।"),
    "kabir":  ("male",   "Hello! I'm Kabir. नमस्ते, आपकी कॉल्स पर मैं ऐसा सुनाई दूँगा।"),
    "simran": ("female", "Hello! I'm Simran. नमस्ते, आपकी कॉल्स पर मैं ऐसी सुनाई दूँगी।"),
    "priya":  ("female", "Hello! I'm Priya. नमस्ते, आपकी कॉल्स पर मैं ऐसी सुनाई दूँगी।"),
    "neha":   ("female", "Hello! I'm Neha. नमस्ते, आपकी कॉल्स पर मैं ऐसी सुनाई दूँगी।"),
    "shruti": ("female", "Hello! I'm Shruti. नमस्ते, आपकी कॉल्स पर मैं ऐसी सुनाई दूँगी।"),
}


# ElevenLabs roster (2026-08-07 provider switch): voice_id -> (name, gender,
# sample line). Mirrors voice_catalog.EL_VOICE_CATALOG — keep in sync. WAVs
# land as el_<name>.wav (voice_ids are not filename material) and index.json
# carries provider + file so /api/voices can tell the rosters apart.
EL_VOICES = {
    "JvXnN4vCCagzhSTjMMjl": ("shivank", "male",
        "Hello! I'm Shivank. नमस्ते, आपकी कॉल्स पर मैं ऐसा सुनाई दूँगा।"),
    "EVsUc9VDoheXQmgm2NpK": ("aryaman", "male",
        "Hello! I'm Aryaman. नमस्ते, आपकी कॉल्स पर मैं ऐसा सुनाई दूँगा।"),
    "d2osXrwa36lUEkuaO4sP": ("maahir", "male",
        "Hello! I'm Maahir. नमस्ते, आपकी कॉल्स पर मैं ऐसा सुनाई दूँगा।"),
    "mnpQAOH9h8vn2tVUYJWD": ("roohi", "female",
        "Hello! I'm Roohi. नमस्ते, आपकी कॉल्स पर मैं ऐसी सुनाई दूँगी।"),
    "Em37RvLnPRGl6d9Suxcj": ("samhita", "female",
        "Hello! I'm Samhita. नमस्ते, आपकी कॉल्स पर मैं ऐसी सुनाई दूँगी।"),
    "Wu4iEB9NxSm711OvcrLo": ("mohini", "female",
        "Hello! I'm Mohini. नमस्ते, आपकी कॉल्स पर मैं ऐसी सुनाई दूँगी।"),
}


def _api_key() -> str:
    key = os.environ.get("SARVAM_API_KEY", "").strip()
    if not key:
        env = REPO / "calling-agent" / ".env"
        m = re.search(r"^SARVAM_API_KEY=(\S+)", env.read_text(), re.M)
        key = m.group(1) if m else ""
    if not key:
        sys.exit("SARVAM_API_KEY not found (env or calling-agent/.env)")
    return key


def _el_api_key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        env = REPO / "calling-agent" / ".env"
        m = re.search(r"^ELEVENLABS_API_KEY=(\S+)", env.read_text(), re.M)
        key = m.group(1) if m else ""
    return key  # empty = skip the EL section (Sarvam set still generates)


def _pcm_to_wav(pcm: bytes, sr: int = 16000) -> bytes:
    import struct
    wav = struct.pack("<4sI4s", b"RIFF", 36 + len(pcm), b"WAVE")
    wav += struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, 1, sr, sr * 2, 2, 16)
    return wav + struct.pack("<4sI", b"data", len(pcm)) + pcm


def _generate_el(index: list) -> None:
    key = _el_api_key()
    if not key:
        print("  (ELEVENLABS_API_KEY not set — skipping EL samples)")
        return
    for voice_id, (name, gender, text) in EL_VOICES.items():
        stem = f"el_{name}"
        out = OUT_DIR / f"{stem}.wav"
        index.append({"speaker": voice_id, "name": name.capitalize(),
                      "gender": gender, "provider": "elevenlabs",
                      "file": stem})
        if out.exists() and out.stat().st_size > 10_000:
            print(f"  {stem}: exists ({out.stat().st_size}b) — skipping")
            continue
        req = urllib.request.Request(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
            "?output_format=pcm_16000",
            data=json.dumps({
                "text": text,
                "model_id": "eleven_flash_v2_5",
                "language_code": "hi",
            }).encode(),
            headers={"xi-api-key": key, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            pcm = resp.read()
        out.write_bytes(_pcm_to_wav(pcm))
        print(f"  {stem} ({gender}): {len(pcm)} bytes PCM -> {out}")


def main() -> None:
    key = _api_key()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for speaker, (gender, text) in VOICES.items():
        out = OUT_DIR / f"{speaker}.wav"
        if out.exists() and out.stat().st_size > 10_000:
            print(f"  {speaker}: exists ({out.stat().st_size}b) — skipping")
            continue
        req = urllib.request.Request(
            "https://api.sarvam.ai/text-to-speech",
            data=json.dumps({
                "text": text,
                "target_language_code": "hi-IN",  # codemix handles the English lead-in
                "speaker": speaker,
                "model": "bulbul:v3",
                "pace": 1.0,
                "speech_sample_rate": 16000,
            }).encode(),
            headers={"api-subscription-key": key,
                     "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
        wav = base64.b64decode(body["audios"][0])
        out.write_bytes(wav)
        print(f"  {speaker} ({gender}): {len(wav)} bytes -> {out}")
    # index.json is what /api/voices serves — Sarvam entries first (curated
    # order above), then the ElevenLabs roster with provider + file fields.
    index = [{"speaker": s, "gender": g, "provider": "sarvam", "file": s}
             for s, (g, _) in VOICES.items()]
    _generate_el(index)
    (OUT_DIR / "index.json").write_text(json.dumps(index, indent=2))
    print(f"  index.json -> {OUT_DIR / 'index.json'}")


if __name__ == "__main__":
    main()
