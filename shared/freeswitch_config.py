"""FreeSWITCH config generation for BYON trunks + DIDs (plan §3a).

Pure renderers (unit-testable, no side effects) plus apply_config() which
writes the XML under FS_CONF_DIR and reloads FreeSWITCH via fs_cli.

Generated files:
  sip_profiles/external/<trunk-name>.xml    one <gateway> per trunk
  dialplan/public/ai_inbound_<did>.xml      one extension per DID -> AI socket
  autoload_configs/acl.conf.xml             trusted_kamailio + provider list

CRITICAL: once Kamailio fronts FreeSWITCH, the external sofia profile enforces
apply-inbound-acl=trusted_kamailio. render_acl() therefore ALWAYS emits the
trusted_kamailio list (loopback + KAMAILIO_IP) no matter what CIDRs it is
given — regenerating the ACL must never lock Kamailio out.
"""

from __future__ import annotations

import os
import socket
import subprocess
from pathlib import Path
from xml.sax.saxutils import escape

from shared.phone import dialplan_expression, significant_digits


def _local_private_ip() -> str:
    """This host's primary private IP (no packets sent). Kamailio runs on the
    same host, so this is the correct trusted_kamailio default — an unset env
    var must never render an ACL that locks the proxy out (= total inbound
    outage after cutover)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        return ""


FS_CONF_DIR = os.environ.get("FS_CONF_DIR", "/etc/freeswitch")
FS_CLI = os.environ.get("FS_CLI", "fs_cli")
AI_SOCKET = os.environ.get("AI_SOCKET", "127.0.0.1:9000")
KAMAILIO_IP = os.environ.get("KAMAILIO_IP") or _local_private_ip()

RELOAD_COMMANDS = ("reloadxml", "sofia profile external rescan", "reloadacl")


def _attr(value) -> str:
    """Escape a value for use inside a double-quoted XML attribute."""
    return escape(str(value if value is not None else ""), {'"': "&quot;"})


def gateway_file_name(trunk: dict) -> str:
    return f"{trunk['name']}.xml"


def dialplan_file_name(number: dict) -> str:
    did = significant_digits(number["number"]) or number["number"].lstrip("+")
    return f"ai_inbound_{did}.xml"


def render_gateway(trunk: dict) -> str:
    """sip_profiles/external/<name>.xml — how FreeSWITCH reaches the carrier.

    Registration-mode trunks (register=1) get an `extension` param (the
    caller_id's significant digits) so registered-delivery INVITEs carry a
    routable destination_number. Caveat documented in deploy/RUNBOOK.md:
    registered delivery arrives at FreeSWITCH's Contact directly, bypassing
    the Kamailio edge — IP-auth trunks are the recommended BYON mode.
    """
    name = trunk["name"]
    realm = trunk.get("sip_domain") or ""
    proxy = trunk.get("proxy") or realm
    transport = (trunk.get("transport") or "udp").lower()
    if transport != "udp" and ";transport=" not in proxy:
        proxy = f"{proxy};transport={transport}"
    username = trunk.get("username") or ""
    password = trunk.get("password") or ""
    register = "true" if trunk.get("register") else "false"
    caller_id = trunk.get("caller_id") or ""

    lines = [
        "<include>",
        f'  <gateway name="{_attr(name)}">',
        f'    <param name="realm" value="{_attr(realm)}"/>',
        f'    <param name="proxy" value="{_attr(proxy)}"/>',
        f'    <param name="register" value="{register}"/>',
    ]
    if username:
        lines += [
            f'    <param name="username" value="{_attr(username)}"/>',
            f'    <param name="password" value="{_attr(password)}"/>',
            f'    <param name="from-user" value="{_attr(username)}"/>',
        ]
    if caller_id:
        lines.append(f'    <param name="from-domain" value="{_attr(realm)}"/>')
    if trunk.get("register") and caller_id:
        ext = significant_digits(caller_id)
        if ext:
            lines.append(f'    <param name="extension" value="{_attr(ext)}"/>')
    lines += [
        '    <param name="caller-id-in-from" value="true"/>',
        "  </gateway>",
        "</include>",
        "",
    ]
    return "\n".join(lines)


def render_inbound_dialplan(number: dict) -> str:
    """dialplan/public/ai_inbound_<did>.xml — route the DID to the AI socket app."""
    did = significant_digits(number["number"]) or number["number"].lstrip("+")
    expr = dialplan_expression(number["number"])
    return (
        "<include>\n"
        f'  <extension name="ai_inbound_{did}">\n'
        f'    <condition field="destination_number" expression="{_attr(expr)}">\n'
        '      <action application="answer"/>\n'
        '      <action application="sleep" data="500"/>\n'
        f'      <action application="socket" data="{_attr(AI_SOCKET)} async full"/>\n'
        "    </condition>\n"
        "  </extension>\n"
        "</include>\n"
    )


def render_acl(cidrs: list[str], kamailio_ip: str | None = None) -> str:
    """autoload_configs/acl.conf.xml.

    trusted_kamailio is what the external profile enforces; lescall_providers is
    kept for back-compat/diagnostics (the live carrier allow-list is Kamailio's).
    """
    kam_ip = KAMAILIO_IP if kamailio_ip is None else kamailio_ip
    trusted = ["127.0.0.1/32"]
    if kam_ip:
        trusted.append(kam_ip if "/" in kam_ip else f"{kam_ip}/32")

    def nodes(cidr_list):
        return "\n".join(
            f'      <node type="allow" cidr="{_attr(c.strip())}"/>'
            for c in cidr_list if c and c.strip()
        )

    return (
        '<configuration name="acl.conf" description="Network Lists">\n'
        "  <network-lists>\n"
        # Stock lists re-emitted so profiles referencing them (internal's
        # apply-inbound-acl=domains) keep working after we own this file.
        '    <list name="lan" default="allow">\n'
        '      <node type="deny" cidr="192.168.42.0/24"/>\n'
        '      <node type="allow" cidr="192.168.42.42/32"/>\n'
        "    </list>\n"
        '    <list name="domains" default="deny">\n'
        '      <node type="allow" domain="$${domain}"/>\n'
        "    </list>\n"
        '    <list name="trusted_kamailio" default="deny">\n'
        f"{nodes(trusted)}\n"
        "    </list>\n"
        '    <list name="lescall_providers" default="deny">\n'
        f"{nodes(cidrs)}\n"
        "    </list>\n"
        "  </network-lists>\n"
        "</configuration>\n"
    )


def _fs_reload() -> dict:
    """Run the reload commands via fs_cli; report per-command success."""
    results = {}
    for cmd in RELOAD_COMMANDS:
        try:
            proc = subprocess.run(
                [FS_CLI, "-x", cmd], capture_output=True, text=True, timeout=15,
            )
            results[cmd] = {
                "ok": proc.returncode == 0,
                "output": (proc.stdout or proc.stderr or "").strip()[:500],
            }
        except Exception as exc:
            results[cmd] = {"ok": False, "output": repr(exc)}
    return results


def apply_config(
    trunk: dict,
    numbers: list[dict],
    reload: bool = True,
    all_provider_cidrs: list[str] | None = None,
    conf_dir: str | None = None,
) -> dict:
    """Write gateway + per-DID dialplan (+ optionally the ACL), then reload FS.

    File-write failures raise (the API turns that into an error response);
    reload failures are captured in the result, not raised.
    """
    base = Path(conf_dir or FS_CONF_DIR)
    written: list[str] = []

    gw_path = base / "sip_profiles" / "external" / gateway_file_name(trunk)
    gw_path.parent.mkdir(parents=True, exist_ok=True)
    gw_path.write_text(render_gateway(trunk), encoding="utf-8")
    written.append(str(gw_path))

    for number in numbers:
        dp_path = base / "dialplan" / "public" / dialplan_file_name(number)
        dp_path.parent.mkdir(parents=True, exist_ok=True)
        dp_path.write_text(render_inbound_dialplan(number), encoding="utf-8")
        written.append(str(dp_path))

    if all_provider_cidrs is not None:
        acl_path = base / "autoload_configs" / "acl.conf.xml"
        acl_path.parent.mkdir(parents=True, exist_ok=True)
        acl_path.write_text(render_acl(all_provider_cidrs), encoding="utf-8")
        written.append(str(acl_path))

    result: dict = {"written": written, "reloaded": False, "reload_results": {}}
    if reload:
        result["reload_results"] = _fs_reload()
        result["reloaded"] = all(r["ok"] for r in result["reload_results"].values())
    return result
