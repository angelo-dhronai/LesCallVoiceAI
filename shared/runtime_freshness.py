"""Voice-runtime code freshness — closes the stale-process failure class.

The failure (hit 3× on 2026-08-07 during the ElevenLabs switch): agent YAML
and .env flags hot-reload, but CODE loads once per container boot. Between a
repo change and the next `docker restart lescall-runtime`, saved config can
mean something different to the runtime than to the dashboard that wrote it —
e.g. `speaker: aryaman` written correctly by the copilot while the running
bridge predated the name resolver, so every call silently spoke the default
voice. Nothing anywhere said "the runtime is behind".

Now it says so. The receiver stamps its boot; this module compares that
stamp against the newest .py under everything the runtime imports and
reports skew. Consumers: /api/health (dashboard + sentinel) and the copilot
write digest (a user changing an agent while the runtime is stale is told
their change may not sound until a restart).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
BOOT_STAMP = _REPO / "phase14_runtime" / "logs" / "runtime_boot.json"

# Everything the telephony runtime imports: the top-level bridge files plus
# the whole backend app tree. deploy/, tests/, dashboard/ are irrelevant to
# the call path and excluded so a dashboard-only edit never flags the runtime.
_TOP_LEVEL = ("ws_audio_receiver.py", "conversation_call_bridge.py",
              "socket_server.py", "call_audit.py")
_APP_TREE = _REPO / "calling-agent" / "backend" / "app"
_SHARED_TREE = _REPO / "shared"

# Grace so the stamp written moments after the newest file (deploy + instant
# restart) never reads as stale.
_GRACE_S = 5.0


def write_boot_stamp() -> None:
    """Called by ws_audio_receiver at process start. Never raises."""
    try:
        BOOT_STAMP.parent.mkdir(parents=True, exist_ok=True)
        tmp = BOOT_STAMP.with_suffix(".tmp")
        tmp.write_text(json.dumps({"boot_ts": time.time()}))
        tmp.replace(BOOT_STAMP)
    except Exception:
        pass


def _newest_code() -> tuple[float, str]:
    newest, which = 0.0, ""
    try:
        for name in _TOP_LEVEL:
            p = _REPO / name
            if p.exists():
                m = p.stat().st_mtime
                if m > newest:
                    newest, which = m, name
        for tree in (_APP_TREE, _SHARED_TREE):
            for p in tree.rglob("*.py"):
                if "__pycache__" in p.parts:
                    continue
                m = p.stat().st_mtime
                if m > newest:
                    newest, which = m, str(p.relative_to(_REPO))
    except Exception:
        pass
    return newest, which


def voice_runtime_freshness() -> dict:
    """{running, boot_at, newest_code_change, newest_file, code_stale}.

    code_stale=True means: a .py the runtime imports changed AFTER the
    runtime booted — saved config may not behave as the current code
    intends until `docker restart lescall-runtime`.
    """
    boot_ts = 0.0
    try:
        boot_ts = float(json.loads(BOOT_STAMP.read_text()).get("boot_ts", 0))
    except Exception:
        pass
    newest, which = _newest_code()
    out = {
        "running": bool(boot_ts),
        "boot_at": boot_ts,
        "newest_code_change": newest,
        "newest_file": which,
        "code_stale": bool(boot_ts) and newest > boot_ts + _GRACE_S,
    }
    if out["code_stale"]:
        out["stale_for_s"] = round(newest - boot_ts)
    return out
