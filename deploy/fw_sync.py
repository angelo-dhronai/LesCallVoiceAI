#!/usr/bin/env python3
"""Reconcile the GCP firewall rule for SIP (UDP 5060 -> Kamailio) from registered carriers.

Source of truth = Kamailio's Postgres `address` table (grp 1), which the dashboard
already keeps in sync with every tenant's trunk `provider_ips`. This script sets the
firewall rule's --source-ranges to BASE_RANGES ∪ (registered carrier CIDRs), so a new
carrier's signaling IPs are admitted at the cloud edge automatically.

SECURITY / least privilege:
- Runs on the HOST (triggered by a systemd path-unit), NOT in the dashboard container,
  so cloud credentials never enter the container.
- Uses whatever gcloud account is active, or a dedicated service account if
  FW_SYNC_GCLOUD_ACCOUNT is set (recommended for prod: a SA with only
  compute.firewalls.get/list/update). Best-effort: never throws; logs + exits non-zero
  on failure so registration still succeeds and the issue is visible in logs.

Env:
  FW_RULE                  default allow-sip-5060-kamailio
  FW_BASE_RANGES           comma CIDRs always kept (default: current Plivo ranges)
  KAMAILIO_DB_URL          Postgres url (default postgresql://lescall:lescall_dev@127.0.0.1:5432/lescall)
  FW_SYNC_GCLOUD_ACCOUNT   optional: gcloud account/SA email to use
  GOOGLE_APPLICATION_CREDENTIALS / CLOUDSDK_*  honored by gcloud as usual
"""
from __future__ import annotations

import ipaddress
import os
import subprocess
import sys

FW_RULE = os.environ.get("FW_RULE", "allow-sip-5060-kamailio")
DB_URL = os.environ.get("KAMAILIO_DB_URL", "postgresql://lescall:lescall_dev@127.0.0.1:5432/lescall")
GCLOUD_ACCOUNT = os.environ.get("FW_SYNC_GCLOUD_ACCOUNT", "")
# Default base = the Plivo ranges originally configured, so nothing pre-existing breaks.
BASE_RANGES = os.environ.get(
    "FW_BASE_RANGES",
    "18.214.109.0/24,13.52.9.0/24,204.89.151.64/26,204.89.151.128/25,"
    "52.220.63.176/28,204.89.149.64/27,204.89.149.128/27,13.238.202.192/26",
)


def _norm(cidr: str) -> str | None:
    cidr = cidr.strip()
    if not cidr:
        return None
    try:
        return str(ipaddress.ip_network(cidr, strict=False))
    except ValueError:
        return None


def _registered_cidrs() -> list[str]:
    import psycopg
    out = []
    with psycopg.connect(DB_URL, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT ip_addr, mask FROM address WHERE grp=1")
            for ip, mask in cur.fetchall():
                n = _norm(f"{ip}/{mask}")
                if n:
                    out.append(n)
    return out


def _gcloud(args: list[str]) -> subprocess.CompletedProcess:
    cmd = ["gcloud"] + args + ["--format=value(sourceRanges.list())"]
    if GCLOUD_ACCOUNT:
        cmd += [f"--account={GCLOUD_ACCOUNT}"]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60)


def main() -> int:
    desired = sorted({c for c in (
        [_norm(x) for x in BASE_RANGES.split(",")] + _registered_cidrs()) if c})
    if not desired:
        print("fw_sync: no ranges to apply; leaving rule unchanged")
        return 0

    # current ranges
    cur = _gcloud(["compute", "firewall-rules", "describe", FW_RULE])
    if cur.returncode != 0:
        print(f"fw_sync: describe failed: {cur.stderr.strip()}", file=sys.stderr)
        return 2
    current = sorted({c.strip() for c in cur.stdout.replace(";", ",").split(",") if c.strip()})

    if current == desired:
        print(f"fw_sync: {FW_RULE} already up to date ({len(desired)} ranges)")
        return 0

    upd = subprocess.run(
        ["gcloud", "compute", "firewall-rules", "update", FW_RULE,
         f"--source-ranges={','.join(desired)}"]
        + ([f"--account={GCLOUD_ACCOUNT}"] if GCLOUD_ACCOUNT else []),
        capture_output=True, text=True, timeout=120)
    if upd.returncode != 0:
        print(f"fw_sync: update failed: {upd.stderr.strip()}", file=sys.stderr)
        return 3
    print(f"fw_sync: {FW_RULE} updated -> {len(desired)} ranges")
    return 0


if __name__ == "__main__":
    sys.exit(main())
