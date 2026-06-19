#!/usr/bin/env python3
"""Sarvam key rotator — background service.

Runs forever (as a docker compose service or a systemd unit). Its jobs:

  1. Seed the key pool (data/sarvam_keys.json) from SARVAM_API_KEYS the first
     time it starts, so every process shares one pool.
  2. Keep calling-agent/.env's SARVAM_API_KEY in sync with the pool's active
     key, so any process started/restarted later picks up the live key.
  3. Heal the pool: if the active key was exhausted by the STT/TTS clients
     (reactive rotation), make sure a healthy key is promoted to active.
  4. Optionally probe the active key against Sarvam's credits endpoint
     (SARVAM_PROBE=1) and rotate proactively if it reports no credit. Sarvam
     historically does not expose a reliable credits API, so the real guarantee
     is the reactive rotation in the clients — this is best-effort on top.

The actual "never run out" guarantee comes from the clients calling
shared.sarvam_keys.report_exhausted() the instant a key returns 401/402/403.
This service makes that durable across restarts and surfaces pool health.
"""
from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import sarvam_keys  # noqa: E402

INTERVAL_S = int(os.environ.get("SARVAM_ROTATOR_INTERVAL_S", "30"))
PROBE = os.environ.get("SARVAM_PROBE", "0") == "1"

_running = True


def _stop(*_):
    global _running
    _running = False


def _keys_from_env() -> list[str]:
    raw = os.environ.get("SARVAM_API_KEYS", "")
    keys = [k.strip() for k in raw.replace("\n", ",").split(",") if k.strip()]
    # Fall back to the single active key if the list var isn't set.
    single = os.environ.get("SARVAM_API_KEY", "").strip()
    if single and single not in keys:
        keys.append(single)
    return keys


def _probe_active() -> None:
    """Best-effort: rotate if Sarvam clearly reports the active key has no credit."""
    if not PROBE:
        return
    try:
        import urllib.request
        import urllib.error
        import json
        key = sarvam_keys.active_key()
        if not key:
            return
        for url in ("https://api.sarvam.ai/credits", "https://api.sarvam.ai/usage",
                    "https://api.sarvam.ai/quota"):
            try:
                req = urllib.request.Request(url, headers={"api-subscription-key": key})
                with urllib.request.urlopen(req, timeout=6) as resp:
                    body = json.loads(resp.read())
                    for f in ("credits", "balance", "remaining", "available"):
                        if f in body:
                            try:
                                if float(body[f]) <= 0:
                                    sarvam_keys.report_exhausted(key, reason="probe_zero")
                            except (TypeError, ValueError):
                                pass
                            return
                    return
            except urllib.error.HTTPError as e:
                if e.code in (401, 402, 403):
                    sarvam_keys.report_exhausted(key, reason=f"probe_{e.code}")
                    return
            except Exception:
                continue
    except Exception:
        pass


def main() -> int:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    keys = _keys_from_env()
    if keys:
        sarvam_keys.seed(keys)  # idempotent; won't duplicate existing pool entries

    st = sarvam_keys.status()
    print(f"[sarvam-rotator] started: {st['healthy']}/{st['total']} keys healthy, "
          f"active={st['active']}, interval={INTERVAL_S}s probe={PROBE}", flush=True)

    last_active = None
    while _running:
        # 1) Heal: if the active key is exhausted, promote the next healthy one.
        cur = sarvam_keys.active_key()
        st = sarvam_keys.status()
        active_entry = next((k for k in st["keys"] if k["key"] == sarvam_keys._fingerprint(cur)), None)
        if active_entry and active_entry["status"] == "exhausted":
            sarvam_keys.report_exhausted(cur, reason="heal")

        # 2) Optional proactive probe.
        _probe_active()

        # 3) Keep the .env in sync with the live active key.
        sarvam_keys.sync_env_file()

        st = sarvam_keys.status()
        if st["active"] != last_active:
            print(f"[sarvam-rotator] active key -> {st['active']} "
                  f"({st['healthy']}/{st['total']} healthy)", flush=True)
            last_active = st["active"]
        if st["healthy"] == 0 and st["total"]:
            print("[sarvam-rotator] WARNING: ALL Sarvam keys exhausted — add more keys "
                  "to SARVAM_API_KEYS!", flush=True)

        for _ in range(INTERVAL_S):
            if not _running:
                break
            time.sleep(1)

    print("[sarvam-rotator] stopped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
