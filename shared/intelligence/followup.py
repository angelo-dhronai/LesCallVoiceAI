"""Stage 3 — the follow-up decision engine.

Deterministic-first, LLM-second, explainable always:

    1. A clarified in-call commitment with a concrete deadline wins outright
       (decision=yes, confidence 0.98, source in_call).
    2. Deterministic live-engine flags (callback_requested with a parseable
       time, whatsapp_promised) force YES.
    3. Otherwise the extraction stage's follow_up judgment decides
       (yes / no / unsure with the model's confidence + reason).

Every decision — including NO — is stored as a followups row so the dashboard
can always answer "why (not)?". Vague YES decisions get a concrete
scheduled_at from deterministic rules, flagged auto_scheduled so the UI shows
the time was AI-picked.

Idempotent: re-runs replace only rows with source='pipeline' for the call;
in-call and manual follow-ups are never touched.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from shared.db.database import get_db

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

_PRIORITY_DEFAULT = "medium"
_DEFAULT_HOUR = 11  # business-hours default when only a date is known


def _now() -> dt.datetime:
    return dt.datetime.now(IST)


_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday",
             "saturday", "sunday")
_TIME_WORDS = {"morning": (10, 0), "afternoon": (14, 0), "evening": (18, 0)}


def resolve_deadline(day: str, time_s: str, anchor: dt.datetime) -> str | None:
    """Deterministic 'Tomorrow' + 'evening' → concrete IST timestamp, anchored
    at the call time. Returns ISO string or None when unresolvable."""
    import re as _re

    day_l = (day or "").strip().lower()
    when = None
    if day_l == "today":
        when = anchor
    elif day_l == "tomorrow":
        when = anchor + dt.timedelta(days=1)
    elif day_l.startswith("day after"):
        when = anchor + dt.timedelta(days=2)
    elif day_l in _WEEKDAYS:
        delta = (_WEEKDAYS.index(day_l) - anchor.weekday()) % 7 or 7
        when = anchor + dt.timedelta(days=delta)
    if when is None:
        return None
    hour, minute = 11, 0
    t = (time_s or "").strip().lower()
    if t in _TIME_WORDS:
        hour, minute = _TIME_WORDS[t]
    else:
        m = _re.match(r"(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?|baje)?", t)
        if m and m.group(1):
            hour = int(m.group(1))
            minute = int(m.group(2) or 0)
            suffix = (m.group(3) or "").replace(".", "")
            if suffix == "pm" and hour < 12:
                hour += 12
            elif suffix in ("baje", "") and hour < 9:
                hour += 12  # "5 baje" on a business call ≈ 17:00
    return when.replace(hour=min(hour, 23), minute=minute, second=0,
                        microsecond=0).isoformat()


def _parse_anchor(call: dict[str, Any]) -> dt.datetime:
    raw = (call.get("started_at") or "").replace("T", " ").replace("Z", "")
    try:
        return dt.datetime.fromisoformat(raw[:19]).replace(tzinfo=IST)
    except ValueError:
        return _now()


def ingest_in_call_commitments(call: dict[str, Any]) -> None:
    """The live engine's typed commitments (collected_data flags.commitments)
    become commitments rows with source='in_call', deadlines resolved
    deterministically. Idempotent: replaces only in_call rows for the call."""
    try:
        flags = (json.loads(call.get("collected_data") or "{}") or {}).get("flags", {}) or {}
    except Exception:
        return
    raw = flags.get("commitments") or []
    if not isinstance(raw, list):
        return
    anchor = _parse_anchor(call)
    with get_db() as db:
        db.execute(
            "DELETE FROM commitments WHERE call_id=? AND source='in_call'",
            (call["id"],),
        )
        for c in raw[:10]:
            if not isinstance(c, dict):
                continue
            day, tm = str(c.get("day") or ""), str(c.get("time") or "")
            deadline = resolve_deadline(day, tm, anchor) if day else None
            db.execute(
                """INSERT INTO commitments
                       (call_id, owner, contact_id, made_by, kind, description,
                        quote, deadline_text, deadline_at, is_vague, clarified,
                        source, confidence)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'in_call', 0.98)""",
                (call["id"], call.get("owner") or "", call.get("contact_id"),
                 c.get("made_by") or "customer", c.get("kind") or "callback",
                 str(c.get("description") or "callback requested")[:500],
                 str(c.get("quote") or "")[:500] or None,
                 " ".join(x for x in (day, tm) if x) or None,
                 deadline, 1 if c.get("vague") else 0,
                 1 if c.get("clarified") else 0),
            )


def _auto_schedule(fu: dict[str, Any], commitments: list[dict[str, Any]]) -> tuple[str | None, bool]:
    """Concrete scheduled_at (ISO) + whether it was auto-picked."""
    date_s, time_s = fu.get("suggested_date"), fu.get("suggested_time")
    if date_s:
        return f"{date_s} {time_s or f'{_DEFAULT_HOUR:02d}:00'}:00+05:30", not bool(time_s)
    # earliest concrete commitment deadline
    deadlines = sorted(c["deadline_at"] for c in commitments if c.get("deadline_at"))
    if deadlines:
        return deadlines[0], False
    # nothing concrete anywhere: business default +2 days
    when = (_now() + dt.timedelta(days=2)).replace(
        hour=_DEFAULT_HOUR, minute=0, second=0, microsecond=0)
    return when.isoformat(), True


def decide(call: dict[str, Any], judgment: dict[str, Any],
           commitments: list[dict[str, Any]]) -> dict[str, Any]:
    """Pure decision function (unit-testable): returns the followups row dict."""
    judgment = judgment or {}
    flags: dict[str, Any] = {}
    try:
        flags = (json.loads(call.get("collected_data") or "{}") or {}).get("flags", {}) or {}
    except Exception:
        pass

    clarified = [c for c in commitments
                 if c.get("source") == "in_call" and c.get("clarified")
                 and c.get("deadline_at")]
    open_kinds = {c.get("kind") for c in commitments}

    if clarified:
        best = sorted(clarified, key=lambda c: c["deadline_at"])[0]
        return {
            "decision": "yes", "confidence": 0.98,
            "explanation": "Commitment was clarified during the call itself.",
            "reason": best.get("description"),
            "followup_type": "callback" if best.get("kind") == "callback" else "other",
            "promised_by": ("customer_requested" if best.get("made_by") == "customer"
                            else "agent_promised"),
            "scheduled_at": best["deadline_at"], "auto_scheduled": 0,
            "priority": judgment.get("priority") or _PRIORITY_DEFAULT,
            "status": "scheduled", "source": "in_call",
            "commitment_id": best.get("id"),
        }

    if flags.get("callback_requested"):
        sched, auto = _auto_schedule(judgment, commitments)
        return {
            "decision": "yes", "confidence": 0.95,
            "explanation": "The live engine recorded an explicit callback request.",
            "reason": judgment.get("reason") or "Customer asked for a callback"
                      + (f" ({flags.get('callback_time')})" if flags.get("callback_time") else ""),
            "followup_type": "callback", "promised_by": "customer_requested",
            "scheduled_at": sched, "auto_scheduled": 1 if auto else 0,
            "priority": judgment.get("priority") or "high",
            "status": "scheduled", "source": "pipeline",
        }

    required = (judgment.get("required") or "unsure").lower()
    confidence = judgment.get("confidence")
    if required == "yes":
        sched, auto = _auto_schedule(judgment, commitments)
        return {
            "decision": "yes", "confidence": confidence,
            "explanation": judgment.get("reason") or "AI analysis found a pending obligation.",
            "reason": judgment.get("reason"),
            "followup_type": judgment.get("type")
                or ("whatsapp" in open_kinds and "send_info") or "callback",
            "promised_by": judgment.get("who_promised"),
            "scheduled_at": sched, "auto_scheduled": 1 if auto else 0,
            "priority": judgment.get("priority") or _PRIORITY_DEFAULT,
            "status": "scheduled", "source": "pipeline",
        }
    if required == "no":
        return {
            "decision": "no", "confidence": confidence,
            "explanation": judgment.get("reason") or "No pending obligation detected.",
            "reason": judgment.get("reason"),
            "followup_type": None, "promised_by": None,
            "scheduled_at": None, "auto_scheduled": 0,
            "priority": "low", "status": "archived", "source": "pipeline",
        }
    return {
        "decision": "unsure", "confidence": confidence,
        "explanation": judgment.get("reason")
                       or "Signals were too weak or conflicting to auto-decide.",
        "reason": judgment.get("reason"),
        "followup_type": judgment.get("type"), "promised_by": judgment.get("who_promised"),
        "scheduled_at": None, "auto_scheduled": 0,
        "priority": judgment.get("priority") or _PRIORITY_DEFAULT,
        "status": "pending", "source": "pipeline",
    }


async def stage_followup(call: dict[str, Any]) -> None:
    call_id = call["id"]
    ingest_in_call_commitments(call)
    with get_db() as db:
        ci = db.execute(
            "SELECT stages, summary FROM call_intelligence WHERE call_id=?", (call_id,)
        ).fetchone()
        commitments = [dict(r) for r in db.execute(
            "SELECT * FROM commitments WHERE call_id=?", (call_id,)
        ).fetchall()]
    judgment = {}
    if ci:
        try:
            judgment = json.loads(ci["stages"] or "{}").get("_follow_up_judgment", {}) or {}
        except Exception:
            judgment = {}

    row = decide(call, judgment, commitments)
    with get_db() as db:
        # replace only pipeline/in_call rows for this call (idempotent re-run);
        # manual follow-ups are user data and never touched
        db.execute(
            "DELETE FROM followups WHERE call_id=? AND source IN ('pipeline','in_call')",
            (call_id,),
        )
        db.execute(
            """INSERT INTO followups
                   (owner, call_id, contact_id, commitment_id, decision, confidence,
                    explanation, reason, summary, followup_type, promised_by,
                    scheduled_at, auto_scheduled, priority, status, source, audit)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (call.get("owner") or "", call_id, call.get("contact_id"),
             row.get("commitment_id"), row["decision"], row.get("confidence"),
             row.get("explanation"), row.get("reason"),
             (ci["summary"] if ci else None),
             row.get("followup_type"), row.get("promised_by"),
             row.get("scheduled_at"), row.get("auto_scheduled", 0),
             row.get("priority") or _PRIORITY_DEFAULT, row["status"], row["source"],
             json.dumps([{"at": _now().isoformat(), "by": "pipeline",
                          "action": "decision", "detail": row["decision"]}])),
        )


def sweep_overdue() -> int:
    """Mark scheduled follow-ups whose time passed as missed, then fire
    followup.overdue automation events for each."""
    with get_db() as db:
        rows = [dict(r) for r in db.execute(
            """UPDATE followups
                   SET status='missed', updated_at=now()
                 WHERE status IN ('pending', 'scheduled')
                   AND scheduled_at IS NOT NULL
                   AND scheduled_at < now() - interval '1 hour'
                   AND deleted_at IS NULL
             RETURNING id, owner, call_id, priority, reason""",
        ).fetchall()]
    if rows:
        try:
            from shared.intelligence.automation import fire_overdue_events

            fire_overdue_events(rows)
        except Exception:  # events are best-effort; the status change is not
            pass
    return len(rows)
