#!/usr/bin/env python3
"""
Sarvam usage monitor.

Standalone utility — does NOT modify any pipeline files.
Usage data is persisted to a JSON file and can be printed at any time.

Run directly:
    python scripts/sarvam_credits.py

Import in other code (optional, non-breaking):
    from scripts.sarvam_credits import tracker
    tracker.record_stt(audio_seconds=1.2, latency_ms=340, failed=False)
    tracker.record_tts(chars=120, latency_ms=210, failed=False)
"""

import json
import os
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
_DEFAULT_STORE = Path(__file__).parent / ".sarvam_usage.json"
STORE_PATH = Path(os.environ.get("SARVAM_USAGE_FILE", _DEFAULT_STORE))

_EMPTY: dict = {
    "stt_requests": 0,
    "stt_audio_seconds": 0.0,
    "stt_failures": 0,
    "stt_total_latency_ms": 0.0,
    "tts_requests": 0,
    "tts_characters": 0,
    "tts_failures": 0,
    "tts_total_latency_ms": 0.0,
    "first_seen": None,
    "last_seen": None,
}


def _load() -> dict:
    try:
        with STORE_PATH.open() as f:
            data = json.load(f)
        # Backfill keys added in later versions
        for k, v in _EMPTY.items():
            data.setdefault(k, v)
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(_EMPTY)


def _save(data: dict) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE_PATH.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(data, f, indent=2)
    tmp.replace(STORE_PATH)


# ---------------------------------------------------------------------------
# Tracker class (importable, thread-safe enough for single-process use)
# ---------------------------------------------------------------------------
class SarvamUsageTracker:
    def __init__(self, store_path: Path = STORE_PATH):
        self._path = store_path

    def _load(self) -> dict:
        global STORE_PATH
        old = STORE_PATH
        STORE_PATH = self._path
        d = _load()
        STORE_PATH = old
        return d

    def _save(self, data: dict) -> None:
        global STORE_PATH
        old = STORE_PATH
        STORE_PATH = self._path
        _save(data)
        STORE_PATH = old

    def record_stt(self, audio_seconds: float = 0.0, latency_ms: float = 0.0, failed: bool = False) -> None:
        d = self._load()
        d["stt_requests"] += 1
        d["stt_audio_seconds"] += audio_seconds
        d["stt_total_latency_ms"] += latency_ms
        if failed:
            d["stt_failures"] += 1
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        d["last_seen"] = now
        if d["first_seen"] is None:
            d["first_seen"] = now
        self._save(d)

    def record_tts(self, chars: int = 0, latency_ms: float = 0.0, failed: bool = False) -> None:
        d = self._load()
        d["tts_requests"] += 1
        d["tts_characters"] += chars
        d["tts_total_latency_ms"] += latency_ms
        if failed:
            d["tts_failures"] += 1
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        d["last_seen"] = now
        if d["first_seen"] is None:
            d["first_seen"] = now
        self._save(d)

    def reset(self) -> None:
        _save(dict(_EMPTY))

    def stats(self) -> dict:
        return self._load()


tracker = SarvamUsageTracker()


# ---------------------------------------------------------------------------
# Credits endpoint check
# ---------------------------------------------------------------------------
def _check_credits_api(api_key: str) -> str | None:
    """Returns balance string if Sarvam exposes a credits endpoint, else None."""
    try:
        import urllib.request
        import urllib.error

        for url in [
            "https://api.sarvam.ai/credits",
            "https://api.sarvam.ai/v1/credits",
            "https://api.sarvam.ai/usage",
            "https://api.sarvam.ai/quota",
            "https://api.sarvam.ai/account",
        ]:
            req = urllib.request.Request(url, headers={"api-subscription-key": api_key})
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status == 200:
                        body = json.loads(resp.read())
                        for field in ("credits", "balance", "remaining", "quota", "available"):
                            if field in body:
                                return str(body[field])
                        return json.dumps(body)
            except urllib.error.HTTPError as e:
                if e.code not in (401, 403, 404, 405, 422):
                    return None
            except Exception:
                return None
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------
def print_report(api_key: str | None = None, *, label: str = "SARVAM-USAGE") -> None:
    key = api_key or os.environ.get("SARVAM_API_KEY", "")
    fingerprint = f"...{key[-6:]}" if len(key) >= 6 else "(no key)"

    credits_str: str | None = None
    if key:
        credits_str = _check_credits_api(key)

    d = _load()

    stt_avg = (
        d["stt_total_latency_ms"] / d["stt_requests"] if d["stt_requests"] else 0
    )
    tts_avg = (
        d["tts_total_latency_ms"] / d["tts_requests"] if d["tts_requests"] else 0
    )

    lines = [f"[{label}]"]
    lines.append(f"Key:              {fingerprint}")

    if credits_str is not None:
        lines.append(f"Credits:          {credits_str}")
    else:
        lines.append("Credits:          endpoint unavailable (Sarvam does not expose credits API)")

    lines.append(f"STT Requests:     {d['stt_requests']}  (failures: {d['stt_failures']})")
    lines.append(f"STT Audio:        {d['stt_audio_seconds']:.1f}s")
    lines.append(f"STT Avg Latency:  {stt_avg:.0f}ms")
    lines.append(f"TTS Requests:     {d['tts_requests']}  (failures: {d['tts_failures']})")
    lines.append(f"TTS Characters:   {d['tts_characters']}")
    lines.append(f"TTS Avg Latency:  {tts_avg:.0f}ms")

    if d["first_seen"]:
        lines.append(f"Tracking since:   {d['first_seen']}")
    if d["last_seen"]:
        lines.append(f"Last activity:    {d['last_seen']}")

    lines.append(f"Data file:        {STORE_PATH}")

    print("\n".join(lines))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Sarvam usage monitor")
    ap.add_argument("--reset", action="store_true", help="Clear all counters")
    ap.add_argument("--key", default=None, help="Sarvam API key (overrides env)")
    ap.add_argument(
        "--record-stt",
        nargs=3,
        metavar=("SECONDS", "LATENCY_MS", "FAILED"),
        help="Manually record an STT call (for testing)",
    )
    ap.add_argument(
        "--record-tts",
        nargs=3,
        metavar=("CHARS", "LATENCY_MS", "FAILED"),
        help="Manually record a TTS call (for testing)",
    )
    args = ap.parse_args()

    if args.reset:
        _save(dict(_EMPTY))
        print("Usage counters reset.")
        sys.exit(0)

    if args.record_stt:
        secs, lat, fail = args.record_stt
        tracker.record_stt(float(secs), float(lat), fail.lower() in ("1", "true", "yes"))
        print("STT call recorded.")

    if args.record_tts:
        chars, lat, fail = args.record_tts
        tracker.record_tts(int(chars), float(lat), fail.lower() in ("1", "true", "yes"))
        print("TTS call recorded.")

    print_report(args.key)
