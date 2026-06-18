# Kamailio SIP proxy / dispatcher (in front of FreeSWITCH)

Carriers (Plivo, etc.) now point trunks at **this proxy's public IP on UDP 5060**,
never directly at FreeSWITCH. Kamailio relays signaling to FreeSWITCH via the
`dispatcher` module; **RTP/media flows directly between carrier and FreeSWITCH**
(Kamailio is signaling-only).

```
Carrier ──SIP──> Kamailio  35.207.206.252:5060  (bind 10.160.0.6, advertise public)
                    │ dispatcher (setid 1, OPTIONS keepalive)
                    ▼
              FreeSWITCH external  10.160.0.6:5080   (trusts ONLY Kamailio via ACL)
   RTP/media ◀───────────────────────────────────▶ Carrier   (direct, not via Kamailio)
```

Files (version-controlled here, deployed to `/etc/kamailio/`):
- `kamailio.cfg` — routing logic (maxfwd/loop, pike flood, carrier allow-list, dispatcher relay, failover)
- `dispatcher.list` — backend FreeSWITCH nodes (setid 1)

## Adding a second FreeSWITCH node (zero redesign)
1. Append to `/etc/kamailio/dispatcher.list` with the **same setid 1**:
   ```
   1 sip:10.160.0.7:5080 0 0
   ```
2. `sudo kamcmd dispatcher.reload`
Dispatcher round-robins + fails over across all setid-1 nodes automatically. The new
node's FreeSWITCH must trust Kamailio (`apply-inbound-acl=trusted_kamailio`, include
the proxy IP in `acl.conf.xml`).

## Carrier IP allow-list
Edit `route[CARRIER_ACL]` in `kamailio.cfg` — add each carrier's SIP signaling IP
(placeholder block clearly marked), then `sudo kamcmd cfg.reload` or restart Kamailio.
(The platform can later push provider IPs from the dashboard into this list.)

## rtpengine (future, only if media must be relayed)
Today media is direct (carrier ↔ FreeSWITCH, IP-auth trunk, no NAT) so no relay is
needed. When FreeSWITCH nodes run on OTHER hosts or endpoints are NAT'd: install
`rtpengine`, `loadmodule "rtpengine.so"`, set `modparam("rtpengine","rtpengine_sock",...)`,
and call `rtpengine_manage()` at the marked spots in `request_route` (offer) and
`onreply_route` (answer).

## Firewall
Open inbound **UDP 5060** (SIP to Kamailio) in the GCP firewall, plus the existing
**UDP 16384–32768** (RTP to FreeSWITCH). Carriers no longer need UDP 5080 open.

## Verify
```
sudo kamcmd dispatcher.list          # FreeSWITCH backend shows FLAGS: AP (active+probing)
systemctl is-active kamailio
# end-to-end OPTIONS -> Kamailio:5060 -> FreeSWITCH -> 200 OK
```

## Change summary (what was modified on the host)
- FreeSWITCH internal profile moved 5060 → **5070** (`vars.xml`) to free 5060 for Kamailio.
- FreeSWITCH external profile: `apply-inbound-acl=trusted_kamailio` added; `acl.conf.xml`
  gained a `trusted_kamailio` list (allows 127.0.0.1 + 10.160.0.6 only).
- Kamailio installed, configured, enabled on boot.
- Dashboard `server-info` now advertises `:5060`.

## Rollback (restore direct carrier → FreeSWITCH:5080)
```
# 1. stop + disable Kamailio
sudo systemctl disable --now kamailio

# 2. restore FreeSWITCH config from backup
sudo tar xzf /home/angelo/lescall/backups/pre-kamailio/etc-freeswitch.tar.gz -C /etc
#    (or restore individual files: vars.xml.bak, external.xml.bak, acl.conf.xml.bak)

# 3. reload FreeSWITCH (internal back to 5060, external ACL removed)
sudo fs_cli -x reloadxml
sudo fs_cli -x "sofia profile internal restart"
sudo fs_cli -x "sofia profile external restart"

# 4. revert dashboard server-info to :5080
#    set SIP_INBOUND_PORT=5080 (env) or git revert the freeswitch_config change, rebuild dashboard_api
# 5. point the carrier (Plivo) trunk back at <public-ip>:5080
```

## IMPORTANT post-cutover step
Repoint your carrier (Plivo) trunk origination from `<public-ip>:5080` to
**`<public-ip>:5060`**. Until then, inbound calls won't arrive (FreeSWITCH no longer
accepts direct carrier traffic on 5080 — only Kamailio).
