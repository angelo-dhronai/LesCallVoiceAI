"""BYON self-service API (docs/MULTITENANT_BYON_PLAN.md §3b).

Owner-scoped trunk + DID registration backed by the shared DB, FreeSWITCH
config generation (shared/freeswitch_config.py) and the Kamailio carrier ACL
(shared/kamailio_acl.py). Mounted into dashboard/server.py exactly like
agent_builder_api (APIRouter, try/except include).

Every endpoint takes `owner: str = Depends(current_owner)` — that dependency
IS the tenant boundary (single-tenant fallback until Google OAuth env is set).
"""

from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("dashboard.telephony")

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from auth import current_owner  # noqa: E402 - same-dir module, like agent_builder_api
from shared.db.database import get_db  # noqa: E402
from shared.phone import significant_digits  # noqa: E402
from shared import freeswitch_config as fsc  # noqa: E402
from shared import kamailio_acl  # noqa: E402

AGENTS_DIR = _REPO_ROOT / "calling-agent" / "agents"
PUBLIC_SIP_IP = os.environ.get("PUBLIC_SIP_IP", "35.207.225.237")
PUBLIC_SIP_PORT = os.environ.get("PUBLIC_SIP_PORT", "5060")
RTP_PORT_RANGE = os.environ.get("PUBLIC_RTP_RANGE", "16384-32768")

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _owner_slug(owner: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", owner.lower()).strip("-")


def _trunk_or_404(db, trunk_id: int, owner: str) -> dict:
    row = db.execute("SELECT * FROM trunks WHERE id=? AND owner=?",
                     (trunk_id, owner)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="trunk not found")
    return dict(row)


def _all_provider_cidrs(db) -> list[str]:
    """Union of provider_ips across ALL trunks — the Kamailio ACL is one
    global list (grp 1); adding a trunk widens it, never replaces others'."""
    cidrs: list[str] = []
    for row in db.execute("SELECT provider_ips FROM trunks "
                          "WHERE provider_ips IS NOT NULL").fetchall():
        cidrs.extend(p.strip() for p in str(row["provider_ips"]).split(",") if p.strip())
    return cidrs


def _validate_agent_binding(db, slug: str, owner: str) -> None:
    """A DID may only bind to an agent the caller owns.

    Agents created after seeding have no index row yet — binding one CLAIMS it
    for the caller (writes the agents row), so a second tenant can never bind
    the same unclaimed agent afterwards.
    """
    if not slug:
        return
    if not re.fullmatch(r"[A-Za-z0-9_-]+", slug):
        raise HTTPException(status_code=400, detail="invalid agent slug")
    if not (AGENTS_DIR / slug / "agent.yaml").exists():
        raise HTTPException(status_code=400, detail=f"agent '{slug}' has no agent.yaml")
    row = db.execute("SELECT owner FROM agents WHERE slug=?", (slug,)).fetchone()
    if row is None:
        db.execute("INSERT INTO agents (slug, owner) VALUES (?,?)", (slug, owner))
    elif not row["owner"]:
        db.execute("UPDATE agents SET owner=? WHERE slug=?", (owner, slug))
    elif row["owner"] != owner:
        raise HTTPException(status_code=403, detail="agent belongs to another tenant")


def _warn_direction_mismatch(slug: str, want: str) -> None:
    """Log when an agent authored for one direction is bound to the other slot.

    Deliberately NOT an error: the outbound column is backfilled from the
    inbound one, so every pre-existing number would otherwise 400 on its next
    unrelated edit. This is the breadcrumb for 'the outbound agent behaves like
    a receptionist' reports.
    """
    if not slug:
        return
    try:
        import yaml
        with open(AGENTS_DIR / slug / "agent.yaml", encoding="utf-8") as fh:
            have = str((yaml.safe_load(fh) or {}).get("call_direction") or "inbound")
    except Exception:
        return
    if have != want:
        logger.warning("agent '%s' is authored call_direction=%s but is bound to "
                       "the %s slot — it will use %s-shaped prompts on %s calls",
                       slug, have, want, have, want)


# Carrier signaling blocks are /16 or narrower in practice; anything broader
# (or 0.0.0.0/…, loopback) would let one tenant swing the platform-global SIP
# allow-list wide open.
_MIN_CIDR_PREFIX = 16
_MAX_CIDR_ENTRIES = 64


def _validate_provider_ips(provider_ips: str) -> None:
    import ipaddress
    entries = [p.strip() for p in (provider_ips or "").split(",") if p.strip()]
    if len(entries) > _MAX_CIDR_ENTRIES:
        raise HTTPException(status_code=400,
                            detail=f"too many provider_ips (max {_MAX_CIDR_ENTRIES})")
    for item in entries:
        try:
            net = ipaddress.ip_network(item, strict=False)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"invalid CIDR: {item}")
        if net.version != 4:
            raise HTTPException(status_code=400, detail=f"IPv4 only: {item}")
        if net.prefixlen < _MIN_CIDR_PREFIX:
            raise HTTPException(
                status_code=400,
                detail=f"{item}: prefix must be /{_MIN_CIDR_PREFIX} or narrower")
        if net.is_loopback or str(net.network_address) == "0.0.0.0":
            raise HTTPException(status_code=400, detail=f"{item}: not allowed")


