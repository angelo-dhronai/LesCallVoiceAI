"""
runtime_guard.py — shared startup safety for the LesCall telephony runtime.

Both long-running services (socket_server.py under system Python and
ws_audio_receiver.py under the venv) import this and call the two guards at
the very top of main(). It centralizes two invariants whose violation has,
historically, taken the whole platform down:

  1. NEVER run as root.
     A root-started process creates root-owned files in the shared runtime
     dirs (/tmp/phase15_audio_debug, /tmp/esl_recordings, ...). The next
     normal run — as the service user — then can't write them, raising
     PermissionError on every audio frame, which makes the agent go deaf for
     the entire call. A root process also can't be killed by the user-owned
     stop_all.sh, leaving a half-dead runtime. We refuse to start as root,
     loudly, so this class of failure can never recur.

  2. Shared runtime dirs must exist and be writable by EVERY participant —
     this process *and* the freeswitch user (uid 999) who writes recordings
     and reads playback WAVs. We (re)create them all up front with 0o777 so
     ownership can never silently block I/O regardless of who ran first.

Both functions are dependency-free and safe to call unconditionally.
"""

from __future__ import annotations

import os
import sys

# Every ephemeral dir a runtime component (this process OR freeswitch) writes
# to or reads from during a call. Kept here as the single source of truth.
RUNTIME_DIRS: tuple[str, ...] = (
    "/tmp/phase15_audio_debug",   # ws_audio_receiver raw-audio debug dumps
    "/tmp/esl_recordings",        # freeswitch-side ESL record_session WAVs
    "/tmp/ai_telephony_playback", # TTS playback WAVs handed to freeswitch
)


def assert_not_root(service_name: str = "service") -> None:
    """Abort immediately if running as root. See module docstring for why."""
    geteuid = getattr(os, "geteuid", None)
    if geteuid is not None and geteuid() == 0:
        sys.stderr.write(
            f"FATAL [{service_name}]: refusing to start as root (euid=0).\n"
            "  A root-owned process poisons shared runtime file ownership and\n"
            "  cannot be stopped by the user-owned scripts, which has taken the\n"
            "  platform down before. Start it as the service user instead, e.g.:\n"
            "      bash /home/angelo/lescall/phase14_runtime/start_all.sh\n"
        )
        raise SystemExit(1)


def bootstrap_runtime_dirs(extra: tuple[str, ...] = ()) -> None:
    """Create every shared runtime dir with permissive mode. Never raises."""
    for d in (*RUNTIME_DIRS, *extra):
        try:
            os.makedirs(d, exist_ok=True)
            os.chmod(d, 0o777)
        except OSError as exc:
            # Best effort: a single un-chmod-able dir (e.g. owned by another
            # user from a past bad run) must not stop the service from booting.
            sys.stderr.write(
                f"WARN: could not bootstrap runtime dir {d}: {exc!r}\n"
            )
