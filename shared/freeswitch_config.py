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


def apply_config(trunk: dict, numbers: list[dict], reload: bool = True) -> dict:
    """Write gateway + dialplan files and reload FreeSWITCH. Returns paths written."""
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
    except OSError as e:
        # e.g. not running as the freeswitch user / FS_CONF_DIR not writable here.
        return {"written": written, "reloaded": False, "error": str(e)}

    reloaded = False
    if reload:
        ok1, out1 = _fs("reloadxml")
        ok2, out2 = _fs("sofia profile external rescan")
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
