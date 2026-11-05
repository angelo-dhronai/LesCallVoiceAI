# LesCall platform audit: from carrier-owned DID to a user's personal mobile number

Audited read-only on 2026-11-03, branch `Bettercallimprov`, working tree (uncommitted changes included). Paths are repo-relative unless they start with `/etc` or `/var`. "Cited Findings" were checked against code, live config, retained logs or an aggregate SELECT. "Inferences" were not executed. FreeSWITCH or carrier behaviour recalled from memory is labelled as such. No test call was placed.

Corrections to the briefing and docs:
- `docs/PLATFORM.md:56` draws a "Kamailio edge" in the path, but `systemctl is-active kamailio` returned `inactive` and FreeSWITCH owns 5060 and 5080 (`ss -ltn`, `ss -lun`).
- `CLAUDE.md` names a `signal_call_active()` gate. Nothing under `conversation_call_bridge.py`, `ws_audio_receiver.py` or `calling-agent/backend/app` contains that name. The real gate is `calling-agent/backend/app/voice/pipeline.py:1170-1183`.
- `dashboard/server.py:4763` still says "verified=1"; the code below it never checks that (`dashboard/server.py:4803-4807`).
- The per-DID dialplan files are live on disk but never execute (see Q1).

## Q1. Inbound path, and what a forwarded call looks like

### Takeaway
Nothing reads Diversion, History-Info or P-Asserted-Identity today, so a forwarded call looks like any other call to that DID: the agent is whatever the DID maps to, and the personal number is invisible. The header does reach the code: Plivo-delivered legs already carry `variable_sip_h_Diversion` in CHANNEL_DATA. But the only samples retained are carrier-internal reroutes that name neither a subscriber nor the DID, and no real forwarded call has been captured.

### Cited Findings

**Dialplan and SIP edge**
- Plivo reaches FreeSWITCH directly on `:5080`: `/etc/freeswitch/vars.xml:428`, `PUBLIC_SIP_PORT=5080` in `calling-agent/.env`, `deploy/RUNBOOK.md:13-14`.
- The first extension in the `public` context is a catch-all (`expression=".*"`) with no `continue`: `/etc/freeswitch/dialplan/public.xml:16-17`. It runs `answer`, `sleep 1000`, `record_session /tmp/dialplan_test.wav`, then `socket 127.0.0.1:9000 async full`. The `public/*.xml` include comes much later, at `public.xml:75`.
- The retained FreeSWITCH logs show `Regex (PASS) [plivo_inbound]` 307 times and `ai_inbound_*` zero times, so the generated per-DID files (`shared/freeswitch_config.py:114-128`) are shadowed.
- Disconnecting a number therefore does not stop answers, despite the docstring "so FreeSWITCH stops answering it" (`shared/freeswitch_config.py:280-296`).
- The external profile has `auth-calls=false` (`/etc/freeswitch/sip_profiles/external.xml:58`) and no `apply-inbound-acl` (grep of that file). The only ACL design lived in Kamailio, which is inactive, so `kamailio_acl.sync_carrier_acl` (`dashboard/telephony_api.py:233,330`) has no live enforcement effect.

**Headers read**
- `socket_server.py:539` reads `Caller-Caller-ID-Number`, falling back to `Channel-Caller-ID-Number`.
- `socket_server.py:548` reads `Caller-Destination-Number`.
- `socket_server.py:550,552,558` read `variable_lescall_campaign_id`, `variable_lescall_agent_slug` and `variable_lescall_dest`.
- The rest of `extract_rtp_info` is media fields (`socket_server.py:561-583`).
- A grep for `Diversion|History-Info|P-Asserted|sip_h_|sip_rh_|sip_ph_|variable_sip_` across `socket_server.py`, `ws_audio_receiver.py`, `conversation_call_bridge.py`, `shared/`, `dashboard/` and `calling-agent/backend/app` returned nothing.
- All CHANNEL_DATA headers are parsed into the `headers` dict and debug-logged (`socket_server.py:421,1932-1933`), so a new field is one `g(...)` away.

