"""Sarvam API key pool with automatic rotation on credit exhaustion.

Why this exists
---------------
A single Sarvam key runs out of credits and every call starts failing. This
module keeps a *pool* of keys and an *active* one in a small JSON file that is
shared across all processes (the telephony containers bind-mount ./data, so
they all see the same file). When a key is reported exhausted, the pool advances
to the next healthy key — so the system never goes dark as long as one key has
credit left.

Two layers cooperate:
  • Reactive (this module, called from the STT/TTS clients): on a credit/auth
    error (HTTP 402/403, or 429 used up), `report_exhausted()` rotates instantly
    and the client retries with the new key. This is the real guarantee.
  • Proactive (deploy/sarvam_rotator.py): a background service that keeps
    calling-agent/.env's SARVAM_API_KEY in sync with the active key (so freshly
    started processes pick it up) and re-seeds / heals the pool.

The store is dependency-free (stdlib only) so it imports cleanly from both the
repo root and from inside calling-agent/backend.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

try:
    import fcntl  # POSIX advisory locking for multi-process safety
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None

_REPO_ROOT = Path(__file__).resolve().parents[1]

STORE_PATH = Path(os.environ.get("SARVAM_KEYS_FILE", _REPO_ROOT / "data" / "sarvam_keys.json"))
ENV_PATH = Path(os.environ.get("SARVAM_ENV_FILE", _REPO_ROOT / "calling-agent" / ".env"))

# A key marked exhausted is retried again after this cooldown (credits may reset,
# e.g. monthly / on top-up). 0 disables retrying exhausted keys.
RETRY_AFTER_S = int(os.environ.get("SARVAM_KEY_RETRY_AFTER_S", str(24 * 3600)))


def _now() -> float:
    return time.time()


def _fingerprint(key: str) -> str:
    return f"{key[:6]}…{key[-4:]}" if key and len(key) >= 10 else "(none)"


# --------------------------------------------------------------------------- #
# Low-level locked read / modify / write
# --------------------------------------------------------------------------- #
def _read_locked(f) -> dict:
    if fcntl:
        fcntl.flock(f, fcntl.LOCK_EX)
    f.seek(0)
    raw = f.read()
    try:
        return json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return {}


def _write_unlocked(f, data: dict) -> None:
    f.seek(0)
    f.truncate()
    json.dump(data, f, indent=2)
    f.flush()
    os.fsync(f.fileno())


def _load() -> dict:
    if not STORE_PATH.exists():
        return {}
    try:
        with STORE_PATH.open("r") as f:
            if fcntl:
                fcntl.flock(f, fcntl.LOCK_SH)
            return json.loads(f.read() or "{}")
    except (OSError, json.JSONDecodeError):
        return {}


def _entry(key: str, status: str = "active") -> dict:
    return {"key": key, "status": status, "exhausted_at": None, "rotations": 0}


def _healthy(e: dict) -> bool:
    if e.get("status") == "active":
        return True
    # Exhausted but past cooldown -> treat as healthy again.
    if RETRY_AFTER_S and e.get("exhausted_at") and (_now() - e["exhausted_at"]) > RETRY_AFTER_S:
        return True
    return False


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def seed(keys: list[str], *, reset: bool = False) -> dict:
    """Create/extend the pool from a list of keys. Idempotent (won't duplicate)."""
    keys = [k.strip() for k in keys if k and k.strip()]
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with STORE_PATH.open("a+") as f:
        data = _read_locked(f)
        if reset or not data.get("keys"):
            data = {"keys": [_entry(k) for k in keys], "active": keys[0] if keys else None}
        else:
            existing = {e["key"] for e in data["keys"]}
            for k in keys:
                if k not in existing:
                    data["keys"].append(_entry(k))
            if not data.get("active") and data["keys"]:
                data["active"] = data["keys"][0]["key"]
        data["updated_at"] = _now()
        _write_unlocked(f, data)
    sync_env_file()
    return data


def active_key() -> str:
    """The key the clients should use right now.

    Falls back to the SARVAM_API_KEY env var when no pool exists, so single-key
    deployments behave exactly as before.
    """
    data = _load()
    active = data.get("active")
    if active:
        return active
    return os.environ.get("SARVAM_API_KEY", "")


def report_exhausted(key: str, reason: str = "credits") -> str | None:
    """Mark `key` exhausted and advance to the next healthy key.

    Returns the new active key, or None if every key is now exhausted.
    Safe to call concurrently from multiple processes (file-locked).
    """
    if not STORE_PATH.exists():
        # No pool configured — nothing to rotate to.
        return os.environ.get("SARVAM_API_KEY", "") or None
    with STORE_PATH.open("a+") as f:
        data = _read_locked(f)
        entries = data.get("keys", [])
        if not entries:
            return None
        idx = next((i for i, e in enumerate(entries) if e["key"] == key), None)
        # Only mark exhausted if the reported key is still the active one (avoids
        # a stale concurrent caller skipping a freshly-rotated good key).
        if idx is not None and data.get("active") == key:
            entries[idx]["status"] = "exhausted"
            entries[idx]["exhausted_at"] = _now()
            entries[idx]["rotations"] = entries[idx].get("rotations", 0) + 1
            entries[idx]["last_reason"] = reason

        # Pick the next healthy key, scanning from just after the exhausted one.
        start = (idx + 1) if idx is not None else 0
        order = entries[start:] + entries[:start]
        new_active = next((e["key"] for e in order if _healthy(e)), None)
        data["active"] = new_active or data.get("active")
        data["updated_at"] = _now()
        _write_unlocked(f, data)
    sync_env_file()
    return data.get("active")


def sync_env_file() -> bool:
    """Rewrite SARVAM_API_KEY=<active> in the .env so restarts use the live key."""
    active = active_key()
    if not active or not ENV_PATH.exists():
        return False
    try:
        lines = ENV_PATH.read_text().splitlines()
        out, found = [], False
        for ln in lines:
            if ln.strip().startswith("SARVAM_API_KEY="):
                out.append(f"SARVAM_API_KEY={active}")
                found = True
            else:
                out.append(ln)
        if not found:
            out.append(f"SARVAM_API_KEY={active}")
        ENV_PATH.write_text("\n".join(out) + "\n")
        return True
    except OSError:
        return False


def status() -> dict:
    """Human-readable pool status (keys are fingerprinted, never exposed raw)."""
    data = _load()
    entries = data.get("keys", [])
    healthy = [e for e in entries if _healthy(e)]
    return {
        "configured": bool(entries),
        "total": len(entries),
        "healthy": len(healthy),
        "exhausted": len(entries) - len(healthy),
        "active": _fingerprint(data.get("active") or active_key()),
        "keys": [
            {"key": _fingerprint(e["key"]), "status": "active" if _healthy(e) else "exhausted",
             "exhausted_at": e.get("exhausted_at"), "rotations": e.get("rotations", 0)}
            for e in entries
        ],
        "store": str(STORE_PATH),
        "updated_at": data.get("updated_at"),
    }
