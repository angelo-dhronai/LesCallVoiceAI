"""Parse call transcripts written by call_audit into structured turns.

Format on disk (recordings/<conn_id>/transcript.txt):

    [00:01.951]
    Agent: Hello! ...

    [00:12.864]
    Caller: जी नहीं ...

Returns a list of {ts, role, text}. Tolerant of missing timestamps / blank lines.
"""
from __future__ import annotations

import re
from pathlib import Path

_TS = re.compile(r"^\[(\d+):(\d+(?:\.\d+)?)\]\s*$")
_TURN = re.compile(r"^(Agent|Caller|User|System)\s*:\s*(.*)$", re.IGNORECASE)


def parse_transcript_text(text: str) -> list[dict]:
    turns: list[dict] = []
    pending_ts: float | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _TS.match(line)
        if m:
            pending_ts = int(m.group(1)) * 60 + float(m.group(2))
            continue
        t = _TURN.match(line)
        if t:
            role = t.group(1).lower()
            role = "agent" if role == "agent" else ("caller" if role in ("caller", "user") else role)
            turns.append({"ts": pending_ts, "role": role, "text": t.group(2).strip()})
            pending_ts = None
        elif turns:
            # continuation of the previous turn
            turns[-1]["text"] += " " + line
    return turns


def parse_transcript_file(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return parse_transcript_text(p.read_text(errors="replace"))


def transcript_to_plain(turns: list[dict]) -> str:
    """Flatten to 'Agent: ...\\nCaller: ...' for LLM input."""
    return "\n".join(f"{t['role'].capitalize()}: {t['text']}" for t in turns)