**Observed headers, from all six retained `socket_server.log*` files, Oct 30 to Nov 3 (numbers masked)**
- 18 ESL sessions had header dumps. All were inbound, at 03:00-03:01 UTC, which matches the sentinel probe hour (`shared/sentinel.py:439,811`). Sampled caller ID equals the DID, so these are self-probes, not customers.
- `variable_sip_P-Asserted-Identity` was present on 18 of 18. `X-Plivo-Stir-Verification` was present on 18 of 18.
- `variable_sip_h_Diversion` was present on 6 of 18, all on one DID. Shape: `<sip:+91…;cpc=ordinary@ka.ims.ttl.in;user=phone>;reason="tgrp-reroute";privacy="off";…`. The user part ended `1200`, not the DID's `3160`.
- On those legs `Caller-Destination-Number`, `sip_to_user` and `sip_req_user` were all the DID.
- `Caller-RDNIS` and any History-Info variable were absent on all 18. The Plivo signalling source was `18.214.109.140/141`.

**Routing**
- The WS path is `/<uuid>/<caller>/<did>/<campaign>/<slug>/<dest>`, with `-` as the empty placeholder and trailing empties dropped (`socket_server.py:764-771,792-798`). The receiver parses these positionally (`ws_audio_receiver.py:405-459`) and passes them to `call_bridge.get_or_create` (`ws_audio_receiver.py:514-522`).
- Agent precedence (`conversation_call_bridge.py:303-308`):
  1. Explicit slug
  2. Campaign
  3. `phone_numbers WHERE sig_digits=?` (`:351-353`)
  4. Dial record
  5. Legacy active agent
- A registered DID with no bound agent gets the neutral `UNROUTED_AGENT` (`conversation_call_bridge.py:377-381`).
- An unregistered destination falls through to the founding tenant's active agent (`conversation_call_bridge.py:354,388`; `:165-178`). Because of the catch-all, any destination reaches this code.

**Direction**
- `direction = "outbound" if (campaign_id is not None or dial) else "inbound"` (`conversation_call_bridge.py:667`). `dial` may be a time-only match within 120 s, and `call_links` is read with no owner filter (`conversation_call_bridge.py:438,467-473,506`). So any dial by any tenant in the last 120 s labels an inbound call outbound.
- That label then:
  - Selects `outbound_agent_slug` first (`conversation_call_bridge.py:358-360`).
  - Makes the customer number our own DID (`conversation_call_bridge.py:594-604`, reached from `:684-685` and `:888-889`).
- Aggregate SELECT: calls whose number matches a registered DID are inbound 204, outbound 26. This fits the mislabel but does not prove it, since legitimate dials to a DID also land there.

**How a forwarded call would look**

| Field | Value on a forwarded call |
|---|---|
| `did` | The LesCall forward-to DID (Request-URI), as on the sampled legs |
| `caller_number` | Whatever CLI the carrier presents (see Gaps) |
| `dialed_number` | Empty |
| `campaign_id`, `agent_slug` | Empty, so step 3 of routing applies |
| Personal number | Not represented anywhere |

| Number scheme | Result today |
|---|---|
| Dedicated DID per user | Correct agent and owner, no code change |
| Many users on one shared DID | All get that DID's single `inbound_agent_slug` and single `owner`; there is no routing key |

### Inferences
- `Diversion` cannot be read as "this call was forwarded" or "this is the subscriber". The samples show a carrier artefact (`tgrp-reroute`) naming some other number, on an unforwarded self-probe. A router must match the Diversion user against a registered source and treat a non-match as "not routable by Diversion".
- Parse the user part rather than reusing the `[^\d]` stripping at `socket_server.py:549`. That would concatenate digits from the whole value, including any digits in the host or parameters.
- From memory of FreeSWITCH's Sofia code, unverified here: `Diversion` is copied to `sip_h_Diversion` from unknown headers. The logs confirm the variable name. The `reason=` parameter (no-answer, busy, unconditional) would be available for per-reason greetings if carriers send it.
- A shared-DID router trusting Diversion is forgeable while `:5080` has no ACL and no auth. Anyone who can reach the port could name a victim's number and bill that tenant. The `Caller-Network-Addr` and `variable_sip_network_ip` values, or a restored ACL, must gate it.

### Gaps
- No real forwarded call exists in the retained logs, so three things are unknown on Indian networks through Plivo: whether Diversion carries the forwarding mobile, whether the original CLI survives, and whether the mobile's number replaces it. One controlled forward with DEBUG header logging (already on, `socket_server.py:1933`) settles it.
- The GCP firewall could not be read (`gcloud` reauthentication failed), so whether `:5080` is world-reachable is unverified. `dhron-voice-interface-report.md:795` records host iptables as empty.