async def _body(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    return data


# ---------------------------------------------------------------------------
# Trunks
# ---------------------------------------------------------------------------

@router.get("/api/trunks")
def list_trunks(owner: str = Depends(current_owner)):
    with get_db() as db:
        rows = db.execute("SELECT * FROM trunks WHERE owner=? ORDER BY id DESC",
                          (owner,)).fetchall()
    trunks = []
    for r in rows:
        t = dict(r)
        t["password"] = "***" if t.get("password") else ""
        trunks.append(t)
    return {"trunks": trunks}


@router.post("/api/trunks")
async def create_trunk(request: Request, owner: str = Depends(current_owner)):
    body = await _body(request)
    name = str(body.get("name") or "").strip()
    sip_domain = str(body.get("sip_domain") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name or ""):
        raise HTTPException(status_code=400,
                            detail="name required ([A-Za-z0-9_-], max 64)")
    if not sip_domain:
        raise HTTPException(status_code=400, detail="sip_domain required")
    _validate_provider_ips(str(body.get("provider_ips") or ""))

    # FreeSWITCH gateway names are global — prefix with the owner slug so two
    # tenants' trunks can never collide.
    full_name = f"{_owner_slug(owner)}_{name}"
    fields = {
        "name": full_name,
        "provider": str(body.get("provider") or "plivo"),
        "owner": owner,
        "sip_domain": sip_domain,
        "proxy": str(body.get("proxy") or ""),
        "username": str(body.get("username") or ""),
        "password": str(body.get("password") or ""),
        "register": 1 if body.get("register") else 0,
        "caller_id": str(body.get("caller_id") or ""),
        "provider_ips": str(body.get("provider_ips") or ""),
        "transport": str(body.get("transport") or "udp"),
    }
    with get_db() as db:
        dup = db.execute("SELECT id FROM trunks WHERE name=?", (full_name,)).fetchone()
        if dup:
            raise HTTPException(status_code=409, detail="trunk name already exists")
        cols = ",".join(fields)
        marks = ",".join("?" * len(fields))
        row = db.execute(
            f"INSERT INTO trunks ({cols}) VALUES ({marks}) RETURNING id",
            tuple(fields.values())).fetchone()
        trunk_id = row["id"]
        acl = kamailio_acl.sync_carrier_acl(_all_provider_cidrs(db))
    logger.info("trunk created: %s (owner=%s) kamailio_acl=%s", full_name, owner, acl)
    return {"id": trunk_id, "name": full_name, "kamailio_acl": acl}


@router.delete("/api/trunks/{trunk_id}")
def delete_trunk(trunk_id: int, owner: str = Depends(current_owner)):
    with get_db() as db:
        _trunk_or_404(db, trunk_id, owner)
        n = db.execute("SELECT COUNT(*) AS n FROM phone_numbers WHERE trunk_id=?",
                       (trunk_id,)).fetchone()["n"]
        if n:
            raise HTTPException(status_code=409,
                                detail=f"{n} number(s) still bound to this trunk")
        db.execute("DELETE FROM trunks WHERE id=?", (trunk_id,))
        acl = kamailio_acl.sync_carrier_acl(_all_provider_cidrs(db))
    return {"deleted": trunk_id, "kamailio_acl": acl,
            "note": "generated FS gateway file (if applied) is not auto-removed; "
                    "see deploy/RUNBOOK.md"}


@router.post("/api/trunks/{trunk_id}/apply")
def apply_trunk(trunk_id: int, owner: str = Depends(current_owner)):
    """Generate + write FreeSWITCH config for this trunk and its DIDs, reload
    FS, and re-sync the Kamailio carrier ACL."""
    with get_db() as db:
        trunk = _trunk_or_404(db, trunk_id, owner)
        numbers = [dict(r) for r in db.execute(
            "SELECT * FROM phone_numbers WHERE trunk_id=? AND owner=?",
            (trunk_id, owner)).fetchall()]
        cidrs = _all_provider_cidrs(db)

    try:
        result = fsc.apply_config(trunk, numbers, reload=True, all_provider_cidrs=cidrs)
    except PermissionError as exc:
        return JSONResponse(status_code=500, content={
            "status": "error",
            "error": f"cannot write {fsc.FS_CONF_DIR}: {exc}",
            "hint": "grant the dashboard user group-write on sip_profiles/external "
                    "and dialplan/public (see deploy/RUNBOOK.md §FS permissions)"})
    result["kamailio_acl"] = kamailio_acl.sync_carrier_acl(cidrs)

    status = "applied" if result.get("reloaded") else "error"
    with get_db() as db:
        db.execute("UPDATE trunks SET config_status=?, last_applied_at=CURRENT_TIMESTAMP "
                   "WHERE id=?", (status, trunk_id))
        if numbers:
            db.execute(
                "UPDATE phone_numbers SET config_status=?, last_applied_at=CURRENT_TIMESTAMP "
                "WHERE trunk_id=?", (status, trunk_id))
    return {"status": status, **result}


@router.get("/api/trunks/{trunk_id}/instructions")
def trunk_instructions(trunk_id: int, owner: str = Depends(current_owner)):
    with get_db() as db:
        trunk = _trunk_or_404(db, trunk_id, owner)
        numbers = [r["number"] for r in db.execute(
            "SELECT number FROM phone_numbers WHERE trunk_id=?", (trunk_id,)).fetchall()]
    steps = [
        f"Point your carrier's origination/termination URI at "
        f"sip:{PUBLIC_SIP_IP}:{PUBLIC_SIP_PORT} (UDP).",
        "Add your carrier's SIP signaling IPs/CIDRs to this trunk's provider_ips "
        "and re-apply — inbound from unlisted IPs is rejected at the SIP edge.",
        f"Allow RTP (media) to/from {PUBLIC_SIP_IP} UDP {RTP_PORT_RANGE} in any "
        "carrier-side firewall.",
        "If the carrier authenticates by IP allow-list (Plivo zentrunk style), add "
        f"this server's egress IP {PUBLIC_SIP_IP} to the carrier trunk's IP ACL.",
    ]
    if trunk.get("provider") == "plivo":
        steps.append(
            "Plivo: use SEPARATE inbound and outbound zentrunks — origination URI "
            "goes on the INBOUND trunk; this trunk's sip_domain must be the "
            "OUTBOUND trunk's domain or outbound calls fail with 403/4180.")
    return {"trunk": trunk["name"], "numbers": numbers,
            "sip_uri": f"sip:{PUBLIC_SIP_IP}:{PUBLIC_SIP_PORT}",
            "rtp_range": RTP_PORT_RANGE, "steps": steps}


# ---------------------------------------------------------------------------
# Phone numbers (DIDs) — THE inbound routing table
# ---------------------------------------------------------------------------

@router.get("/api/numbers")
def list_numbers(owner: str = Depends(current_owner)):
    with get_db() as db:
        rows = db.execute("SELECT * FROM phone_numbers WHERE owner=? ORDER BY id DESC",
                          (owner,)).fetchall()
    return {"numbers": [dict(r) for r in rows]}


@router.post("/api/numbers")
async def create_number(request: Request, owner: str = Depends(current_owner)):
    body = await _body(request)
    number = str(body.get("number") or "").strip()
    sig = significant_digits(number)
    if len(sig) < 7:
        raise HTTPException(status_code=400, detail="number must have at least 7 digits")
    trunk_id = body.get("trunk_id")
    slug = str(body.get("inbound_agent_slug") or "").strip()
    # Omitting the outbound slug mirrors the inbound one, so a number registered
    # by an older client (or the simple form) can place calls immediately.
    out_slug = str(body.get("outbound_agent_slug") or slug).strip()

    with get_db() as db:
        if trunk_id is not None:
            _trunk_or_404(db, int(trunk_id), owner)  # ownership check
        _validate_agent_binding(db, slug, owner)
        _validate_agent_binding(db, out_slug, owner)
        _warn_direction_mismatch(slug, "inbound")
        _warn_direction_mismatch(out_slug, "outbound")
        dup = db.execute(
            "SELECT id, owner FROM phone_numbers WHERE number=? OR sig_digits=?",
            (number, sig)).fetchone()
        if dup:
            # deliberately vague: don't confirm to one tenant that another has
            # this number (enumeration oracle)
            raise HTTPException(status_code=409, detail="number unavailable")
        row = db.execute(
            "INSERT INTO phone_numbers (number, sig_digits, trunk_id, "
            "inbound_agent_slug, outbound_agent_slug, owner, direction, label) "
            "VALUES (?,?,?,?,?,?,?,?) RETURNING id",
            (number, sig, trunk_id, slug or None, out_slug or None, owner,
             str(body.get("direction") or "both"),
             str(body.get("label") or ""))).fetchone()
    logger.info("number registered: %s -> in=%s out=%s (owner=%s)",
                number, slug, out_slug, owner)
    return {"id": row["id"], "number": number, "sig_digits": sig,
            "inbound_agent_slug": slug or None,
            "outbound_agent_slug": out_slug or None,
            "note": "run POST /api/trunks/{trunk_id}/apply to generate the "
                    "FreeSWITCH dialplan for this DID"}


@router.patch("/api/numbers/{number_id}")
async def update_number(number_id: int, request: Request,
                        owner: str = Depends(current_owner)):
    """Rebind a DID (agent / direction / label). Routing changes take effect on
    the NEXT call — the bridge reads phone_numbers per call."""
    body = await _body(request)
    with get_db() as db:
        row = db.execute("SELECT * FROM phone_numbers WHERE id=? AND owner=?",
                         (number_id, owner)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="number not found")
        updates, params = [], []
        for field, want in (("inbound_agent_slug", "inbound"),
                            ("outbound_agent_slug", "outbound")):
            if field in body:
                slug = str(body.get(field) or "").strip()
                _validate_agent_binding(db, slug, owner)
                _warn_direction_mismatch(slug, want)
                updates.append(f"{field}=?")
                params.append(slug or None)
        if "direction" in body:
            updates.append("direction=?")
            params.append(str(body["direction"]))
        if "label" in body:
            updates.append("label=?")
            params.append(str(body["label"]))
        if not updates:
            raise HTTPException(status_code=400, detail="nothing to update")
        params.append(number_id)
        db.execute(f"UPDATE phone_numbers SET {', '.join(updates)} WHERE id=?",
                   tuple(params))
        out = dict(db.execute("SELECT * FROM phone_numbers WHERE id=?",
                              (number_id,)).fetchone())
    return out


@router.delete("/api/numbers/{number_id}")
def delete_number(number_id: int, owner: str = Depends(current_owner)):
    with get_db() as db:
        row = db.execute("SELECT * FROM phone_numbers WHERE id=? AND owner=?",
                         (number_id, owner)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="number not found")
        db.execute("DELETE FROM phone_numbers WHERE id=?", (number_id,))
    return {"deleted": number_id,
            "note": "generated dialplan file (if applied) is not auto-removed; "
                    "see deploy/RUNBOOK.md"}


# ---------------------------------------------------------------------------
# (Caller-ID OTP verification removed: egressing through the tenant's OWN
# trunk means the carrier enforces which caller IDs the account may send —
# a platform-side OTP call to an AI-answered DID proved nothing. The way to
# check a number end-to-end is POST /api/numbers/{id}/test-call in server.py,
# which rings the account owner's phone from that number with its agent live.)
# ---------------------------------------------------------------------------
# Server info (the BYON "what to configure at your carrier" card)
# ---------------------------------------------------------------------------

@router.get("/api/telephony/server-info")
def server_info(owner: str = Depends(current_owner)):
    return {
        "sip_uri": f"sip:{PUBLIC_SIP_IP}:{PUBLIC_SIP_PORT}",
        "public_ip": PUBLIC_SIP_IP,
        "sip_port": int(PUBLIC_SIP_PORT),
        "transport": "udp",
        "rtp_range": RTP_PORT_RANGE,
        "notes": [
            "Signaling must originate from IPs listed in your trunk's provider_ips "
            "(enforced at the Kamailio SIP edge).",
            "Media (RTP) flows directly between your carrier and this server.",
            "Provider-agnostic: Plivo, Twilio, Telnyx, Vonage etc. all work once "
            "their signaling IPs are in provider_ips.",
        ],
    }
