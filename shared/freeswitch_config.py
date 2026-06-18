"""Generate FreeSWITCH config from DB-defined trunks and phone numbers.

Powers the dashboard's "add a Plivo number / trunk and configure FreeSWITCH
automatically" feature. Two outputs per trunk/number:

  1. A SIP gateway XML (sip_profiles/external/<trunk>.xml) — how FS reaches the provider.
  2. An inbound-DID dialplan entry (dialplan/public/) routing the DID to the AI
     socket server (same handoff the live inbound path uses: socket 127.0.0.1:9000).

`render_*` build the XML strings (pure, testable). `apply_config` writes them to
FS_CONF_DIR and reloads via fs_cli. Set FS_CONF_DIR / FS_CLI via env to point at the
real FreeSWITCH (default /etc/freeswitch, fs_cli on PATH).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

FS_CONF_DIR = Path(os.environ.get("FS_CONF_DIR", "/etc/freeswitch"))
FS_CLI = os.environ.get("FS_CLI", "fs_cli")
AI_SOCKET = os.environ.get("AI_SOCKET", "127.0.0.1:9000")

# Public SIP endpoint advertised to providers (shown in the dashboard).
PUBLIC_SIP_IP = os.environ.get("PUBLIC_SIP_IP", "")          # resolved lazily if empty
SIP_INBOUND_PORT = os.environ.get("SIP_INBOUND_PORT", "5080")  # FS external profile
RTP_PORT_RANGE = os.environ.get("RTP_PORT_RANGE", "16384-32768")
PROVIDER_ACL = "lescall_providers"


def _public_ip() -> str:
    if PUBLIC_SIP_IP:
        return PUBLIC_SIP_IP
    # GCP metadata, then external services, then a placeholder.
    import urllib.request
    for url, hdr in (
        ("http://metadata.google.internal/computeMetadata/v1/instance/"
         "network-interfaces/0/access-configs/0/external-ip", {"Metadata-Flavor": "Google"}),
        ("https://api.ipify.org", {}),
    ):
        try:
            req = urllib.request.Request(url, headers=hdr)
            return urllib.request.urlopen(req, timeout=3).read().decode().strip()
        except Exception:
            continue
    return "<server-public-ip>"


def server_sip_info(provider_cidrs: list[str] | None = None) -> dict:
    """What the operator must configure on the provider side (shown in the UI)."""
    ip = _public_ip()
    return {
        "public_ip": ip,
        "inbound": {
            "sip_uri": f"sip:<DID>@{ip}:{SIP_INBOUND_PORT}",
            "host": ip,
            "port": SIP_INBOUND_PORT,
            "transport": "UDP",
            "note": "Point your provider's DID/origination to this SIP URI.",
        },
        "outbound": {
            "note": "We register/connect to YOUR provider trunk (configured below). "
                    "Whitelist this server IP on the provider so it accepts our calls.",
            "from_ip": ip,
        },
        "rtp_port_range": f"{RTP_PORT_RANGE}/udp",
        "acl": {
            "list_name": PROVIDER_ACL,
            "allowed_cidrs": provider_cidrs or [],
            "note": "Provider signaling IPs allowed to send us inbound calls.",
        },
        "firewall_note": f"Open UDP {SIP_INBOUND_PORT} (SIP) and UDP {RTP_PORT_RANGE} (RTP).",
    }


def render_acl(cidrs: list[str]) -> str:
    """Full acl.conf.xml: keep the stock lan/domains lists + our provider list."""
    nodes = "\n".join(f'      <node type="allow" cidr="{c.strip()}"/>'
                      for c in cidrs if c.strip()) or "      <!-- no provider IPs yet -->"
    return (
        '<configuration name="acl.conf" description="Network Lists">\n'
        '  <network-lists>\n'
        '    <list name="lan" default="allow"/>\n'
        '    <list name="domains" default="deny">\n'
        '      <node type="allow" domain="$${domain}"/>\n'
        '    </list>\n'
        f'    <list name="{PROVIDER_ACL}" default="deny">\n'
        f'{nodes}\n'
        '    </list>\n'
        '  </network-lists>\n'
        '</configuration>\n'
    )


def render_gateway(trunk: dict) -> str:
    """SIP gateway XML for a trunk row (see schema: trunks)."""
    register = "true" if trunk.get("register") else "false"
    lines = [
        '<include>',
        f'  <gateway name="{trunk["name"]}">',
        f'    <param name="realm" value="{trunk["sip_domain"]}"/>',
        f'    <param name="proxy" value="{trunk.get("proxy") or trunk["sip_domain"]}"/>',
        f'    <param name="register" value="{register}"/>',
    ]
    if trunk.get("username"):
        lines.append(f'    <param name="username" value="{trunk["username"]}"/>')
    if trunk.get("password"):
        lines.append(f'    <param name="password" value="{trunk["password"]}"/>')
    if trunk.get("caller_id"):
        lines.append(f'    <param name="from-user" value="{trunk["caller_id"]}"/>')
    lines += ['  </gateway>', '</include>']
    return "\n".join(lines) + "\n"


def render_inbound_dialplan(number: dict) -> str:
    """public-context dialplan: route an inbound DID to the AI socket server."""
    did = number["number"].lstrip("+")
    return (
        "<include>\n"
        f'  <extension name="ai_inbound_{did}">\n'
        f'    <condition field="destination_number" expression="^\\+?{did}$">\n'
        '      <action application="answer"/>\n'
        '      <action application="sleep" data="500"/>\n'
        f'      <action application="socket" data="{AI_SOCKET} async full"/>\n'
        "    </condition>\n"
        "  </extension>\n"
        "</include>\n"
    )


def sip_trunk_instructions(trunk: dict) -> dict:
    """What the operator must configure on the provider side (shown in the UI)."""
    return {
        "provider": trunk.get("provider", "plivo"),
        "sip_domain": trunk["sip_domain"],
        "auth_mode": "REGISTER (username/password)" if trunk.get("register")
                     else "IP authentication (allowlist this server's public IP)",
        "freeswitch_signaling_port": "5080 (external profile)",
        "rtp_port_range": "16384-32768/udp",
        "notes": [
            "Point the Plivo outbound trunk / app at this server's public IP on UDP 5080.",
            "For inbound, set the Plivo number's app to deliver SIP to sip:<did>@<server-ip>:5080.",
            "Open SIP (5080/udp) and the RTP range (16384-32768/udp) in the firewall.",
        ],
    }


def apply_config(trunk: dict, numbers: list[dict], reload: bool = True,
                 all_provider_cidrs: list[str] | None = None) -> dict:
    """Write gateway + dialplan (+ ACL) files and reload FreeSWITCH.

    all_provider_cidrs: the full set of allowed provider CIDRs across all trunks
    (the ACL is a single FreeSWITCH-global list). When provided, acl.conf.xml is
    regenerated and reloaded.
    """
    written = []
    try:
        gw_path = FS_CONF_DIR / "sip_profiles" / "external" / f'{trunk["name"]}.xml'
        gw_path.parent.mkdir(parents=True, exist_ok=True)
        gw_path.write_text(render_gateway(trunk))
        written.append(str(gw_path))

        for num in numbers:
            did = num["number"].lstrip("+")
            dp_path = FS_CONF_DIR / "dialplan" / "public" / f"ai_inbound_{did}.xml"
            dp_path.parent.mkdir(parents=True, exist_ok=True)
            dp_path.write_text(render_inbound_dialplan(num))
            written.append(str(dp_path))

        if all_provider_cidrs is not None:
            acl_path = FS_CONF_DIR / "autoload_configs" / "acl.conf.xml"
            acl_path.parent.mkdir(parents=True, exist_ok=True)
            acl_path.write_text(render_acl(all_provider_cidrs))
            written.append(str(acl_path))
    except OSError as e:
        # e.g. not running as the freeswitch user / FS_CONF_DIR not writable here.
        return {"written": written, "reloaded": False, "error": str(e)}

    reloaded = False
    if reload:
        ok1, out1 = _fs("reloadxml")
        ok2, out2 = _fs("sofia profile external rescan")
        if all_provider_cidrs is not None:
            _fs("reloadacl")
        reloaded = ok1 and ok2
        if not reloaded:
            return {"written": written, "reloaded": False, "error": out1 or out2}
    return {"written": written, "reloaded": reloaded}


def _fs(command: str) -> tuple[bool, str]:
    """Run a FreeSWITCH API command: prefer the fs_cli binary, fall back to ESL
    over TCP (works from inside a container where fs_cli isn't installed)."""
    try:
        r = subprocess.run([FS_CLI, "-x", command], timeout=10, capture_output=True, text=True)
        if r.returncode == 0:
            return True, r.stdout
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        pass
    try:
        from shared.esl import esl_api
        return esl_api(command)
    except Exception as e:
        return False, repr(e)
