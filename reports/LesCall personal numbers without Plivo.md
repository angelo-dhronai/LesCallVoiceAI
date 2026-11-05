# Swap the rented number, never remove it

**Plivo can be replaced behind a forwarded personal number, but no option available to a subscriber removes the number behind the forward entirely.** A mobile's forward points at another phone number, so the real choice is whose number that is: Plivo's, another carrier's, an operator block held by LesCall, or a SIM. Plivo at ₹200 a month stays cheapest for a pilot, and only an operator block of about 100 numbers beats it, at roughly 40 to 85 users and before any circuit cost, on weak price evidence. On outbound, conditional call forwarding (CCF) plays no part in originating a call: it only delivers the customer's return call to the agent. Removing Plivo does not change this. A customer sees the personal number only when that number's own SIM places the call, and every such route is unproven, limited to one call at a time and, for business calls, prohibited by TRAI's rules on the earlier research. So the workable product is forwarding for inbound, plus a rented, declared number for outbound with the personal number given as the way back. No new TRAI research was completed, so the regulatory section rests on earlier findings and its workaround list is analysis, not legal opinion. Port 5080 needs correcting too: FreeSWITCH has no protection on that port, but the Google Cloud firewall is very probably filtering it now, this still needs confirming, and it is not a dial-out emergency. Nothing has been built or changed, no calls were placed and nothing has been prototyped.

**How to read this.** Claims are tagged **[V]** verified in our code, live config, logs or a standards or regulator text; **[S]** stated by a vendor, press or a forum, not confirmed; **[I]** my inference or arithmetic; **[U]** unknown. Many web pages were read through a summarising fetcher, and the 3GPP standards through unofficial mirrors, so re-open a source before quoting it formally. Prices are list prices or weak reseller figures, not quotes. This report adds to `reports/LesCall on personal phone numbers.md` (the "earlier report"), which holds the forwarding test plan T1 to T9, the existing-defects table and the fuller cost table. For this report I also spot-checked the key code and config lines, read-only. Short names such as `pipeline.py`, `conversation.py` and `audio_events.py` sit under `calling-agent/backend/app/voice/`, and `usage_metering.py` under `shared/`. **Terms:** a *DID* is a rented phone number that arrives at a server rather than a handset. *CCF* forwards calls when the phone is busy, unanswered or unreachable. *SIP* is the internet signalling protocol for phone calls, and a *trunk* is a SIP connection to a carrier. *FreeSWITCH* is our telephony server. *CLI* is the number the person being called sees.

## 1. Inbound: a direct connection is possible, a numberless one is not

