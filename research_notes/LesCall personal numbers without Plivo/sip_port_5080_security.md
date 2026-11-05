# FreeSWITCH SIP port 5080: real security state and safe fix

Evidence collected 2026-11-03 on `lescall-agent` (read-only). Tags: **[V]** verified on this host or in source, **[I]** inferred, **[U]** unknown.

Bottom line: the previous audit's config claims are correct, but its implied severity is not established.

- FreeSWITCH itself admits any INVITE, and only the GCP VPC firewall can be protecting it. Those rules still cannot be read from this host.
- The port WAS wide open in June 2026: 4,257 scanner calls in about 49 hours, 33 hours of answered time.
- Since 2026-06-17 08:33 UTC there is not one non-DID call in the CDR, and passive capture shows no internet noise on SIP ports today. The most likely state now is that the VPC already restricts 5080. This is strong inference, not proof.
- DevOps must read the firewall first. There is one trap: the manual ACL fix gets wiped by the dashboard unless `trunks.provider_ips` is filled too.

## Q1. Current configuration: what is listening and what does a stranger's INVITE reach?

### Takeaway
There is no authentication or ACL at FreeSWITCH level on 5080. A catch-all answers every INVITE and hands it to the AI socket. No path from `public` reaches the PSTN, because the only transfers into `default` are shadowed by that catch-all. Removing the catch-all without care would bring those transfers back to life.

### Cited Findings
External profile:
- [V] `auth-calls=false` at `/etc/freeswitch/sip_profiles/external.xml:58`. There is no `apply-inbound-acl` and no `apply-nat-acl` in the file. The compiled `/var/log/freeswitch/freeswitch.xml.fsxml` (24 Oct) agrees.
- [V] `context=public` (line 30), `sip-port=5080` (line 28, `vars.xml:428`), `sip-ip=10.160.0.8`, `ext-sip-ip=ext-rtp-ip=35.207.225.237`, `sip-trace=no` (line 23), TLS off (`vars.xml:430`).

Internal profile:
- [V] `/etc/freeswitch/sip_profiles/internal.xml`: 5060 (`vars.xml:422`), `auth-calls=true` (`vars.xml:421`, line 279), `apply-inbound-acl=domains` (line 128), `context=public` (line 87), `apply-nat-acl=nat.auto` (line 101), WS :5066 (line 321), WSS :7443 (line 325).
- [V] `default_password` is not the vendor `1234` and is 16 characters long (value not printed).
- [V] No channel used this profile: `sofia/internal` appears 0 times in `freeswitch.log.1` (2026-08-01 to 11-03). All inbound traffic arrives on external:5080.
- [V] `deploy/RUNBOOK.md` (the "origination URI must point at :5060" line, about 4772) conflicts with `calling-agent/.env:109` `PUBLIC_SIP_PORT=5080` and with this traffic. That RUNBOOK line is stale.

ACL file:
- [V] `/etc/freeswitch/autoload_configs/acl.conf.xml:1-18` holds `lan`, `domains`, `trusted_kamailio` (127.0.0.1, 10.160.0.8) and `lescall_providers` (default deny, EMPTY). No profile references the last two.
- [V] All 6 rows in `trunks` are `provider=plivo` with blank `provider_ips`, across 2 SIP domains.

Dialplan:
- [V] `dialplan/public.xml:16-34`: first extension `plivo_inbound` matches `.*` with no `continue`. It runs answer, sleep 1000, `record_session /tmp/dialplan_test.wav`, then `socket 127.0.0.1:9000 async full`. Compiled order confirms it is first.
- [V] Everything after it is dead today: `unloop`, `public_extensions` (`10[01][0-9]` → `transfer XML default`, lines 60-64), `public_conference_extensions` (lines 66-70), `public_did` (`dialplan/public/00_inbound_did.xml:3,15`), and all five `ai_inbound_<DID>` files. `check_auth` and `transfer_to_default` are XML-commented (lines 80-92).
- [V] `/tmp/dialplan_test.wav` exists, mode 0660 owner freeswitch, last written 03:01 today. Every inbound call overwrites it, so the last caller's audio sits in /tmp. This is a leftover diagnostic (comment at lines 25-29).

