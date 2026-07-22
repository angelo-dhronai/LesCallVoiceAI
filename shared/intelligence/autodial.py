"""Follow-up auto-dialer — guardrails and candidate selection.

The follow-up engine decides a call is needed and schedules a time; until now
nothing placed it, and `sweep_overdue` flipped every due row to 'missed' an
hour later (0 of 155 follow-ups had ever completed). This module decides WHICH
rows may be dialled and WHY a row may not be; the actual origination lives in
dashboard/server.py, which owns the caller-ID/trunk identity, the duplicate
guard and the FreeSWITCH channel.

Everything that makes a decision here is a pure function over (row, now,
counters) so the guardrails can be tested without a database, a clock, or a
phone. `due_candidates` and `record_attempt` are the only DB-touching parts.

The guardrail set, and why each exists:

    window       a customer called at 06:00 is a complaint, not a follow-up
    weekday      Sunday calling generates complaints faster than anything else
    retry cap    without it, a restart loop redials the same person forever
    retry gap    three attempts inside a minute reads as harassment
    daily cap    bounds the blast radius of a bad batch to something survivable
    grace        a human gets a moment to intervene on a just-scheduled row
    max age      a follow-up nobody chased for a week is cold; calling is worse
                 than not calling
    DND          a customer who asked us to stop is never dialled again
    kill switch  config.AUTODIAL_ENABLED, re-read every tick

A row that trips a PERMANENT guardrail (retry cap, DND) is stamped
autodial_blocked_at and never reconsidered. A TRANSIENT one (window, caps)
leaves it eligible for the next sweep.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any

from shared.db.database import get_db
from shared.intelligence import config

log = logging.getLogger("intel.autodial")

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# Guardrails whose verdict can never change for this row, so the executor stops
# reconsidering it. Everything else is retried on the next sweep.
PERMANENT = {"retry_cap", "dnd", "too_old", "no_contact"}


def now_local() -> dt.datetime:
    return dt.datetime.now(IST)


def as_dt(value: Any) -> dt.datetime | None:
    """Coerce a DB timestamp to an aware datetime, or None.

    Load-bearing: this database layer hands TIMESTAMPTZ columns back as
    STRINGS ('2026-07-14 07:30:00+00'), not datetimes. The first version of
    blocked_reason() type-checked with isinstance(..., datetime) and therefore
    skipped BOTH the not-due and too-old gates on every real row — a dry run
    showed it would have dialled follow-ups scheduled three weeks into the
    future, immediately. Never assume these columns are datetimes.

    A naive value is interpreted as IST, matching followups.timezone.
    """
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=IST)
    if not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    # '+00' / '-0530' → '+00:00' / '-05:30' for fromisoformat on older parsers.
    for pat, repl in ((r"([+-]\d{2})$", r"\1:00"), (r"([+-]\d{2})(\d{2})$", r"\1:\2")):
        import re as _re
        text = _re.sub(pat, repl, text)
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=IST)


# ---------------------------------------------------------------------------
# Pure guardrail predicates
# ---------------------------------------------------------------------------

def within_window(when: dt.datetime) -> bool:
    """True if `when` (local) is inside the permitted calling window."""
    if when.weekday() not in config.autodial_weekdays():
        return False
    return config.AUTODIAL_HOUR_START <= when.hour < config.AUTODIAL_HOUR_END


def next_window_open(when: dt.datetime) -> dt.datetime:
    """The next moment the window opens at or after `when` — used to explain a
    skip ("will call after 10:00 Mon") rather than just refusing."""
    probe = when
    for _ in range(14 * 24):  # bounded: a fortnight of hourly probes
        if within_window(probe):
            return probe
        nxt = (probe + dt.timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        if nxt.hour < probe.hour:  # rolled past midnight
            nxt = nxt.replace(hour=0)
        probe = nxt
    return when


def blocked_reason(row: dict[str, Any], *, now: dt.datetime,
                   placed_today: int) -> str | None:
    """None when the row may be dialled right now, else a machine-readable code.

    Order matters: the most permanent/serious reason is reported first, so a
    DND contact never gets explained away as merely "outside window".
    """
    if row.get("dnd"):
        return "dnd"
    if not row.get("contact_handle"):
        return "no_contact"
    if int(row.get("attempts") or 0) >= config.AUTODIAL_MAX_ATTEMPTS:
        return "retry_cap"

    scheduled = as_dt(row.get("scheduled_at"))
    if scheduled is None:
        # No time on the row means nothing decided WHEN to call. Refuse rather
        # than treat "unscheduled" as "call right now".
        return "not_due"
    due_at = scheduled + dt.timedelta(minutes=config.AUTODIAL_GRACE_MIN)
    if now < due_at:
        return "not_due"
    if now - scheduled > dt.timedelta(hours=config.AUTODIAL_MAX_AGE_H):
        return "too_old"

    last = as_dt(row.get("last_attempt_at"))
    if last is not None and now - last < dt.timedelta(minutes=config.AUTODIAL_RETRY_GAP_MIN):
        return "retry_gap"

    if placed_today >= config.AUTODIAL_MAX_PER_DAY:
        return "daily_cap"
    if not within_window(now):
        return "outside_window"
    return None


def explain(reason: str, now: dt.datetime | None = None) -> str:
    """Human sentence for a guardrail code — shown on the follow-up row."""
    now = now or now_local()
    return {
        "dnd": "Customer asked not to be contacted — never auto-dialled.",
        "no_contact": "No linked contact number to call.",
        "retry_cap": f"Reached the {config.AUTODIAL_MAX_ATTEMPTS}-attempt limit — needs a human.",
        "not_due": "Not due yet.",
        "too_old": f"More than {config.AUTODIAL_MAX_AGE_H}h past its time — too cold to auto-call.",
        "retry_gap": f"Waiting {config.AUTODIAL_RETRY_GAP_MIN} min between attempts.",
        "daily_cap": f"Daily auto-call limit ({config.AUTODIAL_MAX_PER_DAY}) reached.",
        "outside_window": ("Outside calling hours — will dial after "
                           f"{next_window_open(now):%a %H:%M}."),
        "no_identity": "No agent is activated on an outbound number for this account.",
    }.get(reason, reason)


# ---------------------------------------------------------------------------
# Candidate selection (DB)
# ---------------------------------------------------------------------------

_DUE_SQL = """
    SELECT f.id, f.owner, f.call_id, f.contact_id, f.scheduled_at, f.priority,
           f.reason, f.attempts, f.last_attempt_at, f.status,
           COALESCE(ct.legacy_id, 'db' || ct.id::text) AS contact_handle,
           COALESCE(ct.phone, c.phone)                 AS dial_phone,
           ct.name                                     AS contact_name,
           (ct.disposition = 'rejected')               AS dnd
      FROM followups f
      LEFT JOIN calls c    ON c.id  = f.call_id
      LEFT JOIN contacts ct ON ct.id = f.contact_id
     WHERE f.deleted_at IS NULL
       AND f.autodial_blocked_at IS NULL
       AND f.decision = 'yes'
       AND f.status IN ('pending', 'scheduled', 'missed')
       AND f.scheduled_at IS NOT NULL
     ORDER BY f.priority = 'urgent' DESC, f.scheduled_at
     LIMIT ?"""


def due_candidates(limit: int = 200) -> list[dict[str, Any]]:
    """Rows the executor should consider this sweep, most urgent first.

    Deliberately only `decision='yes'`: a 'no' or 'unsure' decision must never
    result in an automatic call — 'unsure' is exactly the case a human reviews.
    """
    with get_db() as db:
        return [dict(r) for r in db.execute(_DUE_SQL, (limit,)).fetchall()]


def placed_today(owner: str) -> int:
    """Auto-calls already placed for this tenant in the current local day."""
    with get_db() as db:
        row = db.execute(
            """SELECT count(*) AS n FROM followups
                WHERE owner = ? AND last_attempt_at IS NOT NULL
                  AND (last_attempt_at AT TIME ZONE 'Asia/Kolkata')::date
                      = (now() AT TIME ZONE 'Asia/Kolkata')::date
                  AND last_attempt_error IS NULL""",
            (owner,),
        ).fetchone()
    return int(row["n"] or 0)


def record_attempt(fid: int, *, ok: bool, detail: str = "",
                   permanent: bool = False) -> None:
    """Stamp the outcome of one auto-dial attempt.

    `attempts` only increments when a call was actually PLACED — a row skipped
    for being outside the window must not burn a retry, or an overnight backlog
    would exhaust its budget before business hours.
    """
    with get_db() as db:
        row = db.execute(
            "SELECT audit FROM followups WHERE id=?", (fid,)).fetchone()
        audit = []
        if row:
            try:
                audit = json.loads(row["audit"] or "[]")
            except Exception:
                audit = []
        audit.append({
            "at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "by": "autodial",
            "action": "call_placed" if ok else "call_skipped",
            "detail": detail[:300],
        })
        if ok:
            db.execute(
                """UPDATE followups
                      SET attempts = attempts + 1, last_attempt_at = now(),
                          last_attempt_error = NULL, status = 'scheduled',
                          audit = ?, updated_at = now()
                    WHERE id = ?""",
                (json.dumps(audit[-50:]), fid),
            )
        else:
            db.execute(
                """UPDATE followups
                      SET last_attempt_error = ?, audit = ?, updated_at = now(),
                          autodial_blocked_at = CASE WHEN ? THEN now()
                                                     ELSE autodial_blocked_at END
                    WHERE id = ?""",
                (detail[:300], json.dumps(audit[-50:]), permanent, fid),
            )