"Direct connection" can mean three things, and the answers differ. **Forwarding a mobile straight to LesCall with no number in between is not available.** The phone-side forwarding request carries a number, and the network may check that it falls in an allowed range ([TS 23.082](https://www.itecspec.com/3gpp/23.082/s/1.1.1), [TS 22.082](https://itecspec.com/3gpp/22.082/s/4.3.3)) [V]. The newer 4G voice standard does allow a SIP address as a forward target, but only where the operator's network permits it ([TS 24.604](https://www.itecspec.com/3gpp/24.604/s/4.9.1.4)) [V]. No Indian operator was found selling that [S, absence of evidence], and only their enterprise sales teams can settle it [U]. **A SIP trunk from an operator straight to our Google Cloud server is probably possible, but only by negotiation.** The default sale is a circuit to an office. The only evidence for delivery to a public cloud address is one page saying an Airtel account team can send calls to a public host with IP allow-listing ([Bolna](https://www.bolna.ai/integrations/sip-airtel)) [S], while a Jio customer was told in 2024 to have a PBX (an office phone system) on the premises ([FreePBX forum](https://community.freepbx.org/t/jio-sip-trunk-setup/96823)) [S, one user]. **A physical E1 line (a 30-channel circuit) cannot reach a virtual machine.** It needs a site, a gateway and an operator visit ([Edesy](https://edesy.in/blog/sip-trunk-vs-pri-line-india-2026.md)) [S].

Costs below are telephony only, per user per month, at 100 and 500 inbound minutes, before GST and pulse rounding [I, from list prices]. AI cost is separate: about ₹2.99 per talk-minute all-in on one measured call, which itself includes Plivo [V, one call].

| Option | Number behind the forward | Cost at 100 / 500 min | Outcome if chosen | Evidence and open points |
|---|---|---|---|---|
| **Plivo DID** (baseline) | Plivo's | ₹238 / ₹390 ([Plivo](https://www.plivo.com/voice/pricing/in/)) | Works today with no routing change; single-carrier exposure | Live routing [V]; no real forwarded call ever captured [V] |
| **Edesy** | Edesy's | ₹499 / ₹699 ([Edesy](https://edesy.in/sip-trunk-for-ai-voice-agents/pricing)) | Costs more, adds a second carrier | KYC about 24 h [S]; 60 s pulse; licence not stated |
| **Vobiz** | Vobiz's | ₹545 / ₹725 ([Vobiz](https://www.vobiz.ai/docs/concepts/sip-vs-websockets)) | Same | Launched 2024; a third party quotes ₹600+ and "maturing" SLAs ([AutoInterviewAI](https://www.autointerviewai.com/blog/plivo-vs-vobiz-sip-provider-india-ai-calling-2026)) [S] |
| **Exotel vSIP** | Exotel's | Unknown | Established name, documented SIP delivery to our server; needs a quote | Guides labelled Alpha, no SLA; 20 vs 200 calls/min conflict; samples use port 5070 or TLS 443 ([Exotel](https://support.exotel.com/support/solutions/articles/3000132410-exotel-virtual-sip-trunking-fqdn-based)) [S] |
| **Tata or Airtel trunk, 100-number block** | LesCall's operator block | Full: ₹150–200 flat plus circuit. At 20 users: ₹750–1,000 plus circuit [I] | The only route that removes Plivo and can cut cost; contract and minimums | ₹15,000–20,000/month is from a reseller and an aggregator, not a quote ([IndiaMART](https://www.indiamart.com/proddetail/airtel-sip-trunk-2853660715012.html)); Tata lists "100 free DIDs" ([TTBS](https://www.tatatelebusiness.com/voice-services/sip-trunk-services/)); 20 vs 30 minimum channels conflict; inbound-free assumed [U] |
| **Jio or Vi trunk** | Operator's | Unknown | Likely needs equipment at a site | No public rate; last-mile requirement per the forum post above [S] |
| **BSNL trunk** | BSNL's | Unknown | Jio users cannot use it | Jio's help excludes BSNL numbers as a forward target ([Jio](https://www.jio.com/help/faq/mobile/services/hd-voice/how-can-i-activate-call-forwarding-service/)) [S] |
| **E1 at an office** | Operator's | Unpublished | No advantage over SIP unless SIP is refused | Needs a site, gateway and tunnel to the cloud [S/I] |
| **Telnyx, DIDWW, didlogic, Twilio** | Foreign seller's | didlogic about ₹810 / ₹1,170 [I]; others unknown | Costlier, weaker evidence | Telnyx's own sources conflict on coverage and require 12 months ([Telnyx PR](https://github.com/team-telnyx/knowledge-base/pull/109), [terms](https://telnyx.com/country-specific-requirements-terms-and-conditions-of-service)); Twilio lists toll-free only ([Twilio](https://www.twilio.com/en-us/guidelines/in/regulatory)) [S] |
| **Multi-port gateway with LesCall SIMs** | LesCall's SIMs | About ₹377–442 flat [I] | Removes carriers; one call per SIM; legally the clearest no (§5) | Hardware listings [S]; Jio VoLTE (voice over 4G) unconfirmed |
| **Box with the user's own second SIM** | User's own SIM | About ₹700 flat, user pays [I] | No business rents anything; one call; depends on home power and internet | No verified single-port VoLTE product; ₹20,000 two-port candidate unconfirmed |
| **Rooted spare Android** | User's own SIM | About ₹150 plus an old phone [I] | Lab only | One tested device, root required ([GitHub](https://github.com/s-xander/Android-sip-gateway)) [S] |
| **Foreign number as the target** | Foreign | International rate, unknown | Excluded | Jio blocks international targets [S]; other operators unknown [U] |
| **WebRTC link or QR** (browser calling) | None | ₹0 carrier | An extra door, useful for a website; calls to the mobile are not served; no caller number | [I]; `mod_verto` and `mod_rtc` already loaded [V] |
| **WhatsApp Calling** | The WhatsApp number | ₹0 from Meta | WhatsApp callers only, and the number leaves the app | Earlier report [S] |
| **Operator forward to a SIP address** | None | Not on sale | The only true "no number" route; needs an operator deal | Standards allow it [V]; no Indian offer found |

**Three findings change the decision.** First, a second carrier only makes sense for redundancy or a specific feature, because every per-number vendor found costs more than Plivo [S/I]. That redundancy is real: the Plivo 402 outage and the 45-second silent no-answer burst in project records were both single-carrier failures [V, project records]. Second, a block's value depends on how numbers arrive. Our routing takes the destination number's last 10 digits (`socket_server.py:548-549`, `shared/phone.py:49-55`, `conversation_call_bridge.py:349-353`) [V], so the operator must send the full number in the *Request-URI*, the address line of the SIP call request. From memory, enterprise lines in India often send short digits [I]. Any that do would fall through to the founder's active agent (`conversation_call_bridge.py:388`) [V]. Third, capacity is not the constraint. Thirty channels at 1% blocking carry about 20 calls at once on average (standard table, recalled from memory), far more than 100 users at 500 minutes a month need (about 4.2 in the busy hour) [I, assumed traffic]. Numbers run out before channels do.

**My recommendation:** keep Plivo for the pilot, trial Edesy or Vobiz on one or two numbers as a failover and a test, and get written answers from Tata and Airtel in parallel, since only they change the economics.

## 2. Outbound, TRAI set aside: CCF only provides the way back

**CCF plays no part in originating a call.** Forwarding acts only on calls addressed to the served number, and the standards say so directly: "The served user's ability to originate communications is unaffected" ([TS 24.604](https://whatthespec.net/friendlyspec/spec/24.604/19.0.0), [TS 24.082](https://whatthespec.net/friendlyspec/spec/24.082)) [V]. It cannot set the number shown, place a leg, or add the agent to a live call. Its whole contribution is the return path: when the customer calls the personal number back, the call can reach the agent. It can also harm: any design where LesCall rings the personal number meets that phone's own forward, and the agent can end up talking to itself on two billed legs [I].

Below, **P** is the personal mobile, **C** the customer, **R** a rented number shown to customers and **D** a rented number the agent answers on. This section covers mechanics only. It is not clearance, and §5 governs what may be used.

| Pattern | How the call happens | C sees | What CCF adds | Main limit |
|---|---|---|---|---|
| **a. Rented number, return via P** | Agent calls C from R. Later C dials P, the forward delivers it to D | R; P only if told | The return path | C only knows P if told, and a callback to R already reaches the agent, so CCF adds value only where P is known or given |
| **b. Missed-call return** | A short ring from P, then C calls back through the forward | P, as a missed call | The return path | No conversation unless C acts; a one-ring pattern resembles a known scam shape (from memory) |
| **c. Conference from the handset** | P calls C and D, merges them, stays muted | P | Nothing on this call | Human-run; phone occupied; merge behaviour on Indian networks untested |
| **c2. Network transfer (ECT)** | P calls C, then hands C to D and drops off | P, possibly then an updated number | Nothing | **Unknown whether Jio, Airtel or Vi offer it.** Searches found nothing ([TS 24.629](https://whatthespec.net/friendlyspec/spec/24.629/19.0.0) defines it) [U] |
| **d. Android app plus conference** | LesCall tells a default-dialler app to place both legs and merge | P | Optional return path | Android only; push delivery, phone-maker app killing and the dialler role all unproven ([Android](https://developer.android.com/reference/android/telecom/InCallService)) |
| **e. Hardware on the SIM** | A Bluetooth box drives the phone, or a gateway holds the SIM | P, or the second SIM's own number | Optional return path | One line per SIM; no Bluetooth product found; if P's own SIM moves to a gateway, the phone loses P |
| **f. Click-to-call bridge** | LesCall rings the user, then C, and joins them | R | Nothing | Needs a human; wrong number shown; we have no bridge code today [V] |
| **g. A trunk presenting P** | The server claims P as caller | Refused or rewritten | Nothing | Not a route to design on. Plivo and Edesy both document refusal ([Plivo](https://www.plivo.com/docs/sip-trunking/concepts/technical-specifications.md), [Edesy](https://edesy.in/docs/sip-trunking/getting-started.md)) [S] |

The difference between e and g is the core mechanism. On a real SIM call, the mobile network takes the identity from the authenticated subscription, so the number shown is genuine. A carrier's trunk has no authority over a number that another operator allocated [I]. Exotel's "Custom Caller ID" is the one lead on showing a verified number, but my fetch could not confirm whether it covers a personal mobile [U], and Exotel itself warns about TRAI rules ([Exotel](https://developer.exotel.com/docs/advanced-config/custom-caller-id)) [S].

**The new engineering finding concerns conference patterns c and d, which break call ending in both directions** [I, from code]. FreeSWITCH controls only the D leg. When the agent hangs up, C stays connected to a muted handset unless an app also ends the call. When C hangs up, the agent keeps talking until the silence watchdog fires (`pipeline.py:725-737`). Three more hazards follow from the same setup, read from code and not exercised:

- The greeting gate (`pipeline.py:1170-1183`) sees audio as soon as D connects, so the agent would speak before C is on the line.
- A hold announcement followed by a conference join tone meets the literal conditions of a mailbox beep (`conversation.py:3330-3336`, `audio_events.py:48-57`), which would close the call immediately.
- Ring and hold time on D bill as talk time (`usage_metering.py:14-24`).

A leg arriving on D with P as the caller would also be misfiled: stored either under the user's own mobile or under our own DID (`conversation_call_bridge.py:467-473,581-604`) [V]. Echo and voice detection should cope with a carrier mixer in principle, but that is unmeasured [I]. **No device channel exists today**: no push token, device registry or per-line concurrency limit [V].

Ranked purely by "C sees P and can call P back", the order is e, d, c, b, a, f, g [I]. Pattern e is the only one with no conference and a plausible iPhone story, but no product exists and it means hardware per user. Pattern a ranks low on that test and is the only one that fits the rules (§5). Reliability data is absent for all of them, because none has been built.

## 3. With Plivo: forward to a hidden number, keep outbound on a rented one

The earlier report splits this into groups A to D. The steps below turn it into a sequence and add the outbound half. Everything here is proposed and nothing exists yet.

**Inbound with Plivo**

| Step | What happens | Files or systems |
|---|---|---|
| 0 | Run the port 5080 check in §6 | DevOps note |
| 1 | Fix the direction bug: any tenant's dial in the last 120 s labels an inbound call outbound, picks the wrong agent slot and stores our DID as the customer | `conversation_call_bridge.py:438,467-473,594-604,667` |
| 2 | Rent one 080 or 022 Plivo number on a KYC'd account (KYC is the operator's identity check) | [Plivo](https://www.plivo.com/docs/numbers/rent-india-numbers.md); host is in `asia-south1-c` [V] |
| 3 | Register it with an `-inbound` agent and `direction='inbound'`, so the daily probe skips it. The trunk must belong to the same account | `shared/telephony_admin.py:294,315-320`, `shared/sentinel.py:449-457`, `dashboard/server.py:4786` |
| 4 | Run tests T1 to T6 on a spare DID: register the row and leave the agent empty, because an unregistered number reaches the founder's agent. Headers are already logged | `conversation_call_bridge.py:377-381,388`, `socket_server.py:1932-1933` |
| 5 | User sets a conditional forward in the operator app or phone settings and reads it back | Earlier report; avoid `*401#` (§5) |
| 6 | The call arrives by the existing path: catch-all dialplan, socket, DID lookup, pipeline | `/etc/freeswitch/dialplan/public.xml:16-17`, `socket_server.py:548-549`, `conversation_call_bridge.py:348-367` |
| 7 | To productise: new `forwarding_sources` table in migration `0032` (the repo ends at 0031 [V]), never a `phone_numbers` row | `shared/db/schema.sql:57-74` for contrast |
| 8 | "Use my mobile" flow outside the trunk wizard, plus number pool or admin create and transfer | `frontend/src/pages/Telephony.tsx:395-397`, `frontend/src/components/ActivateAgent.tsx:60,80,103-104`, `frontend/src/pages/Overview.tsx:57-74`, `shared/telephony_admin.py:294,556` |
| 9 | Exclude these lines from silence alerts, guard the test call against the self-forward loop, add a forwarding health check, set the plan gate | `shared/sentinel.py:64,398-428`, `dashboard/server.py:5223-5249`, new code, `shared/billing/entitlements.py:936-955` |

**Outbound with Plivo, using CCF as the return path (pattern a)**

| Step | What happens | Files or systems |
|---|---|---|
| 1 | Settle the A2P declaration for R (§5) | Plivo and counsel |
| 2 | Use a tenant number R for outbound. Identity comes from the owner's rows with direction `both` or `outbound` | `dashboard/server.py:4781-4830` |
| 3 | Keep D as a separate `inbound` row. One `both` row could serve as R and D, but the sentinel would probe it and could kill a live call at 08:30 IST (`shared/sentinel.py:461-473`) [V for the code, I for the effect] | `shared/sentinel.py:449-457` |
| 4 | The dial runs through the existing builder, showing R | `shared/fs_originate.py:87-106` |
| 5 | The agent says the user's name and gives P as the way back, as authored agent data; optionally by WhatsApp with consent. No engine change for a fixed line [I] | `calling-agent/agents/<slug>/agent.yaml`, `shared/whatsapp/` |
| 6 | C calls P, the forward reaches D, the inbound agent answers | As inbound step 6 |
| 7 | History joins up by owner plus last 10 digits if the carrier preserves C's number [U, T1] | `conversation_call_bridge.py:512-568,581-585` |
| 8 | Fix framing: a return call inside 120 s is labelled outbound and takes the outbound agent. A longer-lived "expected return" record is new work, optional | `conversation_call_bridge.py:438,667`, `calling-agent/backend/app/voice/conversation.py:3760-3790` |

**Patterns showing P over Plivo (c, d) stay experiments.** Plivo would carry only D. They need a `personal_lines` table and per-line lease, a device API with push, a greeting armed by a "merged" event, suppressed watchdogs and beep close until then, two-way hangup, and billing from the merge (`pipeline.py:742-755`, `shared/billing/entitlements.py:509-516`, `socket_server.py:1073-1103`) [I]. Do not build them for business use. The cheapest learning needs no code: one manual merge on each of Jio, Airtel and Vi, using our own test numbers, listening for hold wording, join tone and delay.

## 4. Without Plivo: only the biller changes, except for the SIM routes

Ignoring TRAI, **Plivo is not the deciding variable for any pattern.** Patterns a, b, c, d and f need some rented number from any carrier. Pattern e needs none. Pattern g fails on every carrier for which evidence was found.

**Inbound without Plivo: one IP carrier or operator trunk**

| Step | What happens | Files or systems |
|---|---|---|
| 1 | Add a trunk row with another `provider`. Plivo is only a default | `shared/db/schema.sql:35-50`, `shared/telephony_admin.py:262,286`, `dashboard/telephony_api.py:212,359-361` |
| 2 | Add number rows with `direction='inbound'` | `shared/sentinel.py:449-457` |
| 3 | Agree port and transport. UDP and TCP 5080 work today, TLS is off, and Exotel's samples may need another port or TLS | `/etc/freeswitch/vars.xml:428-430` |
| 4 | Admit the carrier: add its addresses to the Google Cloud firewall **and** to `trunks.provider_ips` (§6). The inbound note said a second carrier "works instantly", but that holds only if the firewall is open, and the security evidence says it probably is not | `/etc/freeswitch/sip_profiles/external.xml:58` |
| 5 | Confirm the public IP is a reserved one, since carriers allow-list it [U] | `vars.xml:301,314` |
| 6 | Capture one INVITE (the SIP call request) before any real user. Check the Request-URI, caller number and Diversion header (the header that may name the forwarding number) | `socket_server.py:1932-1933` |
| 7 | If digits are short, normalise them before the socket. A shared pilot number with the real DID only in the To header needs `auto_to_user` or a dialplan condition | `shared/freeswitch_config.py:101-104`, [Sofia docs](https://developer.signalwire.com/freeswitch/users-and-endpoints/gateways/) |
| 8 | Check which number we read as the caller. Exotel's From header can be masked while another header carries the validated number | `socket_server.py:539`, [Exotel](https://support.exotel.com/support/solutions/articles/3000132410-exotel-virtual-sip-trunking-fqdn-based) |

**A real bug for blocks:** a registered trunk writes one fixed `extension` from its single caller ID (`shared/freeswitch_config.py:101-104`), which breaks many numbers on one registered trunk. IP-authenticated trunks are not affected [V/I]. For a non-cloud operator delivery, add a small session border controller (a SIP firewall box) at a Mumbai data centre with a VPN or interconnect to our region. Tata Communications, Sify and CtrlS appear as Google partners in stale sources, and Airtel and Jio did not ([itbrief](https://itbrief.com.au/story/googles-partner-interconnect-service-now-generally-available), [CRN](https://www.crn.in/?p=64029)) [S].

**Inbound without any carrier: the user's own second SIM in a box.** The chain is: caller, personal mobile, forward to the second SIM, box, registration with FreeSWITCH's internal profile, then the existing path [I]. It needs new per-user credentials, because only stock users exist and no generator does [V]. It needs a binding of each box to its number, since a compromised box could otherwise name any tenant's number [I]. It needs TLS switched on (`vars.xml:424`) [V]. **Two earlier conclusions now conflict with this design.** The security track recommends closing 5060 and 7443 at the firewall because nothing uses them, while this route needs one of them reachable. The earlier rule "never put a mobile in `phone_numbers`" was written for personal numbers, and a gateway SIM is a narrower case that needs a deliberate decision [I].

**Outbound without Plivo**

| Step | What happens | Files or systems |
|---|---|---|
| 1 | Add a gateway for the new carrier. Identity picks the gateway from the trunk name, so egress changes with no call-flow code [V] | `shared/freeswitch_config.py:66-111`, `dashboard/server.py:4824-4830` |
| 2 | Use a number owned on that carrier as R. Edesy refuses others with `403 Caller ID Not Authorized` ([Edesy](https://edesy.in/docs/sip-trunking/getting-started.md)) [S] | `phone_numbers` row |
| 3 | Move the platform default away from Plivo through `OUTBOUND_GATEWAY_NAME`, whose default is `plivo-outbound` [V] | `shared/fs_originate.py:53`, `dashboard/server.py:4732` |
| 4 | Update checks that assume a CLI Plivo accepts, and the sentinel's probes | `shared/voice_health.py:76`, `shared/sentinel.py:449-475` |
| 5 | Keep CCF as the return path, exactly as in §3 | Inbound table above |
| 6 | For pattern e: a gateway row with register mode, a sentinel exclusion and a per-line lease | `shared/freeswitch_config.py:83-104`, `shared/sentinel.py:449-473` |

**Comparison: inbound and outbound, with and without Plivo.** Costs are telephony only, and legal tags refer to §5.

| Case | Route | Customer or caller sees | Rented number? | Telephony cost | Capacity | Platform work | Evidence state | §5 tag |
|---|---|---|---|---|---|---|---|---|
| Inbound, Plivo | CCF to a dedicated DID | Caller dials P | Yes, Plivo's | ₹238 / ₹390 a month | Elastic, unstated | None for a pilot; product layer later | Routing [V]; forward untested | Legal-leaning |
| Inbound, other carrier | CCF to Edesy or Vobiz | Caller dials P | Yes, theirs | ₹499–725 a month | Unstated | Row, firewall, ACL, capture | Unproven here [S] | Legal-leaning; cloud leg grey |
| Inbound, operator block | CCF to LesCall's series | Caller dials P | Yes, LesCall's | ₹150–200 plus circuit at 100 users | About 30 calls shared | Request-URI check; possibly an SBC | Weak prices, cloud delivery unconfirmed [S/U] | Legal-leaning; reseller question |
| Inbound, own SIM | CCF to the user's second SIM in a box | Caller dials P | No | About ₹700, user pays | 1 call | Credentials, binding, TLS | No product verified [U] | Grey |
| Outbound, Plivo | R plus CCF return | R; P only as the way back | Yes, Plivo's | ₹0.38/min, 30 s pulse | Trunk limit | Direction fix; authored line | Live for R [V]; return untested | Legal if declared |
| Outbound, other carrier | R on that carrier plus CCF return | R | Yes, theirs | ₹0.45–0.50/min list, direction not split [S] | Unstated | Gateway, default gateway, checks | Unproven here | Legal if declared |
| Outbound, Plivo as D, SIM dials | App or manual merge | P | Yes, as D | ₹0.38/min plus 1–2 airtime legs | 1 per SIM | Large: app, leases, gating, hangup | Never tested [U] | Business: not legal. Personal: grey |
| Outbound, no carrier | SIM in a gateway or Bluetooth box | P or second SIM's number | No | ₹0 to LesCall, plus hardware | 1 per SIM | Gateway row, exclusion, lease | Jio module reports poor; no Bluetooth product ([Quectel](https://forums.quectel.com/t/eg25-g-problem-with-jio-sim/5277)) [S] | Business: not legal. Personal: grey |

## 5. TRAI: forwarding looks safe, outbound on a personal number does not

**The evidence here is older than the rest of this report.** The dedicated fresh TRAI research track was stopped before it wrote anything, so there are **no new TRAI notes**. This section rests on the earlier regulation notes, the earlier report and a few remarks in the new notes. **These texts remain unread:** TCCCPR 2018 principal regulations (including the definitions of "Sender" and "Commercial Communication"); TRAI's 13 August 2024 Direction; OSP Guidelines of 23 June 2021; Principal Authorisation Rules 2026; Cyber Security Amendment Rules 2025; User Identification Rules 2026; the SIM-binding Directions; the DoT `*401#` order; and Amendment 01/Licensing Policy/2026. The notified SIM threshold under s.42(3)(d) was not found either, nor was the DPDP Rules text read.

**The date conflict is unresolved.** On the regulation's commencement clause counted from 18 September 2026, the A2P declaration starts about **17 November**, 14 days from today. A vendor notes the e-Gazette reference contains 28 September, which would make it about **27 November** ([TRAI 2026](https://www.trai.gov.in/sites/default/files/2026-09/Regulation_18092026.pdf), [The Telephony Co](https://www.thetelephony.co/india/blog/tcccpr-third-amendment-dates)). Plan on the earlier date until counsel confirms. Legal points need counsel.

**"TRAI" here is shorthand for three separate things.** TRAI's telemarketing regulations (TCCCPR) govern *senders* of commercial and automated calls. The Telecommunications Act 2023 governs authorisation, number use and SIM equipment. DoT orders govern how forwarding may be activated. They reach a CCF product in five ways:

1. **Activation.** DoT told operators to stop USSD-based forwarding from 15 April 2024 "till further notice", naming `*401#` ([Communications Today](https://www.communicationstoday.co.in/dot-directs-deactivation-of-ussd-based-call-forwarding-by-april-15/)) [S, press only]. No lifting notice was found, and a "dial this code" onboarding resembles the scam behind that order.
2. **Automated calls are in scope by definition.** An A2P call (application-to-person) is one started by software "without direct human dialling", including "artificial voice". Calls without prior declaration are treated as unsolicited, and TRAI rejected a live-agent exclusion ([TRAI 2026](https://www.trai.gov.in/sites/default/files/2026-09/Regulation_18092026.pdf)) [V]. CCF does not touch this, but anything that places a call does.
3. **Commercial calls from a personal number.** Commercial communication must use registered headers or special-series numbers, and an unregistered Sender's commercial call counts as unsolicited ([TRAI 2025](https://www.trai.gov.in/sites/default/files/2025-02/Regulation_12022025.pdf)) [V].
4. **The penalty falls on the person's whole identity.** A first violation bars outgoing on all their telecom resources for 15 days. The next disconnects them for a year and blocks the devices, and a flagged number is mapped to the Sender's KYC across operators ([TRAI 2026](https://www.trai.gov.in/sites/default/files/2026-09/Regulation_18092026.pdf)) [V]. For a user, that means losing their main number.
5. **Shared trunks tie forwarding to outbound behaviour.** TRAI says "whole PRI/SIP links need to be barred, and not individual DID numbers" [V]. So one tenant's complaints could reach the same trunk that carries other users' forward targets, and at the disconnection stage those forwards would go dead with no sign on the handset [I]. This is why inbound users have a stake in outbound compliance.

**The forwarded leg itself should not be a Sender event** [I; no text says so expressly]. On CCF, then, the regulatory risk sits in the surroundings rather than the forward. The new notes add one practitioner remark: "anyone without a licence cannot connect IP with PSTN", both legs must stay in India, and a cloud-hosted PBX is a grey area ([Anurag Bhatia](https://anuragbhatia.com/post/2026/09/ip-telephony-in-india/)) [S]. That applies to any cloud-terminated trunk, Plivo's included, and sits in tension with our current production setup. Counsel should address it directly.

**The table below is analysis built from existing findings, not new primary legal research.** "Legal" here means I found a route the rules describe, or no prohibition; no instrument expressly blesses forwarding. The "Status" column marks how much each row rests on. Rows that are workarounds are marked **W**.

| Position | Option | What people get | Basis | Status |
|---|---|---|---|---|
| **Legal-leaning** | **W1. Conditional forward to a rented DID**, set in the operator app or phone settings | Receive on P; the agent answers | No prohibition found; TCCCPR aims at senders; `*401#` avoided | Inference from absence of a rule. Press only on the order. Counsel: is this "providing telecom services"? |
| **Legal, if declared and consented** | **W2. Rented, KYC'd, declared number in the correct series; the person's name in the spoken intro** | Call as the person's assistant; C sees R | [TRAI 2025](https://www.trai.gov.in/sites/default/files/2025-02/Regulation_12022025.pdf) and [2026](https://www.trai.gov.in/sites/default/files/2026-09/Regulation_18092026.pdf) texts read. Landline series for non-BFSI service calls appears tolerated, may narrow; 140 for promotional | Regulation read; pairing is my analysis. Sender definition unread |
| **Legal-leaning** | **W3. W2 plus giving P as the way back, with W1 on P** | Call out from R; return calls to P reach the agent | Outbound is W2; return is W1 | Analysis only. Untested with any carrier |
| **Legal-leaning** | **W4. Customer calls first**: publish P, use a website button or WhatsApp prompt | Conversation on P with no outbound event | Same as W1 | Analysis only |
| **Legal-leaning, thin** | **W5. WhatsApp Calling on a second number** | Both directions on WhatsApp only, with opt-in for business-initiated | No CLI or trunk; platform rules decide | Vendor and Meta docs; regulator silent. Unconfirmed: AI-chatbot policy |
| **Lowest exposure** | **W6. Web link or QR** | Web callers reach the agent | No phone-network leg | Analysis only. Does not serve calls to P |
| **Legal, ordinary use** | **W7. The person dials normally; no agent on the line; forward handles missed returns** | Real P, human conversation | Outside the A2P definition on plain reading; commercial use still caught by the unregistered-Sender rule | Analysis only. Sender definition unread |
| **Unknown** | **W8. Operator offers the same number as internet telephony** | P on SIP through a licensee | [AZB on DoT 2018](https://www.azbpartners.com/bank/clarification-and-amendments-regarding-internet-telephony/) says a licensee may; no product found | Law-firm summary; no operator answer. Ask enterprise sales |
| **Unknown** | **W9. Showing a name instead of a number (CNAP, preferred name)** | Recipient sees a verified name on R | Search digests only; applies to 4G and newer ([Telangana Today](https://telanganatoday.com/mobile-users-to-see-callers-names-under-new-cnap-framework-from-november-1)) | Unverified. Ask Plivo whose name shows |
| **Unknown** | **W10. Vendor's verified caller ID** (Exotel) | Possibly a verified number shown | Search extract only; vendor warns about TRAI | Unconfirmed; do not design on it |
| **Grey** | Unconditional forward, not via USSD | Agent answers all calls | The form the DoT order names; loop risk | Press only. Avoid as primary |
| **Grey** | Forward to a non-Plivo cloud trunk | As W1 | Forwarding as W1; cloud termination per the practitioner above | Practitioner view only |
| **Grey** | **W11. One user's single-SIM box, inbound** | Receive without a rented number | s.3(1), s.42(1); threshold not found. If LesCall ships or runs it, service-provision and abetment theories apply | Act read; application is reasoning. No ruling |
| **Grey** | **W12. Bluetooth box, inbound** | Receive; SIM stays in the phone | No instrument addresses it | Reasoning only |
| **Grey** | **W13. Android bridge, inbound** | Receive with a hold gap | No instrument; s.42(2)(b) question if it captures a live call between others | Reasoning only |
| **Grey** | **W14. Human-started merge or AI calling for an individual's non-commercial use** (dentist, family) | P shown | A2P on its face; TRAI says rules "apply to all A2P calls" | Sender definition unread |
| **Grey** | **W15. Click-to-call bridge** | Human plus C on R | PSTN-to-PSTN patch is closest to the toll-bypass concern | OSP text via law firm only. Counsel first |
| **Not legal** | Any business's commercial call from a personal 10-digit number, by any route, including c, d and e | Risks every resource the person holds | Reg 3, reg 2(bw), reg 25 | Text read. High confidence for businesses |
| **Treat as not legal** | A trunk presenting P | Refused | Vendor refusals; s.42(3)(b)/(c), s.3(8). Consented CLI untested | Vendor firm; statutory reading for counsel |
| **Treat as not legal** | Foreign route showing a +91 mobile | Blocked | DoT's spoofed-call system; Plivo and Twilio closed | Press digests and vendor docs |
| **Not legal** | Multi-SIM gateway run by or for customers | Clearest no | s.42(1), s.42(3)(d), Schedule IV names SIM box | Act and regulation read |
| **Excluded** | Foreign number as forward target | Blocked on Jio | International-leg and both-legs concerns | Vendor plus practitioner |

**The practical package is W1 + W2 + W3**, with W4 and W6 as extra doors. It gives people a link to their personal number in both directions without any route that needs the personal number shown on an automated call. It also gives up the one thing the owner may most want: customers will not see P on an AI-placed business call. I found no compliant route to that, and I would not spend engineering on one until counsel names a basis. This report gives no method for avoiding operator or government detection and none should be built: the Act's penalties include cognizable, non-bailable offences for number misuse ([Act](https://www.trai.gov.in/sites/default/files/2024-09/Telecommunications_01012024.pdf)) [V]. Counsel's question list remains as in the earlier report, with the cloud-PBX point added.

## 6. Port 5080: FreeSWITCH is open, the firewall is probably not

**Correction to the earlier summary.** It left the impression of an internet-open port with urgent consequences. The accurate statement is: **FreeSWITCH has no protection on port 5080, the Google Cloud (GCP) firewall is very probably filtering it now but this must be confirmed, and it is not a dial-out emergency.**

**Terms.** Port 5080 is the door where carriers deliver incoming calls. "No access check" means FreeSWITCH treats a stranger's call request exactly like Plivo's. *Authentication* means proving who you are, usually with a password. *IP restriction* means accepting only listed internet addresses, regardless of what the sender claims. An *ACL* (access control list) is FreeSWITCH's own address list, and a firewall is the same idea applied further out by Google's network. Plivo proves itself by address rather than password, so `auth-calls=false` is not itself the bug. The missing ACL is.

**The configuration evidence [V].** `auth-calls=false` sits at `/etc/freeswitch/sip_profiles/external.xml:58`, and the file has no `apply-inbound-acl` line. The ACL list `lescall_providers` exists but is empty, and no profile uses it. Kamailio, which was meant to enforce it, is inactive. A catch-all at `dialplan/public.xml:16-34` answers everything, hands it to the AI socket and records it to `/tmp/dialplan_test.wav`. The host's own firewall (`iptables`) filters nothing.

**It was open in June [V].** The call log (CDR) holds **4,257 scanner calls between 15 June 07:05 and 17 June 08:33 UTC, 4,220 answered, 33.1 hours of answered time**, with destinations swept across prefixes in the classic hunt for an outbound route and caller IDs such as `401`, `boss` and `zoiper`. Source addresses for those days are gone, because the log template has no IP field. The cost is unknown: compare Sarvam, Gemini and Plivo consoles for those days [U].

**It looks filtered now, but that is strong inference, not proof.** Since 17 June 08:33 UTC the call log holds not one non-DID call, against 410 legitimate public calls from July to November [V]. From 1 August to today, all 307 calls went to real DIDs, and all 1,279 media lines point into Plivo's Mumbai block [V]. A 10.5-minute passive capture saw SSH and web scanners but nothing on any SIP port [V, short window]. The 18 retained call header dumps come from Plivo addresses only [V, about 5 days]. The rules themselves could not be read, since this VM's identity lacks `compute.firewalls.list` [V].

**Unexplained points, carried through rather than resolved:**

- The burst stopped a day before the 18 June commits on `origin/restructure-and-dashboard` that reconcile a rule named `allow-sip-5060-kamailio` and say carriers "no longer need UDP 5080". Yet Plivo uses 5080 today, so whichever rule admits it is undocumented [V for both, I for the mismatch].
- The VM's tags are only `http-server` and `https-server`, so the admitting rule is untagged, on one of those tags, or targeted by service account [I].
- A 13 July memory line reads "zero packets with firewall wide open both ports", which is ambiguous [V].
- The public IP has moved before, which could explain the stop as well as a firewall edit could [V/I].
- `deploy/RUNBOOK.md`'s line that the origination URI must point at 5060 is stale against `.env` and live traffic [V].

**Severity.** If the firewall limits 5080 to Plivo, the likely state, this is **Medium**: defence in depth, one firewall edit away from High. If the outside test shows it open, it is **High for abuse and cost**, still not a dial-out or data-theft emergency. **Anyone reaching the port could:**

- get an answered call and an AI session with no credential;
- choose a tenant by naming its number, creating fake calls and billing;
- reach the founder's agent with an unknown number;
- spoof a known customer's number and possibly hear that customer's stored memory reflected back, the one real data path [I, not tested];
- flood the box, since limits are `max-sessions=1000` and 30 per second, far above capacity.

**On the evidence they could not:**

- dial out, because no bridge is reachable from `public`, the socket app only plays, records and hangs up, the control port is loopback only, and FreeSWITCH refuses a transfer on a one-legged call ([sofia.c, master branch](https://raw.githubusercontent.com/signalwire/freeswitch/master/src/mod/endpoints/mod_sofia/sofia.c), not the installed 1.11.0 tag);
- listen to other calls;
- reach the database or media ports.

**What must be done.** Read the rules, test from outside, and add the FreeSWITCH ACL either way. The firewall is currently the only wall, with nothing behind it. The ACL is also a prerequisite for any router that trusts a forwarding header, and for any second carrier. Nothing has been changed, and every change below needs the owner's yes.

Note for DevOps, ready to paste:

```text
Subject: lescall-agent SIP port 5080 - check first, fix in this order

Not an emergency and no dial-out path was found. FreeSWITCH has no auth or IP list
on 5080 (auth-calls=false, no apply-inbound-acl), and a catch-all answers every call.
Only the GCP firewall can be protecting it. No scanner call has appeared since
17 June (4,257 before that), so it is probably filtered, but nobody has read the rules.
Steps 1-2 are read-only. Send me the results. Change nothing until I confirm.

1. READ THE RULES
gcloud compute firewall-rules list --project=lescall \
  --format="table(name,direction,priority,sourceRanges.list(),allowed[].map().firewall_rule().list(),targetTags.list(),targetServiceAccounts.list(),disabled,logConfig.enable)"
gcloud compute instances network-interfaces get-effective-firewalls lescall-agent \
  --zone asia-south1-c --project=lescall
gcloud compute instances describe lescall-agent --zone asia-south1-c --project=lescall \
  --format="yaml(tags,networkInterfaces[0].accessConfigs,serviceAccounts)"
gcloud compute addresses list --project=lescall   # is 35.207.225.237 reserved?
Look for any rule allowing 5060, 5080, 5066, 7443, 8021, 8080, 8081, 8082, 1337, or
tcp/udp/all from 0.0.0.0/0. Check hierarchical policies. Name the rule admitting Plivo.

2. TEST FROM OUTSIDE (your own machine, OPTIONS only, never INVITE)
sipsak -s sip:probe@35.207.225.237:5080
nmap -sU -Pn -p 5080 --script sip-methods 35.207.225.237
Repeat for TCP 5080, then 5060, 8080, 8021, 7443, 8081, 8082.
200 OK = open from your IP. Timeout = filtered.

3. ASK PLIVO for its authoritative Zentrunk origination list for India. The docs disagree.

4. FIREWALL, only if steps 1-2 show it open
RANGES="13.52.9.0/25,216.120.187.128/26,18.214.109.128/25,18.215.142.0/26,204.89.148.128/26,3.120.121.128/26,18.228.70.64/26,13.238.202.192/26,13.205.159.55/32,15.206.154.163/32,43.204.247.115/32,43.205.14.55/32,<ranges from the existing rule>"
gcloud compute firewall-rules create allow-sip-5080-carriers --project=lescall --network=default \
  --priority=900 --direction=INGRESS --action=ALLOW --rules=udp:5080,tcp:5080 \
  --source-ranges="$RANGES" --enable-logging
gcloud compute firewall-rules create deny-sip-5080-world --project=lescall --network=default \
  --priority=950 --direction=INGRESS --action=DENY --rules=udp:5080,tcp:5080 \
  --source-ranges=0.0.0.0/0 --enable-logging
If already restricted: add logging for 48 h and reconcile ranges instead.

5. FREESWITCH ACL
a. Write the same ranges into trunks.provider_ips on all 6 rows (comma-separated;
   64 max per trunk). Re-apply one trunk so acl.conf.xml regenerates, then confirm
   lescall_providers is populated.
b. Back up: sudo cp -a /etc/freeswitch/sip_profiles/external.xml{,.bak-$(date +%s)}
c. Add after line 58: <param name="apply-inbound-acl" value="lescall_providers"/>
d. fs_cli -x 'reloadacl'
   fs_cli -x 'acl 13.52.9.100 lescall_providers'      # must be true
   fs_cli -x 'acl 18.214.109.141 lescall_providers'   # must be true
   fs_cli -x 'acl 8.8.8.8 lescall_providers'          # must be false
e. Only if all three are right: fs_cli -x 'sofia profile external rescan'

6. DIALPLAN GUARD. Back up public.xml. Confirm every phone_numbers row has its
   ai_inbound_ file. Remove the catch-all plivo_inbound AND public_extensions,
   public_conference_extensions and public_did together, add a final extension that
   responds 404, then fs_cli -x reloadxml. Check call audit still records.

7. SMALL ITEMS. Add ${sip_network_ip} to the end of the CDR template (confirm the
   sentinel's CDR reader tolerates it). Reserve the IP if it is ephemeral. Close 1337,
   5060, 5066, 7443, 8081, 8082 if open, BUT ask me first about 5060 and 7443, because
   a proposed design may need one. Do not close port 8080 without checking nginx.

TRAPS
- A hand-edited ACL is wiped by the next tenant Connect: render_acl rewrites the file
  (shared/freeswitch_config.py:131-171,256-260). Use provider_ips only.
- An empty list means default deny, so inbound stops. Before enforcing, add a code guard
  so apply_config refuses to write an empty lescall_providers.
- Never run 'sofia profile external restart'. It drops live calls. Use rescan.
- An unknown list name also denies: reloadacl BEFORE rescan.
- Do not set auth-calls=true. Plivo sends no password and would get 407.
- OPTIONS gets 200 even with the ACL on, so an outside OPTIONS test proves the
  firewall only, never the ACL.
- Commenting out only plivo_inbound brings transfers into the default context back to life.
- Do not source-restrict media (RTP) in this pass. Our media comes from 204.89.151.128-191,
  which is not in the Zentrunk table, and a wrong list means dead air during failover.
- A new carrier needs its addresses in both the firewall and provider_ips.
- FreeSWITCH behaviour was read from master, not 1.11.0. Trust the acl and status commands.

VERIFY AFTER EACH CHANGE
- Real phone call to each DID. Do NOT run python -m shared.sentinel --probe as a check:
  it dials every DID and its cleanup can cut a live call on those numbers.
- socket_server.log shows variable_sip_network_ip inside the list, the agent answers,
  and a calls row appears under the right owner.
- One outbound test call.
- Outside OPTIONS now times out (firewall). An INVITE test of the ACL only with my approval.
- Watch freeswitch.log for 'Rejected by acl' from Plivo-looking IPs for a week.

ROLLBACK
- Firewall: gcloud compute firewall-rules delete deny-sip-5080-world --project=lescall
- ACL: remove the param, fs_cli -x reloadxml, fs_cli -x 'sofia profile external rescan'
- Dialplan: restore public.xml from backup, fs_cli -x reloadxml
```

## Conflicts and gaps that remain open

| Topic | State | Effect |
|---|---|---|
| A2P date | 17 vs 27 November, unresolved | Plan on the 17th |
| TRAI texts | Nine instruments unread; no fresh research done | §5 is analysis; counsel must read them |
| Real forwarded call | None exists in our logs [V] | Caller-number survival, Diversion content and audio quality all unknown for every option, Plivo included |
| Firewall rules | Unread [U] | §6 severity is conditional |
| Plivo's address lists | Zentrunk table lacks Mumbai and Singapore; firewall page does not mention Zentrunk; third party adds more | Use a broad list until Plivo answers |
| Second carrier "works instantly" | Inbound note vs security evidence | Corrected: firewall and ACL must both admit it |
| Firewall closure vs SIM box | Security note closes 5060/7443; box design needs one | Decide before closing, or drop the box |
| Mobile in `phone_numbers` | Earlier rule vs gateway designs; a `both` row is also probed | Decide if a gateway is ever pursued |
| Operator cloud delivery | Bolna's Airtel page vs "circuit to premises" sources | Needs written answers |
| Operator and Exotel prices | Reseller and blog figures only; Tata 20 vs 30 channels; Exotel 20 vs 200 calls/min; Exotel page disagrees with itself on the Request-URI | All "unknown" cells need quotes |
| Telnyx India | Product page vs a later removal in its knowledge base | Do not plan on it |
| ECT, join tones, merge timing, held-leg audio | No evidence on Indian networks | Needs a handset session |
| June spend | Unknown | Check vendor consoles |
| Reliability of forwarding | hiRobin's 30–43% missing-call telemetry has no sample size and comes from a monitoring seller ([hiRobin](https://www.hirobin.ai/blog/call-forwarding-failures-india)) [S] | Run T7; build monitoring |

## Recommended order of next steps

Taken together, these findings make "without Plivo" a resilience and scale decision rather than a way to reach the outbound goal, and they show the urgent work is not the feature at all. I recommend this order:

1. **Now:** send the DevOps note and run steps 1 to 3 only.
2. **Now, before 17 November:** confirm the A2P declaration position with Plivo and counsel, including CLI ranges and the series of the five live numbers. This applies to existing traffic with or without this feature.
3. **Next:** after the owner's yes, apply the firewall change if needed, then the ACL, then the dialplan guard. Ship the direction-label fix.
4. **Then:** run T1 to T6 on a spare Plivo DID with four SIMs, plus one manual merge per operator on our own test numbers. In parallel, brief counsel with the unread texts and the questions in §5.
5. **In parallel:** request written answers from Tata and Airtel (cloud delivery, full number in the Request-URI, inbound charges, block price, SIP-address forward targets), and from Plivo (Diversion passthrough, underlying carrier, whose name CNAP shows, origination IP list).
6. **Pilot:** one or two business users with their own KYC documents, on conditional forwarding, outbound from R, P given as the way back, with an AI and recording notice in the opening line if counsel agrees.
7. **If the pilot holds under T7:** build the product layer, then trial Edesy or Vobiz as a failover on one or two numbers. Consider a block only when user numbers approach the 40 to 85 range and a real quote confirms the arithmetic.

**Decisions the owner must make**

| Decision | Options | My recommendation |
|---|---|---|
| Why move off Plivo? | Redundancy, cost at scale, or removing the number | Redundancy now, cost later. Removing the number is not available |
| Approve the port 5080 changes? | Read-only first, then firewall, ACL, dialplan | Yes, in that order |
| Accept that customers will not see P on AI-placed business calls? | Accept, or fund counsel to look for a basis | Accept for now |
| Who holds KYC on forward-target numbers? | Each business user, or LesCall under a reseller application | Each business user for the pilot; counsel on the rest |
| First audience | Business users or consumers | Business: a ₹200 DID does not reach a consumer price, and DPDP duties are less exposed |
| Second carrier now? | Edesy, Vobiz or Exotel quote | Trial after T1 to T6, not before |
| Pursue hardware (own-SIM box, Bluetooth)? | Lab only, or stop | Lab at most, inbound only, after counsel's view on the SIM threshold |
| Spend on an Android dialler or conference? | Build or not | Not now; manual merge test first |
| Engage telecom counsel now? | Yes or wait | Yes, because the date and the unread texts cannot be settled internally |
