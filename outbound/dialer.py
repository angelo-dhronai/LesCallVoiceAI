"""Outbound dialer engine — DB-backed.

Reads campaigns/leads from the shared DB, originates calls through FreeSWITCH
(same `fs_cli originate ... sofia/external/<phone>@<gateway> outbound_ai_connect`
command the proven dashboard uses), and writes status/disposition back to the DB.

The dialplan extension `outbound_ai_connect` hands the answered call to the AI
socket server (127.0.0.1:9000) — identical to the live inbound path.

Run a campaign loop:
    python -m outbound.dialer --campaign 1
Single lead (testing):
    python -m outbound.dialer --lead 5
Add --dry-run to skip the actual fs_cli originate.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re

from shared.db.database import get_db

# Provider-agnostic: no hardcoded carrier. Each campaign supplies its own
# gateway (the trunk name / SIP domain) + caller_id; these env vars are only
# optional fallbacks for single-tenant/dev use.
CALLER_ID = os.environ.get("OUTBOUND_CALLER_ID", "")
GATEWAY = os.environ.get("OUTBOUND_GATEWAY", "")
DP_EXT = os.environ.get("OUTBOUND_DP_EXT", "outbound_ai_connect")
DP_CTX = os.environ.get("OUTBOUND_DP_CTX", "default")
FS_CLI = os.environ.get("FS_CLI", "fs_cli")


def _originate_cmd(phone: str, caller_id: str, gateway: str,
                   campaign_id: int | None = None) -> str:
    # lescall_campaign_id rides along as a channel variable so socket_server can
    # forward it to the AI bridge for per-campaign agent selection.
    camp = f",lescall_campaign_id={campaign_id}" if campaign_id else ""
    return (
        f"originate {{origination_caller_id_number={caller_id},"
        f"originate_timeout=45,ignore_early_media=true{camp}}}"
        f"sofia/external/{phone}@{gateway} {DP_EXT} XML {DP_CTX}"
    )


def _parse_result(out: str) -> str:
    if "+OK" in out:
        return "answered"
    if "NO_ANSWER" in out or "ALLOTTED_TIMEOUT" in out:
        return "no_answer"
    if "USER_BUSY" in out or "BUSY" in out:
        return "busy"
    if any(x in out for x in ("NORMAL_CLEARING", "CALL_REJECTED", "USER_NOT_REGISTERED",
                              "UNALLOCATED", "ORIGINATOR_CANCEL", "SUBSCRIBER_ABSENT")):
        return "declined"
    return "failed"


_DISPOSITION = {"declined": "rejected", "no_answer": "to_contact_later", "busy": "to_contact_later"}


async def dial_lead(lead: dict, caller_id: str, gateway: str, dry_run: bool = False) -> str:
    phone = re.sub(r"[^\d]", "", lead.get("phone", ""))
    if len(phone) < 7:
        _record(lead["id"], "failed")
        return "failed"
    if not gateway:
        # No carrier gateway configured on the campaign — can't originate.
        _record(lead["id"], "failed")
        return "failed (no gateway)"

    _set_status(lead["id"], "dialing")
    if dry_run:
        _record(lead["id"], "answered")
        return "answered (dry-run)"

    cmd = _originate_cmd(phone, caller_id, gateway, lead.get("campaign_id"))
    try:
        proc = await asyncio.create_subprocess_exec(
            FS_CLI, "-x", cmd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=55)
        status = _parse_result(stdout.decode() + " " + stderr.decode())
    except (FileNotFoundError, OSError):
        # fs_cli not installed (e.g. inside a container) -> originate over ESL TCP.
        from shared.esl import esl_api
        ok, out = await asyncio.to_thread(esl_api, cmd, 55.0)
        status = _parse_result(out) if ok else "failed"
    except Exception:
        status = "failed"
    _record(lead["id"], status)
    return status


async def run_campaign(campaign_id: int, dry_run: bool = False) -> dict:
    """One pass over a campaign's pending leads, respecting retry_limit + concurrency."""
    with get_db() as db:
        camp = db.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        if not camp:
            raise SystemExit(f"campaign {campaign_id} not found")
        camp = dict(camp)
        leads = [dict(r) for r in db.execute(
            "SELECT * FROM leads WHERE campaign_id=? AND status IN ('pending','failed') "
            "AND attempts < ? ORDER BY id", (campaign_id, camp["retry_limit"]))]

    caller_id = camp.get("caller_id") or CALLER_ID
    gateway = camp.get("gateway") or GATEWAY
    sem = asyncio.Semaphore(max(1, camp.get("max_concurrency", 1)))

    async def _one(lead):
        async with sem:
            return await dial_lead(lead, caller_id, gateway, dry_run)

    results = await asyncio.gather(*[_one(l) for l in leads], return_exceptions=True)
    summary = {}
    for r in results:
        key = str(r)
        summary[key] = summary.get(key, 0) + 1
    return {"campaign_id": campaign_id, "dialed": len(leads), "results": summary}


def _set_status(lead_id: int, status: str) -> None:
    with get_db() as db:
        db.execute("UPDATE leads SET status=? WHERE id=?", (status, lead_id))


def _record(lead_id: int, call_status: str) -> None:
    status = "called" if call_status == "answered" else "failed"
    with get_db() as db:
        row = db.execute("SELECT disposition FROM leads WHERE id=?", (lead_id,)).fetchone()
        disp = row["disposition"] if row else None
        if not disp or disp == "not_classified":
            disp = _DISPOSITION.get(call_status, disp)
        db.execute(
            "UPDATE leads SET status=?, disposition=?, attempts=attempts+1, "
            "last_called_at=CURRENT_TIMESTAMP WHERE id=?", (status, disp, lead_id))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign", type=int)
    ap.add_argument("--lead", type=int)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.lead:
        with get_db() as db:
            lead = db.execute("SELECT * FROM leads WHERE id=?", (args.lead,)).fetchone()
        if not lead:
            raise SystemExit("lead not found")
        print(asyncio.run(dial_lead(dict(lead), CALLER_ID, GATEWAY, args.dry_run)))
    elif args.campaign:
        print(asyncio.run(run_campaign(args.campaign, args.dry_run)))
    else:
        ap.error("pass --campaign or --lead")


if __name__ == "__main__":
    main()
