"""Propagate per-tenant carrier IPs into Kamailio's allow-list.

When a client registers/activates a trunk, their provider signaling IPs
(trunks.provider_ips) must be allowed at the Kamailio edge. Kamailio enforces
this via the `permissions` module reading the Postgres `address` table (grp 1,
CIDR-aware). This module rewrites that table from the current set of trunk IPs
and triggers a runtime reload (no Kamailio restart, no call drop).

Reload trigger: we touch a file that a host systemd path-unit watches, which runs
`kamcmd permissions.addressReload`. This avoids granting the dashboard container
access to Kamailio's root-owned control socket.

The `address` table lives in Postgres (Kamailio's db_postgres), independent of the
dashboard's own app DB — so this connects to KAMAILIO_DB_URL directly.

Env:
  KAMAILIO_DB_URL          default postgresql://lescall:lescall_dev@127.0.0.1:5432/lescall
  KAMAILIO_RELOAD_TRIGGER  default <repo>/data/kamailio_reload
"""
from __future__ import annotations

import ipaddress
import logging
import os
from pathlib import Path

from shared.db.database import _REPO_ROOT

logger = logging.getLogger("kamailio_acl")

KAMAILIO_DB_URL = os.environ.get(
    "KAMAILIO_DB_URL", "postgresql://lescall:lescall_dev@127.0.0.1:5432/lescall")
RELOAD_TRIGGER = Path(os.environ.get(
    "KAMAILIO_RELOAD_TRIGGER", str(_REPO_ROOT / "data" / "kamailio_reload")))
ACL_GROUP = 1  # matches allow_source_address("1") in kamailio.cfg


def _parse(cidr: str) -> tuple[str, int] | None:
    """'18.214.109.0/24' -> ('18.214.109.0', 24); bare IP -> /32. None if invalid."""
    cidr = cidr.strip()
    if not cidr:
        return None
    try:
        net = ipaddress.ip_network(cidr, strict=False)
        return str(net.network_address), net.prefixlen
    except ValueError:
        logger.warning("[KAM-ACL] skipping invalid CIDR %r", cidr)
        return None


def sync_carrier_acl(cidrs: list[str]) -> dict:
    """Rewrite the Kamailio `address` table (grp 1) from `cidrs` and trigger reload.

    Never raises into the caller — returns {synced, reloaded, error?} so a trunk
    apply still succeeds even if the Kamailio side is briefly unavailable.
    """
    parsed = [p for p in (_parse(c) for c in cidrs) if p]
    try:
        import psycopg
        with psycopg.connect(KAMAILIO_DB_URL, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM address WHERE grp=%s", (ACL_GROUP,))
                for ip, mask in parsed:
                    cur.execute(
                        "INSERT INTO address (grp, ip_addr, mask, port, tag) "
                        "VALUES (%s,%s,%s,0,'carrier')", (ACL_GROUP, ip, mask))
            conn.commit()
    except Exception as e:
        logger.warning("[KAM-ACL] address-table sync failed: %s", e)
        return {"synced": 0, "reloaded": False, "error": str(e)}

    reloaded = _trigger_reload()
    logger.info("[KAM-ACL] synced %d carrier CIDR(s); reload=%s", len(parsed), reloaded)
    return {"synced": len(parsed), "reloaded": reloaded}


def _trigger_reload() -> bool:
    try:
        RELOAD_TRIGGER.parent.mkdir(parents=True, exist_ok=True)
        RELOAD_TRIGGER.touch()
        return True
    except OSError as e:
        logger.warning("[KAM-ACL] could not touch reload trigger: %s", e)
        return False