## Q2. Sharing a DID, and what a `personal_numbers` model touches

### Takeaway
One number has exactly one owner and one inbound agent, enforced by a UNIQUE on `number` plus API checks on `sig_digits`. A forwarding-source model should be a new table, not more `phone_numbers` rows, because several subsystems treat every row in that table as a carrier DID they may dial.

### Cited Findings
- Schema: `number TEXT NOT NULL UNIQUE`, `sig_digits` with a non-unique index, and single `owner`, `inbound_agent_slug`, `outbound_agent_slug` columns (`shared/db/schema.sql:57-74`). Live indexes confirm: `phone_numbers_pkey`, `phone_numbers_number_key` (unique), `idx_phone_numbers_sig` (not unique).
- Duplicates by `sig_digits` are blocked only in application code (`dashboard/telephony_api.py:401-407`, `shared/telephony_admin.py:322-328`). Readers use `fetchone()` on `sig_digits` (`conversation_call_bridge.py:228,287,351-353`).
- Live data, aggregates only: 5 numbers, 5 owners, 5 distinct `sig_digits`, 0 with `verified=1`, 0 with no trunk, all `direction='both'`. 6 trunks across 6 owners.
- `verified` has no reader or writer in the telephony code (grep over `dashboard/telephony_api.py`, `shared/telephony_admin.py`, `shared/sentinel.py`, `dashboard/server.py`). Removal is explained at `dashboard/telephony_api.py:474-478`. The stale comment is at `shared/db/schema.sql:67-68`.
- `POST /api/numbers` validates only 7+ digits, trunk ownership and agent ownership (`dashboard/telephony_api.py:381-400`). It does not prove control of the number.
- The `phone_numbers` readers are `conversation_call_bridge.py`, `dashboard/server.py`, `dashboard/admin_ui.py`, `dashboard/telephony_api.py`, `dashboard/admin_telephony.py`, `shared/telephony_admin.py`, `shared/sentinel.py`, `shared/db/database.py`, `shared/db/migrate_sqlite_to_pg.py`, `shared/db/seed.py`, `deploy/build_viraj_rojgar.py`, plus three test files.
- Gate: `paid_owner` guards mutations only (`dashboard/telephony_api.py:43-67`). `telephony_allowed` skips `enforcement_enabled()` and fails closed on a missing entitlement (`shared/billing/entitlements.py:936-955`). Columns come from `shared/db/migrations/0015_telephony_entitlement.sql:14-15`.
- UI assumes a carrier:
  - `frontend/src/pages/Telephony.tsx:395-397` (Origination URI on your inbound trunk)
  - `:525,868` (`provider_ips`)
  - `:366` (locked state)
  - `:225` (test call)
  - `:643` (per-number binding)
- `frontend/src/components/ActivateAgent.tsx:60,80,103-104` reads `/numbers`, PATCHes the slot and says "Connect your own number first". `frontend/src/pages/Overview.tsx:57-74` counts bound numbers for the checklist.
- Admin: `dashboard/admin_telephony.py:33,200-248` covers bind, connect, disconnect, test call and transfer. `shared/telephony_admin.py:294-340` requires the trunk to belong to the same account (`:315-320`). `transfer_number` is at `shared/telephony_admin.py:556`.
- Metering is keyed on `conn_id`, owner and `fs_uuid`, not on the DID: `shared/usage_metering.py:96-118` and `:252`. CDR reconciliation therefore works for forwarded calls.
- The `calls` table has no DID or forwarded-from column. Only `direction`, `phone` and `sig_digits` match a column-name search, and the INSERT column list is at `conversation_call_bridge.py:1694-1700`. `/v1` exposes `direction` and `phone` (`shared/public_api/serialize.py:113-115`).

### Inferences
Suggested new table, migration `0032` (the next number per `docs/PLATFORM.md:48`):

`forwarding_sources(id, owner, number, sig_digits UNIQUE, did_id → phone_numbers, inbound_agent_slug, status, verified_at, verify_method, label, created_at)`

Add `calls.forwarded_from` and `calls.forward_reason`, and expose them in `/v1`. A UNIQUE on `sig_digits` is what stops two tenants claiming one mobile. The existing `verified` column should not be repurposed, because it describes DIDs.