Toll fraud:
- [V] `socket_server.py` only issues `playback`, `record_session`, `stop_record_session` and `hangup` (lines 332, 728, 1200, 859). `ws_audio_receiver.py` and `conversation_call_bridge.py` contain no bridge or transfer execution; their "originate" hits are comments.
- [V] Outbound originate requires ESL. `event_socket.conf.xml:30,37` sets `listen-ip=127.0.0.1`, a non-default password and `apply-inbound-acl=loopback.auto`; `ss` confirms 127.0.0.1:8021.
- [V] FreeSWITCH source: REFER on a one-legged call returns 403, "Cannot Blind Transfer 1 Legged calls" (`mod_sofia/sofia.c:9723` on [master](https://raw.githubusercontent.com/signalwire/freeswitch/master/src/mod/endpoints/mod_sofia/sofia.c)). The installed build is 1.11.0-release; I read master, not that tag.
- [V] `default.xml:347-349` (`sip_uri`) and `default/01_example.com.xml:8,17,26` (`sofia/gateway/${default_gateway}`) exist but are not reachable from `public` while the catch-all stands.

Listeners and host firewall (`sudo ss -ltnup`):
- [V] FreeSWITCH: 10.160.0.8 on 5060, 5080 (UDP+TCP), 5066, 7443, 8081, 8082 (verto); 127.0.0.1:8021; UDP 0.0.0.0:1337.
- [V] Loopback only: 9000, 9001, 9004, 5432, 8090, 8081 (docker-proxy).
- [V] All interfaces: 80, 443, 22, 8080 (python, lescall-api), 20201/20202 (Ops Agent), 5355.
- [V] `iptables -S`: INPUT policy ACCEPT with no rules except Docker chains; `nft` shows the same. The host filters nothing.
- [V] Metadata: VM `lescall-agent`, zone asia-south1-c, project `lescall`, network `default`, tags only `http-server, https-server`, attached SA `lescall-recsync@`, scope `cloud-platform`.

### Inferences
- [I] Since the VM's only tags are http/https, whatever lets Plivo reach 5080 is either an untagged rule (applies to all instances), a rule on one of those two tags, or a rule targeted by service account.

### Gaps
- [U] The purpose of FreeSWITCH's UDP 1337 socket; I did not investigate it. DevOps should confirm it is closed at the VPC.

## Q2. Can anyone on the internet reach it?

### Takeaway
It was open in June, and is very probably restricted now. The rules cannot be read here. Treat it as "unverified, probably filtered" until DevOps runs the commands below and one outside OPTIONS test.

### Cited Findings
gcloud attempts (all read-only, none altered config):
- [V] Active account: reauthentication failed.
- [V] `--account=927969746216-compute@...`: metadata 404; this SA is no longer attached.
- [V] `--account=lescall-recsync@...` (the VM's attached identity): `Required 'compute.firewalls.list' permission`. I stopped there and did not attempt impersonation or other accounts.

Evidence it WAS open:
- [V] CDR `/var/log/freeswitch/cdr-csv/*`, 6,318 rows, 2026-06-15 to 11-03. 4,257 rows in context `public` have destinations that are not DIDs, all between 06-15 07:05 and 06-17 08:33 UTC. Details in Q3.

Evidence it is filtered now:
- [V] Zero non-DID public rows from 06-17 08:33 to today, while 410 legitimate public calls were logged from July to November. A world-open answering SIP port going 4.5 months without one scanner INVITE would be very unusual.
- [V] `freeswitch.log.1` (08-01 to 11-03): 307 calls processed in `public`, all to the 4 DIDs called in that window. All 1,279 AUDIO RTP lines point to 64 peers, every one in 204.89.151.128-191. Every other IP in the whole log is ours, Plivo's two signalling /24s, or a private or special-use address. A scanner's SDP would show up here.
- [V] Passive header-only capture on `ens4`, about 10.5 minutes in two runs. First run, 110 s: 0 packets on 5060, 5080, 5066, 7443, 8021, 8080, 8082. Second run (all inbound SYNs plus UDP 5060-5090), 08:25:10-08:33:59 UTC: nothing on any port except 15 SYNs to :22 from 6 sources, 5 to :443 from 3, and 3 to :80 from 3. Internet noise reaches the VM where the VPC allows it. Short window, so supporting evidence only.
- [V] Git history on branch `origin/restructure-and-dashboard`: commits 5c3615d and 340c34a (2026-06-18) reconcile a VPC rule `allow-sip-5060-kamailio` to source ranges `18.214.109.0/24, 13.52.9.0/24, 204.89.151.64/26, 204.89.151.128/25, 52.220.63.176/28, 204.89.149.64/27, 204.89.149.128/27, 13.238.202.192/26` through SA `lescall-fw@`. That README said "Carriers no longer need UDP 5080 open". `fw_sync.py` and the unit are absent from this checkout, and no `lescall-fw-sync` unit exists on the host.
- [V] `dashboard/admin_app.py:25-26` says the VPC is the only outer wall. Project memory `project_security_sprint_2026_08_03.md` records an external scan finding 8021/9000/9001/9004/8080/8081/5432 unreachable; 5080 was not in that list.

Evidence against, or ambiguous:
- [V] Memory `project_metering_docker_logging.md:52-53` (2026-07-13) says "zero packets with firewall wide open both ports". That is ambiguous: it may mean the host firewall or may refer to open-to-Plivo. It should not be dismissed until the rules are read.
- [V] The public IP has drifted across VM stop/start (`project_ephemeral_ip_drift.md`), and `vars.xml:283-284` still holds old IPs. I could not date a change against the 06-17 stop.

No decision on record:
- [V] `deploy/RUNBOOK.md:90-91`: "keep `apply-inbound-acl` OFF until Kamailio". Lines 157-159: "open UDP 5060 to the carrier IP ranges ... 5080 can be closed to the world after cutover", a cutover that never happened (`RUNBOOK` §38 note near line 4780: `provider_ips` inert, Kamailio inactive). `docs/MULTITENANT_BYON_PLAN.md:69,336,423` has the design only. `docs/PLATFORM.md:428` marks 8080 firewall unverified. No document records the actual 5080 rule.

### Inferences
- [I] Most likely state: a source-restricted rule (or a similar set) admits Plivo to 5080 and nothing else. Why the burst stopped on 06-17 is not explained by any record I found. An IP change or a firewall edit are both plausible.
- [I] Two facts about API and media ports: 8080 is listening on all interfaces, and no SYNs arrived in about 10 minutes, consistent with the August scan. 9000/9001/9004 are loopback-bound, so they are not reachable regardless of the firewall. [V for the binds]

### Gaps
- [U] The actual VPC rules. DevOps must run (read-only):
```bash
gcloud compute firewall-rules list --project=lescall \
  --format="table(name,direction,priority,sourceRanges.list(),allowed[].map().firewall_rule().list(),targetTags.list(),targetServiceAccounts.list(),disabled,logConfig.enable)"
gcloud compute instances network-interfaces get-effective-firewalls lescall-agent \
  --zone asia-south1-c --project=lescall
gcloud compute instances describe lescall-agent --zone asia-south1-c --project=lescall \
  --format="yaml(tags,networkInterfaces[0].accessConfigs,serviceAccounts)"
gcloud compute addresses list --project=lescall   # is 35.207.225.237 reserved static?
```
Check specifically: any rule allowing 5060, 5080, 8080, 8021, 5066, 7443, 8081, 8082, 1337, or `tcp`/`udp`/`all` from `0.0.0.0/0`; the `default-allow-internal` scope; hierarchical policies.

## Q3. Real evidence of exposure: who got in, and what did it cost?

### Takeaway
There was a real scanner burst on 15-17 June: 4,257 calls, 4,220 answered, 33.1 hours of answered time. Since then, nothing. Source IPs for June are gone. There is no evidence of dial-out, and no surviving `calls` row or recording that can be tied to a scanner.

### Cited Findings
June burst (CDR, source: `/var/log/freeswitch/cdr-csv/Master.csv*`):
- [V] 2,094 calls on 06-15, 1,673 on 06-16, 490 on 06-17. 4,220 had an answer stamp; 119,263 billsec (33.1 h).
- [V] Scanner signatures: destinations with the same last 4 digits and many prefixes, e.g. suffix `3636` (905 calls, up to 18 digits, leading `00`, `90`, `60`), `7742` (820, lengths up to 18), `3002` (210, 207 distinct callers, 15-digit `00…`), `6100` (2,242, in forms such as `11…`, `01…`, `91…`). This is the classic prefix sweep hunting for an outbound route.
- [V] Caller IDs were mostly 3-4 digits (`401`: 1,673 rows; `100`, `1000`, `2000`...`9001`), plus `boss`, `eXosip`, `zoiper`, `*`, `!`, `#`.
- [V] Hangups: NORMAL_UNSPECIFIED 2,813, NORMAL_CLEARING 1,383, MANDATORY_IE_MISSING 33, MEDIA_TIMEOUT 24.

Source IPs:
- [U] June source IPs cannot be counted. The CDR template has no IP field (`autoload_configs/cdr_csv.conf.xml:16`), `sip-trace` is off, and the retained FreeSWITCH logs start 2026-07-29.
- [V] What is retained: `phase14_runtime/logs/docker/socket_server.log*` plus `run_*`, about 5 days. 18 inbound ESL header blocks in the docker logs with `variable_sip_network_ip`, `sip_received_ip`, `Caller-Network-Addr`: only 4 IPs, 13.52.9.100/.101 and 18.214.109.140/.141. All 18 have user agent `Zentrunk`. All are inside Plivo's published `13.52.9.0/25` and `18.214.109.128/25`. Zero strangers.
- [V] All 214 channel names carrying a From-host in `freeswitch.log.1` show `zt.plivo.com`. From-host is spoofable, so this is weak on its own.

Did strangers get AI, and burn credits?
- [V] `calls` table: 1,139 rows, earliest 2026-06-19, so it has no burst-era rows. Owner is never blank. Inbound caller ID shorter than 10 digits: 0 rows. The 26 inbound rows over 13 digits all start `912`, are 14 digits, repeat a few suffixes and carry ordinary agents and durations from July to October. [I] A number-format artefact, not scanners; I did not trace the cause.
- [V] `recordings/`: 38 folders dated 06-15 to 06-17. Caller-part length across all 1,271 folders is 12 digits in 1,221, 14 in 45, 10 in 4; there are also 2 malformed names. No folder has a scanner-shaped 3-5 digit caller. The 4 ten-digit folders are all from 06-15 about 22:00, ~31 s each, with 127-character transcripts, one caller; I cannot classify that caller.

### Inferences
- [I] FreeSWITCH answered the scanners and handed them to the socket. The surviving recordings suggest most did not reach a full AI turn, but a pruned or differently behaving June pipeline cannot be excluded.
- [I] Scanners rarely send RTP. The greeting gate fires on the first audio frame (CLAUDE.md invariant). Whether FreeSWITCH-generated frames or comfort noise trip that gate for a silent caller is untested.
- [I] No toll fraud in June: the sweep's goal is an outbound route, and the catch-all gave none.

### Gaps
- [U] June Sarvam, Gemini and Plivo spend. Compare vendor consoles for 15-17 June. A daily metering record for those days was not found on this host.
- [U] Stranger INVITEs aimed at a real DID would look like normal calls in the CDR (no IP field). The RTP-peer check and the 5-day ESL sample say none since August, not before.

## Q4. Plivo's IP ranges and legitimate senders

### Takeaway
Published signalling covers the 4 IPs seen today, but Plivo's Zentrunk table has no Mumbai or Singapore row, and its docs disagree between pages. Ask Plivo for the authoritative Zentrunk origination list before enforcing. Nothing other than Plivo legitimately sends INVITEs to 5080.

### Cited Findings
- Zentrunk "IP Whitelisting Requirements": North California `13.52.9.0/25`, `216.120.187.128/26`; Virginia `18.214.109.128/25`, `18.215.142.0/26`, `204.89.148.128/26`; Frankfurt `3.120.121.128/26`; São Paulo `18.228.70.64/26`; Sydney `13.238.202.192/26`. Signalling 5060/5061, media 10000-30000. No CIDRs for Mumbai or Singapore, though both are listed as data centres. No statement on origination vs termination, and no date on the page — [Plivo technical specifications](https://www.plivo.com/docs/sip-trunking/concepts/technical-specifications.md); [Plivo SIP trunking FAQ](https://plivo.com/docs/faq/voice-api-and-SIP-trunking/sip-trunking)
- Plivo says to whitelist all addresses because calls may move region during a disruption — [Plivo SIP trunking docs, via search result](https://docs.plivo.com/docs/sip-trunking.md)
- Voice firewall page (does not mention Zentrunk): external SIP endpoints US+RoW `13.52.9.4-5/32`, `18.214.109.132-133/32`; India `13.205.159.55`, `15.206.154.163`, `43.204.247.115`, `43.205.14.55`. RTP media UDP 10000-30000: Mumbai `204.89.151.64/26`, `204.89.151.128/25`; Singapore `52.220.63.176/28`, `204.89.149.64/27`, `204.89.149.128/27`; San Jose `216.120.187.64/26`, `.128/26`, `.192/27`; Ashburn `204.89.148.64/26`, `.128/26`, `.192/27`; Frankfurt `3.120.121.128/26`; São Paulo `54.233.191.0/27`, `18.228.70.64/26`; Sydney `13.238.202.192/26` — [Plivo firewall network configuration](https://docs.plivo.com/docs/voice/concepts/firewall-network-configuration)
- Third-party list of 14 Plivo SIP ranges adds `54.233.191.0/27`, `18.136.1.128/26`, `204.89.149.128/27`, `15.207.90.192/31`, `204.89.151.128/27`, `204.89.151.160/27`, saying "Add all of them, not only the India ones" — [Bolna Plivo setup](https://www.bolna.ai/docs/sip-trunking/tutorials/plivo-setup). Not Plivo's own document.
- [V] Observed here: signalling only from 13.52.9.100/.101 and 18.214.109.140/.141 (US POPs, even for Indian DIDs); media only from 204.89.151.128-191 (Mumbai block). The media IPs are NOT in the Zentrunk table, so a list built from that table alone must never be applied to RTP.
- [V] Other senders: `shared/sentinel.py:257-275` sends OPTIONS from 10.160.0.8 to 10.160.0.8:5080 (not an INVITE). `sentinel --probe` (lines 433-475) loops through the carrier, so it comes back from a Plivo IP. Neither `callsim` nor `calltester` hits 5080 (no matches for `SOCK_DGRAM` or `5080`). Kamailio is `inactive`; `shared/kamailio_acl.py` writes a DB nobody enforces, and `connect_number` logs that as noise (RUNBOOK near 4858). `trunks`: all Plivo, no other tenant carrier.

### Inferences
- [I] Recommended allowlist for 5080 until Plivo answers: the Zentrunk table + the 4 India signalling /32s + Bolna's extras + the existing VPC rule's ranges. Wider than strictly needed, but still blocks the internet, and the failure mode of a too-narrow list is lost calls during a Plivo failover.

### Gaps
- [U] Plivo's authoritative current Zentrunk origination list for India. The old support article returned 403 to my fetch. DevOps should open a Plivo ticket.

## Q5. The fix, layered, in order

### Takeaway
Order: read rules → outside test → VPC (only if open) → populate `provider_ips` and code guard → FreeSWITCH ACL → dialplan guard → small hardening. All steps are mid-call safe if `sofia profile external restart` and FreeSWITCH restarts are avoided. Propose to the owner first; nothing has been changed.

### Cited Findings
Step 0, baseline. Run Q2's read-only commands. Record `fs_cli -x 'sofia status profile external'`.

Step 1, outside test, from DevOps' own machine only, OPTIONS only:
```bash
sipsak -s sip:probe@35.207.225.237:5080        # or: svmap 35.207.225.237 -p 5080 --method OPTIONS
nmap -sU -Pn -p 5080 --script sip-methods 35.207.225.237
```
A 200 OK means open from that IP, severity High. A timeout means filtered. Do not send INVITE. Repeat for TCP 5080, and for 5060, 8080, 8021, 7443, 8081, 8082.

Step 2, (a) GCP firewall. Only needed if Step 0 or 1 shows it open:
```bash
RANGES="13.52.9.0/25,216.120.187.128/26,18.214.109.128/25,18.215.142.0/26,204.89.148.128/26,3.120.121.128/26,18.228.70.64/26,13.238.202.192/26,13.205.159.55/32,15.206.154.163/32,43.204.247.115/32,43.205.14.55/32,<existing rule's ranges>"
gcloud compute firewall-rules create allow-sip-5080-carriers --project=lescall --network=default \
  --priority=900 --direction=INGRESS --action=ALLOW --rules=udp:5080,tcp:5080 \
  --source-ranges="$RANGES" --enable-logging
gcloud compute firewall-rules create deny-sip-5080-world --project=lescall --network=default \
  --priority=950 --direction=INGRESS --action=DENY --rules=udp:5080,tcp:5080 \
  --source-ranges=0.0.0.0/0 --enable-logging
```
- Lower number wins, so allow beats deny and both beat a default 1000 allow. The old rule can be removed afterwards.
- Rollback in seconds: `gcloud compute firewall-rules delete deny-sip-5080-world`.
- Do the same deny for 5060 and any other SIP/verto port found open. Nothing legitimate uses them ([V] 0 `sofia/internal` channels).
- If Step 0 shows it already restricted: only add logging for 48 h (`firewall-rules update <rule> --enable-logging`) and reconcile ranges.
- Live-call risk [I]: in-progress calls keep Plivo as their signalling peer, which stays allowed, and RTP is untouched. The danger is only an INVITE from an unlisted Plivo POP. Hence the broad list.

RTP, honest position:
- [V] FreeSWITCH uses the default 16384-32768 (`switch.conf.xml:150-151` commented out; observed local ports 16424-32752). Remote is Plivo's 10000-30000.
- Do not source-restrict RTP in the first pass. A wrong list causes dead-air calls that only show during a POP failover, and the media and signalling lists differ (Q4). Risk of leaving it: [I] sockets exist only per call, so a stranger must guess a live port and may at most inject noise; it does not expose call audio by itself.
- Optional later: restrict to the full Plivo media table after 2 weeks of firewall logs. Plivo requires media or calls have no audio — [Plivo firewall page](https://docs.plivo.com/docs/voice/concepts/firewall-network-configuration).

Step 3, the trap that must be fixed before the ACL:
- [V] `shared/freeswitch_config.py:131-171` `render_acl()` rewrites all of `acl.conf.xml` from a fixed template whenever any trunk is applied (`:256-260`, called from `dashboard/telephony_api.py:320` and `shared/telephony_admin.py:407,645`). File mtime Oct 24 = the last Connect. A hand-added list is deleted by the next tenant Connect. `lescall_providers` is rebuilt from `_all_provider_cidrs(db)`, which is blank today.
- [I] Therefore: write the ranges into `trunks.provider_ips` on all 6 rows and use `lescall_providers`. Do NOT add a custom list by hand.
- Code guard to propose: `apply_config` must refuse to write an empty `lescall_providers` once the profile enforces it, otherwise deleting a trunk or a blank field drops all inbound. New non-Plivo BYON trunks need `provider_ips` before Connect; `telephony_api.py:350-351` already tells users inbound from unlisted IPs is rejected, which becomes true only now. The `render_acl` docstring saying `trusted_kamailio` is enforced (`freeswitch_config.py:134`) is also false today.

Step 4, (b) FreeSWITCH ACL. `external.xml` is not generated, so it is safe to edit. After line 58:
```xml
<param name="apply-inbound-acl" value="lescall_providers"/>
```
Keep `auth-calls=false`. Source behaviour ([sofia.c master](https://raw.githubusercontent.com/signalwire/freeswitch/master/src/mod/endpoints/mod_sofia/sofia.c) 10570-10705):
- ACL match with auth-calls=false: call proceeds.
- ACL miss with auth-calls=false: immediate `403 Forbidden`, "IP %s Rejected by acl" at WARNING.
- With auth-calls=true: an ACL match authorises, and a miss falls to digest. Setting true without an ACL would 407 Plivo, which sends no digest for origination.
- `X-AUTH-IP` trust applies only with `apply-proxy-acl`, which is not set, so it cannot be spoofed in.
- IP auth, not digest, because Plivo origination offers an IP target and we have no directory user for it.

Apply in this order (never `sofia profile external restart`, which drops calls):
```bash
sudo cp -a /etc/freeswitch/sip_profiles/external.xml{,.bak-$(date +%s)}
fs_cli -x 'reloadacl'                                    # always re-reads XML first (mod_commands.c:1919)
fs_cli -x 'acl 13.52.9.100 lescall_providers'            # must be true
fs_cli -x 'acl 18.214.109.141 lescall_providers'         # must be true
fs_cli -x 'acl 8.8.8.8 lescall_providers'                # must be false
fs_cli -x 'sofia profile external rescan'
```
- Rescan on a running profile resets `acl_count` then re-reads `apply-inbound-acl` ([sofia.c](https://raw.githubusercontent.com/signalwire/freeswitch/master/src/mod/endpoints/mod_sofia/sofia.c) 4487, 4706, 5737). Source is master, so [I] for 1.11.0, but `rescan` has been used live on this host before for ext-ip changes (`project_ephemeral_ip_drift.md`). Check `sofia status profile external` and watch the next inbound.
- Mid-call safety: an in-dialog re-INVITE takes `sofia_handle_sip_i_reinvite` (sofia.c:1805-1808), not the ACL block, and BYE is not gated. An unknown list name returns deny (`switch_core.c:1373-1399`), so reload ACL before rescan.
- OPTIONS is answered 200 regardless of ACL (`sofia.c:11899-11916`). So sentinel keeps passing, and an outside OPTIONS test will NOT prove the ACL works. Only the VPC stops OPTIONS.
- Rollback: remove the param, `reloadxml`, `sofia profile external rescan`. Or `apply-inbound-acl=none` (sofia.c:5738).

Step 5, (c) dialplan guard. The RUNBOOK's "comment out plivo_inbound" (lines 80-88) alone is NOT safe:
- [V] It would make `public_extensions`, `public_conference_extensions` and `public_did` live, which transfer strangers into `default`.
- Delete or comment those three too. Add `/etc/freeswitch/dialplan/public/zz_reject_unknown.xml`:
```xml
<include><extension name="reject_unknown">
  <condition field="destination_number" expression=".*">
    <action application="respond" data="404"/>
  </condition></extension></include>
```
- `fs_cli -x reloadxml` affects only new calls. Rollback: restore `public.xml` from backup, `reloadxml`.
- [V] Safe for current traffic: all 5 `phone_numbers` rows have `ai_inbound_*` files, every public CDR row since 06-17 ends in a listed DID, and Connect generates the file for new DIDs. The DID forms in logs (`91…`, `+91…`) match `^(\+?\d*?)?DID$`.
- Side effects: answer delay goes 1000 ms → 500 ms; `record_session /tmp/dialplan_test.wav` disappears (good) and the `CASE R2`-style dialplan-recording diagnostics in `socket_server.py` change. Check call audit still records.
- Application layer: `conversation_call_bridge.py:298-395` sends an unregistered DID to `_resolve_active_agent_slug()`, the founder tenant's active agent. A registered DID without an agent gets the neutral fallback. Propose: inbound with DID not in `phone_numbers` hangs up instead of falling to the legacy path. This is what the audit called "gives them the founder's agent" [V].

Step 6, (d) other cheap hardening:
- Add `${sip_network_ip}` to the end of the CDR template so future audits have source IPs (`cdr_csv.conf.xml:16`; confirm the sentinel CDR reader tolerates a new column at the end).
- `switch.conf.xml:61,63`: `max-sessions=1000`, `sessions-per-second=30`. Far above host capacity (memory: 0.117 cores/call on e2-standard-4). Lower after measuring, using `fsctl max_sessions`, which is live.
- Bind `lescall-api` to 127.0.0.1 if nginx is its only client. Close 1337 and verify 5066, 7443, 8081, 8082 at the VPC. Consider disabling `mod_verto` (`modules.conf.xml:50`) if unused [U: I did not verify nothing uses it].
- Reserve the static IP if `addresses list` shows it is not (IP drift caused dead air before).
- `fail2ban`: low value once VPC and ACL exist; `log-auth-failures` is false. Could read "Rejected by acl" lines if the VPC cannot be restricted.
- `sip-trace`: leave off except for a short window. It would write caller numbers to logs.

Verification after each step:
1. Known good: a real phone call to each DID, plus `python -m shared.sentinel --probe` (the loopback probe costs a small Plivo charge). Confirm via `socket_server.log` that `variable_sip_network_ip` is in the list, an agent answers, and a `calls` row is created under the right owner.
2. Outside: OPTIONS from DevOps' IP gets a timeout (VPC). To test the ACL itself, DevOps may send one INVITE from their own IP to a non-DID, ONLY in a window where the VPC temporarily permits that IP, and expect 403 plus the "Rejected by acl" log line. Do this only with owner approval.
3. Outbound test call, to prove nothing else broke (ACL is inbound INVITE only).
4. Watch `/var/log/freeswitch/freeswitch.log` for `Rejected by acl` from any Plivo-looking IP for a week. This is the early warning of a missing POP, and comes only if the source is allowed by VPC.

### Inferences
- [I] Low-traffic window: inbound was about 3-4 calls per day in recent months, and many are daily probes around 03:00 UTC. Campaign dials are outbound and unaffected by the inbound ACL.

### Gaps
- [U] Whether FreeSWITCH 1.11.0 matches master for every line cited; verify with the `acl` and `sofia status` commands above.
- [U] Whether GCP drops tracked UDP flows at rule change. I did not verify this in Google's docs. Allow-before-deny ordering makes this moot for Plivo.

## Q6. Severity in plain words

### Takeaway
If the VPC already limits 5080 to Plivo (the likely state), this is defence-in-depth: Medium, and only one firewall change away from High. If the outside test shows 5080 open, it is High for abuse and cost, though not a dial-out or data-theft emergency. Either way the fix is cheap.

### Cited Findings
If an attacker can reach 5080, they CAN [V from code and config, I where noted]:
- Get an answered call and an AI session with no credential (catch-all, `auth-calls=false`). [I] Free AI minutes, and Sarvam and Gemini spend if audio flows. June shows the answer rate: 4,220 of 4,257.
- Choose the tenant by naming a real DID: `resolve_agent_slug` step 3 picks that tenant's agent. [I] The call is metered to that tenant and creates a `calls` row, transcript and Intelligence entry for them, so fake calls and junk leads appear in a tenant's dashboard.
- Unknown DID: reach the founder's active agent, with the row on the founder account.
- Spoof caller ID, because identity is the SIP From value. `_fetch_customer_context(owner, phone)` (`conversation_call_bridge.py:512-520`) loads that contact's stored memory for a returning caller. [I] A stranger pretending to be a known customer could hear that context reflected by the agent. Limited, but this is the one real data-exposure path. A forged shared-DID Diversion header (`platform_audit.md:77`) would add to this if that router is built.
- Flood: nothing per-source limits calls. `max-sessions=1000`, 30/s, RTP timeout 300 s (`external.xml:67`), far beyond capacity. [I] Real calls can fail or the box can OOM (a June OOM is in memory). Tenant concurrency caps are behind billing enforcement, which is off (`entitlements.py:509`, CLAUDE.md).
- Fingerprint and enumerate: OPTIONS always gets 200.

They CANNOT, on the evidence:
- Dial out to the PSTN. No bridge is reachable from `public`, the socket app only plays, records and hangs up, ESL is loopback with a 64-character password and an ACL, and REFER on one leg is refused. June `default`-context CDR rows were 36, 15 and 15 on the three burst days against 2,094, 1,673 and 490 public rows, so they do not scale with the sweep. I did not reconcile each row.
- Listen to other people's calls. No call-content retrieval exists on SIP; recordings live behind the API and tokens.
- Reach ESL, the media WS or Postgres: 8021, 9000, 9001, 9004, 5432 are loopback.
- Log in as a SIP user on 5060 with the default password; it is changed. (Whether that port is reachable: same VPC unknown.)

Nuance:
- `auth-calls=false` is not the bug in itself. It is the correct setting for an IP-authenticated carrier; the missing piece is the ACL.
- The previous audit's "Kamailio inactive so `shared/kamailio_acl.py` enforces nothing" is confirmed [V: `systemctl is-active kamailio` = inactive; `lescall_providers` empty; no profile references it].

### Inferences
- [I] The first real risk event today would be a change in the VPC rule, since no host layer exists behind it. That is the strongest argument for the ACL even if the outside test is clean.
- [I] Before building a shared-DID or personal-number router that trusts Diversion, the ACL is a prerequisite.

### Gaps
- [U] Actual financial loss in June (see Q3).
- [U] Whether the customer-memory path produces audible leakage in practice. I read the fetch code only, and did not read how every agent's prompt uses it. It was not tested, per the no-SIP constraint.
