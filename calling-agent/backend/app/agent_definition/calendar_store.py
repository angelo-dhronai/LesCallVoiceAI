"""
Calendar bookings store — per-agent persistence of booked appointment slots.

Each agent folder gets a bookings.json next to its agent.yaml:

    [
      {
        "slot_day": "Tomorrow",
        "slot_time": "10:00 AM",
        "appointment_time": "Tomorrow at 10:00 AM",
        "caller_name": "Ravi",
        "caller_phone": "+91...",
        "conn_id": "abc123",
        "booked_at": "2026-07-04T10:12:00Z"
      }
    ]

A slot from calendar.mock_slots is considered taken once one booking
references it, so availability shrinks as calls book slots. Writes are
atomic (temp file + os.replace) because the dashboard process and the
telephony bridge may both touch the file.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_AGENTS_DIR = Path(__file__).resolve().parents[3] / "agents"


def bookings_path(agent_folder: str) -> Path:
    return _AGENTS_DIR / agent_folder / "bookings.json"


def list_bookings(agent_folder: str) -> list[dict]:
    path = bookings_path(agent_folder)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as exc:
        logger.warning("[calendar] could not read bookings for '%s': %s", agent_folder, exc)
        return []


def _save(agent_folder: str, bookings: list[dict]) -> None:
    path = bookings_path(agent_folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(bookings, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def add_booking(
    agent_folder: str,
    slot_day: str,
    slot_time: str,
    appointment_time: str,
    caller_name: str = "",
    caller_phone: str = "",
    conn_id: str = "",
) -> dict:
    """Record a booking. Duplicate (conn_id, appointment_time) pairs are ignored
    so re-confirmation within one call doesn't double-book."""
    bookings = list_bookings(agent_folder)
    for b in bookings:
        if conn_id and b.get("conn_id") == conn_id and b.get("appointment_time") == appointment_time:
            return b
    entry = {
        "slot_day": slot_day,
        "slot_time": slot_time,
        "appointment_time": appointment_time,
        "caller_name": caller_name,
        "caller_phone": caller_phone,
        "conn_id": conn_id,
        "booked_at": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    bookings.append(entry)
    _save(agent_folder, bookings)
    logger.info("[calendar] booked %r for '%s' (caller=%s)", appointment_time, agent_folder, caller_name or "?")
    return entry


def delete_booking(agent_folder: str, index: int) -> bool:
    bookings = list_bookings(agent_folder)
    if 0 <= index < len(bookings):
        bookings.pop(index)
        _save(agent_folder, bookings)
        return True
    return False


def _slot_key(day: str, time: str) -> str:
    return f"{day.strip().lower()}|{time.strip().lower()}"


def booked_slot_keys(agent_folder: str) -> set[str]:
    keys = set()
    for b in list_bookings(agent_folder):
        day, time = b.get("slot_day", ""), b.get("slot_time", "")
        if day or time:
            keys.add(_slot_key(day, time))
    return keys


def available_slots(agent_def, agent_folder: str) -> list:
    """mock_slots minus already-booked slots. Falls back to all slots when the
    bookings file is unreadable — offering a taken slot beats offering none."""
    slots = list(getattr(agent_def.calendar, "mock_slots", []) or [])
    if not agent_folder:
        return slots
    taken = booked_slot_keys(agent_folder)
    if not taken:
        return slots
    return [s for s in slots if _slot_key(s.day, s.time) not in taken]
