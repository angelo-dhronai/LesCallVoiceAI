"""The FreeSWITCH originate string — ONE definition, shared by every caller.

Extracted from dashboard/server.py, which was the only place that knew how to
build one. It stops being the only place the moment a second surface can dial:
the admin plane's "test this number" needs the identical channel variables, and
a second hand-written copy is how they drift.

What must not drift, and why (RUNBOOK §33)
------------------------------------------
`lescall_dest` carries the DIALLED NUMBER on the channel. It is already in the
dial string, but FreeSWITCH sets the resulting channel's
Caller-Destination-Number to the DIALPLAN EXTENSION (`outbound_ai_connect`),
not to the number — so the telephony bridge has no first-hand way to know who
this leg is calling. It used to fall back to "the most recently logged dial",
which during a burst is somebody else: 44 calls greeted a real person by a
stranger's name before 2026-08-29. A channel variable cannot race — it belongs
to this leg and no other.

Omitting it from a second originate builder would reintroduce exactly that bug
on the new surface only, which is the kind of regression that takes months to
be believed. Hence this module, and hence `test_originate_carries_the_dialled_number`.
"""

from __future__ import annotations

import os

# The dialplan extension every AI outbound leg is bridged into. Not an env var
# in the original either — it is the name of a real extension in the shipped
# dialplan, so changing it means editing FreeSWITCH config too.
OUTBOUND_DP_EXT = "outbound_ai_connect"
OUTBOUND_DP_CTX = "default"

# Founding-account fallbacks, used only when a caller supplies neither. Every
# tenant-scoped path passes its own number and gateway explicitly.
DEFAULT_CALLER_ID = os.environ.get("OUTBOUND_CALLER_ID", "918065953160")
DEFAULT_GATEWAY = os.environ.get("OUTBOUND_GATEWAY_NAME", "plivo-outbound")

ORIGINATE_TIMEOUT_S = 45


def originate_cmd(phone: str, agent_slug: str = "", campaign_id: str = "",
                  caller_id: str = "", gateway_name: str = "") -> str:
    """Build the `originate` for one outbound AI leg.

    Egress is ALWAYS through a named gateway (`sofia/gateway/<name>/…`) so the
    call uses that trunk's own credentials and caller-id authorisation — never
    a bare domain that could resolve to the shared profile and send a caller ID
    the carrier has not authorised for that account.
    """
    caller_id = caller_id or DEFAULT_CALLER_ID
    gateway_name = gateway_name or DEFAULT_GATEWAY

    dest_digits = "".join(c for c in str(phone or "") if c.isdigit())[:20]
    chan_vars = [
        f"origination_caller_id_number={caller_id}",
        f"originate_timeout={ORIGINATE_TIMEOUT_S}",
        "ignore_early_media=true",
    ]
    if dest_digits:
        chan_vars.append(f"lescall_dest={dest_digits}")
    if agent_slug:
        chan_vars.append(f"lescall_agent_slug={agent_slug}")
    if campaign_id:
        chan_vars.append(f"lescall_campaign_id={campaign_id}")
    return (
        "originate {" + ",".join(chan_vars) + "}"
        f"sofia/gateway/{gateway_name}/{phone} "
        f"{OUTBOUND_DP_EXT} XML {OUTBOUND_DP_CTX}"
    )