Ownership proof becomes mandatory in the shared-DID model. Whoever registers a victim's mobile receives that person's forwarded calls. No OTP channel exists today: SMS is `not_configured` (`shared/intelligence/automation.py:417-418`). Cheap options:
- A call from the mobile to the DID, matched on caller ID, while a code is pending.
- An AI verification call to the mobile that speaks a code the user types.

### Gaps
- Whether Plivo allows N DIDs under the same Zentrunk without extra configuration, and the rental cost per DID, are out of scope here.

## Q3. Outbound path and caller ID

### Takeaway
Caller ID is simply the `phone_numbers.number` digits placed in `origination_caller_id_number`. No code stops a registered mobile being presented, so the only barrier is the carrier. A separate presented-CLI field would touch very little code; the blockers are carrier and legal, which are researched elsewhere.

### Cited Findings
- `_outbound_identity(owner)` walks the owner's numbers with `direction` in `both` or `outbound` (`dashboard/server.py:4781-4787`). It requires the trunk to be owned by the tenant, `applied`, and the agent owned (`:4811-4823`). It returns `caller_id = digits(number)` and `gateway_name = trunk.name` (`:4824-4830`). The founder falls back to the global trunk (`:4792-4796`).
- The builder sets `origination_caller_id_number`, `originate_timeout=45`, `ignore_early_media`, `lescall_dest`, `lescall_agent_slug` and `lescall_campaign_id`, then dials `sofia/gateway/<gw>/<digits> outbound_ai_connect XML default` (`shared/fs_originate.py:87-106`). It has no extra-variable hook and sets no PAI or Privacy.
- `/etc/freeswitch/dialplan/default/outbound_ai.xml` holds the extension that hands the call to `socket 127.0.0.1:9000`.
- Generated gateways set `caller-id-in-from=true` (`shared/freeswitch_config.py:106`). The two gateway files inspected (`plivo.xml` and one tenant's) point at the same `25935838218519915.zt.plivo.com`. So those two "own trunks" are one carrier account, which qualifies the claim at `dashboard/server.py:4803-4807` that the tenant's own carrier authorises the CLI.

| Dial origin | Location | Identity source |
|---|---|---|
| Callback automation | `dashboard/server.py:4278,4336` | `_outbound_identity` |
| Follow-up autodial | `dashboard/server.py:4504,4606` | `_outbound_identity` |
| Manual, contact and scheduled funnel | `dashboard/server.py:5129,5141` | `_outbound_identity` |
| Campaign | `dashboard/server.py:5522,5660` | `_outbound_identity`, once per run |
| Tenant test call | `dashboard/server.py:5241-5249` | The number row directly, to `users.phone` |
| Admin test call | `shared/telephony_admin.py:496-498` | The number row via `originate_cmd` |
| Sentinel probe | `shared/sentinel.py:512-515` | Hand-written originate, no `lescall_dest` |

So `fs_originate.py` is not literally the only builder. The sentinel string bypasses it.

- A plain-language note at `dashboard/telephony_api.py:361-363` records 403/4180 when using the wrong Plivo trunk.

### Inferences
- Minimal mechanics for a personal CLI:
  1. Add `phone_numbers.presented_caller_id`, or a verified `forwarding_sources` lookup.
  2. Return it as `caller_id` at `dashboard/server.py:4825`.
  3. Optionally add a `sip_h_P-Asserted-Identity` variable in `originate_cmd`.
  4. Handle the two test-call paths that compute `caller` themselves.
- Bridge logic survives this, because the customer comes from `lescall_dest`, not caller ID (`socket_server.py:554-559`, `conversation_call_bridge.py:625-631`).
- A return call to that CLI reaches the user's phone, not the agent, unless the mobile also forwards to a DID.
- A SIP-speaking GSM gateway is the one route where the presented number really is the SIM's. It only needs a trunk row and gateway XML, and `render_gateway` already has a register mode with an `extension` param (`shared/freeswitch_config.py:101-104`).

### Gaps
- Whether Plivo Zentrunk accepts or rewrites an unowned CLI was not tested. No call was placed.

## Q4. Features that depend on the number or direction

### Takeaway
Nearly every per-person feature keys on `caller_number` by last 10 digits and owner. If carriers rewrite CLI to the forwarding mobile, contacts, memory and WhatsApp all collapse onto the owner's own number. Sentinel is the subsystem most harmed by putting a mobile in `phone_numbers`.

### Cited Findings

| Feature | Behaviour | Impact when forwarded or personal |
|---|---|---|
| Canon | Last 10 digits, `to_e164` assumes India (`shared/phone.py:13,49-55,70-90`) | Fine for Indian mobiles; foreign numbers can collide |
| Customer number | Inbound = caller ID (`conversation_call_bridge.py:594-595`) | Depends on carrier CLI |
| Memory | `contacts` then `customer_memory` by owner and sig (`conversation_call_bridge.py:512-568`) | Rewritten CLI means every caller shares one history |
| Contact creation | Unknown caller auto-creates a contact (`conversation_call_bridge.py:1566-1603`) | Family and friends become CRM contacts |
| Dial identity | Exact phone match only (`conversation_call_bridge.py:642-658`) | Unaffected on inbound |
| DND | Campaign checks `contacts` (`dashboard/server.py:5357,5363`) | Outbound only; no inbound effect |
| WhatsApp | Sends to `to_e164(call.phone)` (`shared/whatsapp/stage.py:239,247,280`); consent by owner and E.164 (`shared/db/migrations/0030_whatsapp.sql:118-129`) | Rewritten CLI could send a caller's brochure and consent record to the owner |
| Machine line | Beep close is outbound only (`calling-agent/backend/app/voice/conversation.py:3333-3335`). The text gate at `:3393-3412` has no direction test in the lines read | See Inferences |
| Greeting gate | Waits up to 5 s for the first frame (`calling-agent/backend/app/voice/pipeline.py:1170-1183`) | Same as any inbound call |
| Metering | Keyed per call (`shared/usage_metering.py:252`) | Unaffected |
| Billing admission | Owner from slug or DID (`conversation_call_bridge.py:675`, `:1020-1033`, `:1301-1309`) | An unresolved owner is refused or marked unattributed |

**Sentinel**
- `check_inbound_flow` alerts per number when the last `public` CDR is older than 26 h, after a first baseline call (`shared/sentinel.py:64,398-428`).
- `_probe_targets` picks every number with `direction='both'`, an applied owned trunk and an inbound agent (`shared/sentinel.py:449-457`).
- `loopback_probe` originates to that number daily at 03:00 UTC, which is 08:30 IST (`shared/sentinel.py:439,512-515,811-813`).
- It then runs `uuid_kill` on any channel whose caller ID or destination matches, reasoning that "no real caller can present our DID" (`shared/sentinel.py:462-473`).
- It sleeps 8 s per number in series (`shared/sentinel.py:540`).

### Inferences
- A personal mobile registered as an ordinary `phone_numbers` row would ring the user every morning and could have the user's own live call killed. Code currently permits this registration.
- Dedicated forward-to DIDs set to `direction='inbound'` escape the probe (it requires `both`) and also cannot place outbound calls (`dashboard/server.py:4786`). But they lose the active check, and a legitimately quiet personal line would email a false "inbound silence" alert every 26 h. `check_inbound_flow` needs an exclusion for these lines.
- A forwarded inbound call during any tenant's campaign can be labelled outbound, get the DID as its customer number, and be stored and followed up against that wrong number. This defect already exists on all inbound calls, but it matters more when the forwarding line's calls are personal.
- Test-call loop: `POST /api/numbers/{id}/test-call` dials `users.phone` from the DID. If that mobile forwards unconditionally to the DID, the agent answers itself, both legs meter, and the 120 s dial window labels the second leg outbound. A guard is needed comparing the destination with the owner's forwarding sources.
- The machine-line regex includes wording like "your call has been forwarded to voicemail" (`calling-agent/backend/app/voice/machine_line.py:29`). If a carrier forwarding announcement were audible to the agent before the caller spoke, it could classify as a machine. Likelihood is low and untested.

### Gaps
- Whether calls placed through forwarding keep the original CLI on Jio, Airtel, Vi and BSNL decides how serious the first row group is. No samples exist here.

## Q5. Voice pipeline changes, and non-SIP ingress

### Takeaway
A forwarded call needs no pipeline change: it is the same SIP leg, dialplan, codec and WS format. A phone app cannot simply stream into port 9001, because that port is unauthenticated, loopback-only and one-way. TTS returns through a file played on an ESL-registered channel, so non-SIP ingress is either a SIP/WebRTC client of FreeSWITCH or a new transport.

### Cited Findings

**What a new ingress would have to speak on 9001**
- Bind: `127.0.0.1:9001` by default, with no auth, and a comment explaining why it must not be exposed (`ws_audio_receiver.py:50-58`; `dhron-voice-interface-report.md:788,794-795`). `ss` confirms `127.0.0.1:9001`, `:9000` and `:9004`.
- Path: `/<id>/<caller>/<did>/<campaign>/<slug>/<dest>`. The `id` is 8-80 chars of `[A-Za-z0-9-_.]`, else a random id is minted. Digit-only segments are enforced for caller, DID, campaign and dest; the slug is `[A-Za-z0-9_-]` (`ws_audio_receiver.py:405-459,480`). Slug ownership is checked (`conversation_call_bridge.py:259-295`).
- Audio: binary L16 mono, 8 kHz, 20 ms, 320 bytes per frame. Twilio-style JSON media frames are also accepted (`dhron-voice-interface-report.md:33-41`; `ws_audio_receiver.py:44,60`). `socket_server.py:799-803` requests `mono <sr>`. The receiver pings every 20 s and caps messages at 1 MiB (`ws_audio_receiver.py:1061-1065`).
- After resampling, the seam is exactly 640 bytes per 20 ms at 16 kHz. The frame contract is hard and unvalidated (`dhron-voice-interface-report.md:68-94`).

**Return path**
- TTS is written as WAV files and sent as `playback_request` to `:9004` (`dhron-voice-interface-report.md:592-598`; `conversation_call_bridge.py:1987`).
- `socket_server` only accepts types `hangup_request`, `break_request` and `playback_request` (`socket_server.py:1073-1103`), and needs a live ESL registration in `_active_calls` (`socket_server.py:997-1001`).
- A connection without an ESL channel therefore cannot hear anything.

**Abstractions and existing SIP clients**
- `AudioSource` and `AudioSink` are abstract (`calling-agent/backend/app/audio_transport/base.py:6,35`). The DHRON report advises not feeding 9001 and instead driving `VoicePipeline` directly with a custom sink that implements `interrupt_playback` and `playback_busy_until` (`dhron-voice-interface-report.md:875-878,942`).
- Its route skips the business layer (`dhron-voice-interface-report.md:948`), which a LesCall product feature would need to keep.
- FreeSWITCH already loads `mod_verto`, `mod_rtc` and `mod_sofia` (`/etc/freeswitch/autoload_configs/modules.conf.xml:41,48,50`). The internal profile binds WS 5066 and WSS 7443 (`/etc/freeswitch/sip_profiles/internal.xml:321,325`), listening on `10.160.0.8`.

**Telephony-tuned assumptions an app path would break** are listed at `dhron-voice-interface-report.md:380-391` and `:1132-1140`: absolute level thresholds, µ-law-based dead-media checks, FSMN calibrated only on 8 kHz upsampled audio.

### Inferences
Ranked by remaining work for an app or gateway:
1. A SIP GSM gateway acts as a trunk. No pipeline change, and no routing change if it sends a stable destination.
2. A softphone or WebRTC client registered on the internal profile, with a dialplan entry to the AI socket. No pipeline change, but it needs firewalling, per-user SIP credentials and a route that carries identity.
3. A custom WSS gateway plus a new sink and a `TelephonySession` that accepts a chosen source and sink. This is the most work, and it needs level re-tuning for app-captured audio.

Whether Android or iOS let an app capture GSM call audio is a platform limit, and the other researchers cover it.

### Gaps
- The FSMN and threshold behaviour on double-hop forwarded audio is unmeasured. Calltester and callsim could only approximate it; a real forward test is needed.
- WebRTC ports were not probed from outside the VPC.

## Q6. Personal-assistant persona, briefly

### Takeaway
Most of take-a-message is already expressible as agent data, and an owner notification exists only as the in-app bell. There is no live transfer capability anywhere: the `transfer_to` key is accepted and read by nothing.

### Cited Findings
- `guardrails.escalation.transfer_to` is allowlisted (`calling-agent/backend/app/agent_definition/controls.py:82`). A repo grep across `calling-agent/backend/app`, `dashboard` and `shared` finds no other reference.
- A search for `uuid_transfer`, `uuid_bridge`, `uuid_deflect` and a bridge application in Python, XML, YAML, shell and TSX, excluding agents and venvs, found none. The ESL commands used are playback, record, `uuid_audio_stream`, `uuid_break` and hangup (`socket_server.py:330,726,800,825,989-1018`).
- Escalation is spoken only. `action` feeds a prompt band (`calling-agent/backend/app/voice/prompt_blocks.py:1875-1884`), and `handoff_message` is the closing line (`calling-agent/backend/app/voice/conversation.py:4872-4878`).
- Notifications are in-app inserts (`shared/intelligence/notify.py:28-41,44`). `send_sms`, `send_whatsapp`, `slack`, `teams` and `crm_update` return `not_configured` (`shared/intelligence/automation.py:417-418`). A WhatsApp sender exists (`shared/whatsapp/sender.py:88`) but is built around per-call playbooks, templates and caller consent (`CLAUDE.md`, WhatsApp section).
- Known-caller context by phone exists (`conversation_call_bridge.py:535-568`). Slots, stages, `termination` and `post_call` are data (`docs/PLATFORM.md:153-167`). "Are you an AI?" answers yes when asked (`calling-agent/backend/app/voice/ai_disclosure.py`, per `CLAUDE.md`).

### Inferences
- A take-a-message assistant could be an ordinary `-inbound` agent: no engine change for the conversation. The new parts would be owner identity fields (or a template) and a post-call owner notification, by adapting the WhatsApp sender to send an approved template to `users.phone`.
- Live transfer would need a fourth 9004 request type, an ESL `bridge` or `uuid_transfer`, an owner-leg originate, and teardown of the AI pipeline and audio-stream bug.
- With an unconditional forward, transferring back to the owner's mobile re-forwards to the DID and loops. It would need conditional forwarding only, or a second number.
- The intelligence pipeline scores every call as a lead and can raise follow-ups, so personal calls would need a per-agent opt-out to prevent contact pollution and follow-up suggestions to friends.

### Gaps
- None material for this short scope.

## Q7. What breaks or misbehaves today on a forwarded call

### Takeaway
A forward to a registered, bound, dedicated DID works today with no code change. The defects are mislabelled direction during dial bursts, CLI-dependent identity, sentinel noise, test-call loops, and answers on numbers that should have stopped.

### Cited Findings
- Direction mislabel: `conversation_call_bridge.py:667`, with the unscoped query at `:467-473`.
- Unregistered DID answered by the founder's active agent: `conversation_call_bridge.py:354,388`.
- A disconnected or deleted number keeps answering: catch-all at `/etc/freeswitch/dialplan/public.xml:16-17`. Deleting a row makes it unregistered, then the rule above applies (`dashboard/telephony_api.py:460-470`). In the admin path `disconnect_number` only sets a status and removes a shadowed file (`shared/telephony_admin.py:428-439`, `shared/freeswitch_config.py:280-296`).
- A registered DID with no inbound agent answers as the neutral fallback (`conversation_call_bridge.py:377-381`).
- `call_direction` comes from the agent definition (`calling-agent/backend/app/voice/conversation.py:2225`), and slug suffix rules enforce it (`docs/PLATFORM.md:169-170`). An `-outbound` agent bound to the inbound slot only logs a warning (`dashboard/telephony_api.py:118-137`).
- Hello and identity logic assumes the agent is the caller's first contact. Nothing knows the caller already rang the mobile for 20-30 s. `reason=` is unread (see Q1).
- `PATCH /api/numbers/{id}` stores `direction` unvalidated (`dashboard/telephony_api.py:444-446`), while the admin path validates it (`shared/telephony_admin.py:49,310-311`). A bad value silently removes a number from routing paths that filter on it.

### Inferences
- "Forward-on-busy" and "forward-on-no-answer" produce the same leg as unconditional, so the differences are in greeting content and user expectations rather than plumbing.
- Removing the catch-all to let per-DID files work is a separate, risky change. It would also fix the "unregistered DID" and "disconnect does not stop" defects, but a DID with no applied file would fall through to the other `public` rules and fail, which is exactly the kind of outage the catch-all may be masking. Verify all five DIDs have files first (five exist under `/etc/freeswitch/dialplan/public/`).

### Gaps
- Actual ring-then-forward timing and post-forward audio quality need a live test.

## Change list per attachment model

### Takeaway
Start with one dedicated forward-to DID per user: it needs no voice or routing changes, and a shared DID can follow once a real capture proves Diversion is reliable. Never put a personal mobile in `phone_numbers` as an ordinary row. Items below are proposals derived from the cited findings above; none were built.

### Cited Findings
Evidence for each entry is in Q1-Q7. Only code locations are repeated here.

**Model A: dedicated forward-to DID per user**

| Area | Change |
|---|---|
| Voice, bridge, dialplan | None for routing (`conversation_call_bridge.py:351-367`) |
| Inventory | No DID pool table exists; add one, or use the admin create and transfer flow (`shared/telephony_admin.py:294,556`) |
| Trunk | Per-tenant trunk row still required (`shared/telephony_admin.py:315-320`) unless the rule is relaxed for platform-owned inbound DIDs |
| Personal number | Store it for display, loop guard and verification (new table, Q2) |
| Sentinel | Exclude these lines from silence alerts, decide probe policy (`shared/sentinel.py:398-428,449-457`) |
| Test call | Guard against self-forward loop (`dashboard/server.py:5223-5249`) |
| UI | New "use my mobile" flow with forwarding instructions, outside the trunk wizard (`frontend/src/pages/Telephony.tsx:395-397`, `frontend/src/components/ActivateAgent.tsx:103-104`, `frontend/src/pages/Overview.tsx:57-74`) |
| Billing | Reuse the telephony gate or define a new one (`shared/billing/entitlements.py:936`, migration 0015) |
| Fix regardless | Scope and tighten the direction label (`conversation_call_bridge.py:467-473,667`) |

**Model B: one shared DID, routed by Diversion or CLI**

Everything in A, plus:

| Area | Change |
|---|---|
| `socket_server.py:548-568` | Extract and parse `variable_sip_h_Diversion`, also read the network source |
| `socket_server.py:734-798` | Add path segment 7 |
| `ws_audio_receiver.py:458-459,514-522` | Parse and forward it |
| `conversation_call_bridge.py:298-381,611-697,844-875,1002-1019` | Add a forwarded-from routing step and owner, ahead of the DID step; change the signatures |
| Shared DID row | Owner semantics must change. `_did_owner` and `_explicit_slug_permitted` assume one tenant (`conversation_call_bridge.py:212-232,283-291`) |
| Unmatched case | Define behaviour for no or unknown Diversion (neutral line and non-billable) |
| Security | Source-IP gate (`/etc/freeswitch/sip_profiles/external.xml:58`), mandatory ownership proof |
| Records | New `calls` columns, `/v1` fields (`conversation_call_bridge.py:1694`, `shared/public_api/serialize.py:113`) |
| Tests | `calling-agent/backend/tests/test_did_routing_isolation.py`, `test_direction_binding.py`, `test_outbound_identity.py`, `test_admin_telephony.py` |

**Model C: outbound showing the personal number**
- `dashboard/server.py:4825,5241`, `shared/telephony_admin.py:497`, `shared/fs_originate.py:91-95`.
- Verified-source table.
- Carrier and legal clearance first.

**Model D: GSM gateway or SIM box**
- Trunk row and gateway XML (`shared/freeswitch_config.py:66-111`).
- Destination mapping or `extension` param (`:101-104`).
- Plus Model A's sentinel handling, since it is still a number row. Egress goes via `sofia/gateway/<name>` with no code change.

**Model E: app or WebRTC ingress**
- SIP client route: internal-profile credentials, dialplan entry to `socket 127.0.0.1:9000`, identity variables.
- Custom route: authenticated WSS proxy, new sink (`calling-agent/backend/app/audio_transport/base.py:35`), session factory changes, level re-tuning (`dhron-voice-interface-report.md:942,949`).

### Inferences
Order by certainty of working:
1. Model A works with the current bridge.
2. Model D is mostly configuration.
3. Model B depends on the unproven Diversion content.
4. Model C depends on carrier policy.
5. Model E's custom route is the largest build.

Effort figures were not estimated, apart from the DHRON report's 4-6 engineering days for its different no-business-layer adapter (`dhron-voice-interface-report.md:953`).

### Gaps
- The decision between A and B requires the single forwarded test call noted in Q1, and the per-DID cost from the carrier research.
