# Inbound forwarded calls to LesCall without Plivo as the number behind the forward

Research date: 2026-11-03. Extends `reports/LesCall on personal phone numbers.md` and its notes; does not repeat them. Read-only: nothing was changed, restarted or dialled.

Evidence tags: **[V]** read directly in our code, live config or a standards text. **[S]** stated by a vendor, reseller or forum, not independently confirmed. **[I]** my inference or arithmetic. **[U]** unknown. Web pages were read through a summarising fetcher or a search digest, so re-open a page before relying on exact wording. "(digest)" means seen only in a search digest. Nothing here was tested against a live carrier. Regulation gets a tag only; the owner has another track for it.

## The fundamental question: can a call-forward target be anything but a dialable number?

### Takeaway
On the handset path available to a subscriber, the target is a phone number. The IMS spec does allow a SIP URI, but only where the operator's network permits it, so **"no number behind the forward at all" is impossible without the operator's cooperation**, and no Indian operator was found selling that. Everything else in these notes has a number behind it, and the only real question is whose number: Plivo's, another carrier's, an operator block held by LesCall, a SIM, or the user's own second SIM.

### Cited Findings
**Circuit-switched / MMI path (TS 24.082, 23.082, 22.082, 29.002)**
- Registration carries `RegisterSS (CFU, BasicServiceCode, ForwardedToNumber, ForwardedToSubAddress, LongFTNsupported)`; if the network cannot accept it, an error comes back; error values are in TS 24.080 — [TS 24.082 §1.2.1 via iTecSpec](https://www.itecspec.com/3gpp/24.082/s/1.2.1) [V, mirror of the standard].
- The number in the home country may be entered in three schemes, all numeric: national significant number; trunk prefix plus national number; international prefix, country code and national number. "The network may also validate the forwarded-to number" — [TS 23.082 §1.1.1 via iTecSpec](https://www.itecspec.com/3gpp/23.082/s/1.1.1) [V].
- The check is described as "a simple check of the forwarded to number to see if the number is within the allowed number range" — [TS 22.082 §4.3.3](https://itecspec.com/3gpp/22.082/s/4.3.3) [V]. A forwarded-to number can run to 28 digits under the long-FTN work — [3GPP search digest on TS 22.082 / NP-000066](https://3gpp.org/FTP/tsg_cn/TSG_CN/TSGN_07/Tdocs/PDF/NP-000066.pdf) (digest).
- One exception softens "must be E.164" but not "must be a number". For subscribers with originating CAMEL Phase 2+, "this address need not be in E.164 international format" — [TS 29.002 §7.6.2.22](https://itecspec.com/3gpp/29.002/s/7.6.2.22). With the TIF-CSI flag the network "shall neither convert nor validate the received number" — [TS 23.082 §1.1.1](https://www.itecspec.com/3gpp/23.082/s/1.1.1) [V]. Both are operator-provisioned subscription data.
- I could not read the ASN.1 `RegisterSS-Arg` (the current TS 29.002 module was not retrieved). A sibling CR shows these fields use the MAP `AddressString` family — [NP-000288](https://3gpp.org/FTP/tsg_cn/TSG_CN/TSGN_08/tdocs/PDF/NP-000288.pdf) (digest) [I that the forwarded-to field is the same family].

**IMS path (TS 24.604, CDIV)**
- In the `forward-to` action, the target "should be a SIP URI (IETF RFC 3261) or tel URI (IETF RFC 3966)". The same clause says the network can indicate whether a user may use **any URI** as target, or whether it must be a telephone number (a SIP URI with `user=phone`, or a tel URI) — [TS 24.604 §4.9.1.4 via iTecSpec](https://www.itecspec.com/3gpp/24.604/s/4.9.1.4) [V].
- This is the one clause in the standards that makes a numberless forward conceivable. But the target is stored and executed by the operator's Telephony Application Server, and the operator decides the policy [I from the same clause].

**India**
- No Indian operator product attaching a mobile number to SIP or to an external IMS identity was found — [alternative_channels.md, operator section](../LesCall%20on%20personal%20phone%20numbers/alternative_channels.md) [S, absence of evidence]. Nothing in this round changed that.
- Jio's own help text describes targets as "all mobile and landline numbers", excluding BSNL numbers, the user's own number and international numbers — [Jio help](https://www.jio.com/help/faq/mobile/services/hd-voice/how-can-i-activate-call-forwarding-service/) [S, official, via earlier note].

### Inferences
- **Clear statement for the owner:** a subscriber cannot point a GSM/VoLTE forward at `sip:agent@lescall`. MMI codes take digits, and the IMS clause that permits a URI is the operator's to enable, then requires the operator to route to an external SIP domain over some interconnect. That is an operator commercial deal, not an integration [I].
- Three things would count as "operator cooperation": a CDIV profile allowing SIP targets; a number hosted in the operator's IMS with a trunk to us; or an operator-run enterprise number series on a trunk. Only the third is sold, and it is still a number, just not Plivo's [I].
- A Ut/XCAP client on the handset cannot get around this. Whether Indian operators expose Ut at all is unknown, and the "any URI" permission is still theirs [I/U].

### Gaps
- Any written position from Airtel, Jio, Vi or Tata on SIP-URI CDIV targets. Only their enterprise sales can answer.
- The TS 24.082 PDF downloaded as compressed binary and there is no PDF tool on this host, so section text comes from the iTecSpec mirror, not the ETSI PDF.

## Other carriers and DID providers: who actually delivers India numbers to a customer's own FreeSWITCH by SIP?

### Takeaway
Two distinct markets exist. **API-layer / VNO providers** (Exotel vSIP, Vobiz, Edesy, Boni) deliver over the public internet to a SIP URI, with fast KYC, but every per-number price found is above Plivo's ₹200. **Licensed operators** (Tata, Airtel, Jio, Vi, BSNL) sell blocks with channel minimums and generally assume a circuit to a premises. No per-number vendor found beats ₹200/month. Only an operator block can, and only once it is well filled.

### Cited Findings
**Price anchor:** Plivo ₹200/month + ₹0.38/min, 30 s pulse — [Plivo India pricing](https://www.plivo.com/voice/pricing/in/) [S]. A vendor's general market range puts local DID rental at ₹200–500/month, which places Plivo at the floor — [FreJun, updated 10 Jul 2026](https://frejun.com/sip-trunk-providers-india/) [S, generic].

**API-layer / VNO, delivered over the internet**

| Provider | Number and minute price | SIP delivery to own server | KYC, time, minimum | Status |
|---|---|---|---|---|
| **Exotel vSIP** | Not published for vSIP. Rental plus one-time activation exist, no figures — [Exotel FAQ](https://developer.exotel.com/docs/faqs/pricing-plans). Blogs conflict on plans (₹9,999 / ₹19,999 / ₹49,499 vs "$200 for six months"); incoming ₹0.40 local and ₹0.75 inter-circle are described as historical — [CloudTalk](https://www.cloudtalk.io/blog/exotel-pricing/), [DialNexa](https://dialnexa.com/blogs/exotel-pricing/) (digest) | Yes. ExoPhone in the user part: `sip:+91XXXXXXXXXX@sip.customer.com:5070;transport=tcp`; G.711; TCP 5070 or TLS 443; DNS on each call; media UDP 10000–40000 — [Exotel FQDN guide, Jul 2025](https://support.exotel.com/support/solutions/articles/3000132410-exotel-virtual-sip-trunking-fqdn-based) | Static IP plus TLS/SRTP route at Mumbai PoP — [Exotel TLS](https://docs.exotel.com/dynamic-sip-trunking/exotel-sip-trunking-tls-india). KYC page not read | Guides labelled Alpha, "not covered by production-grade SLAs" (digest); the TLS page states no status. Rate limit conflict: 200 vs 20 calls/min per trunk. Edesy calls Exotel a UL VNO — [Edesy](https://edesy.in/blog/sip-trunk-providers-india-ai-calling-2026.md) [S] |
| **Vobiz** | ₹500/month per DID + ₹0.45/min SIP (₹0.65 on WebSocket) — [Vobiz docs](https://www.vobiz.ai/docs/concepts/sip-vs-websockets). Third party: DID ₹600+, 92-series ₹900+, per-second billing, verified Aug 2026 — [AutoInterviewAI](https://www.autointerviewai.com/blog/plivo-vs-vobiz-sip-provider-india-ai-calling-2026) | Yes, "SIP INVITE routed to endpoint URI"; console has Inbound Trunks with a Primary URI over TCP — [ElevenLabs on Vobiz](https://elevenlabs.io/docs/eleven-agents/phone-numbers/telephony/vobiz) | No contract or minimum; 24 h activation (third party). KYC details not found | Launched 2024; SLAs "still maturing"; no licence stated in that article (third party) |
| **Edesy** | Landline ₹449, mobile/92-series ₹499, toll-free ₹999/month; ₹0.50/min, 60 s pulse; ₹0 trunk fee, no minimum, excl. 18% GST — [Edesy pricing](https://edesy.in/sip-trunk-for-ai-voice-agents/pricing) | Yes, INVITE to a URI you choose; PCMU/PCMA; TLS/SRTP on request — [Edesy docs](https://edesy.in/docs/sip-trunking/getting-started) | KYC about 24 h; documents not listed | Licence not stated; the review of Exotel above comes from Edesy, a competitor |
| **Boni** | From ₹500/month per number; minutes not given — [dev.to, Jul 2026](https://dev.to/animesh_gupta_705a19fc6f8/how-to-give-your-livekit-vapi-or-retell-voice-agent-a-real-indian-91-phone-number-am6) | SIP URI, names FreeSWITCH | Assisted KYC via WhatsApp | Promotional post only |
| **Ozonetel** | Not found | Registration-based SIP, UDP inbound, "Static SIP" enabled by Ozonetel's team — [ElevenLabs on Ozonetel](https://elevenlabs.io/agents/integrations/ozonetel) (digest) | Not found | Own docs not found |
| **Tata Smartflo** | Not found | Sarvam documents an account/API integration, not SIP to own server — [Sarvam](https://docs.sarvam.ai/voice-agents/deploy/telephony/smartflo) (digest) | Not found | Tata's SIP trunk product is the relevant path; see below |

**Knowlarity, MyOperator, Servetel:** searches returned nothing showing an inbound DID delivered by SIP to a customer's own server. Do not assume they can.

**Licensed operators**

| Operator | Published facts | Delivery |
|---|---|---|
| **Tata Tele Business** | Plans ₹250, ₹350, ₹500, ₹700, plus 500/750 OBD and 500/850 UL; rates listed are outgoing only. "100 free DIDs are included", "from 20 to 1500 simultaneous calls on a single link", 99.5% SLA, all NGN circles; PRI needs a new order, no migration — [TTBS](https://www.tatatelebusiness.com/voice-services/sip-trunk-services/) [S, official]. A TTBS FAQ in a digest says minimum 30 channels; the current page says 20 (conflict). Whether rental is per channel is not stated [U]. Aggregator estimate for 30 channels: ₹18,000–20,000/month — (digest, unofficial) | Delivery not on page. Edesy says "dedicated circuit to your premises or PBX" and "enterprise contracts" for Tata, Airtel, Jio and Vi [S] |
| **Airtel Business** | No rate card. Reseller listing: ₹15,000/month, 100 DIDs, 30 channels, excl. tax, undated — [IndiaMART](https://www.indiamart.com/proddetail/airtel-sip-trunk-2853660715012.html) [S, weak]. A vendor range of ₹3,000–40,000 per channel is too wide to use — [didlogic blog](https://didlogic.com/blog/best-sip-trunk-provider-india/) | ILL/MPLS, "dedicated fiber or MPLS links", pilot numbers with "large DID ranges" — [altF9](https://altf9.tech/india-sip-trunk-3cx-recommendation/), [CleveroTel, 1 Oct 2026](https://cleverotel.in/sip-trunk-provider-in-india/) [S]. **But** Bolna says Airtel can send calls to its public host on port 5060 UDP/TCP with IP allow-listing, configured by "your Airtel Business account team", codecs PCMU/PCMA — [Bolna](https://www.bolna.ai/integrations/sip-airtel) [S] |
| **Jio Business** | 10–5,000 channels; "pay only for active channels"; fixed rental for unlimited domestic calling; no public rate; only price seen is an old reseller ₹7,500/month, 30 units — digest of [Jio SIP pages](https://www.jio.com/business/services/voice-and-collaboration/sip-trunk/) [S] | Ethernet and GPON last mile, sometimes UBR — [Anurag Bhatia, 28 Sep 2026](https://anuragbhatia.com/post/2026/09/ip-telephony-in-india/). In 2024 Jio told a customer "I would need a PBX on my premises", gateway with GE to the "JIO L2 switch", VLANs, E.164 in To, From and Request-URI, plus a DID and pilot range — [FreePBX forum, May 2024](https://community.freepbx.org/t/jio-sip-trunk-setup/96823) [S, one user] |
| **Vi Business** | Product exists, no tariff — [Vi infographic](https://www.myvi.in/content/dam/vodafoneideadigital/StaticPages/EnterpriseImages/SIP_Trunking_Infographic.pdf) (digest) | Not published |
| **BSNL** | 2 free DIDs per concurrent channel, max 10; 10–10,000 channels; G.711, G.729, AMR; circle tariff exists but the page could not be opened — [BSNL](https://bsnl.co.in/services/enterprises/Sip_Trunk_new.html) (digest; direct fetch 404). **Jio's help text excludes BSNL numbers as a forward target** ([Jio](https://www.jio.com/help/faq/mobile/services/hd-voice/how-can-i-activate-call-forwarding-service/)) | Not found |

**Airtel IQ:** no per-minute price or self-hosted SIP documentation found; G2 says pricing is not provided — [G2](https://g2.com/products/airtel-iq/pricing) (digest). Treat Airtel IQ as unproven for this purpose [U].

**Foreign carriers**
- **Twilio:** India regulatory page lists only toll-free +91800, with an address "outside India" — [Twilio](https://www.twilio.com/en-us/guidelines/in/regulatory); domestic calling N/A — [Twilio voice](https://www.twilio.com/en-us/guidelines/in/voice) [S, official]. No India local DID found.
- **Telnyx:** internally conflicting. Release note of 17 Sep 2026: mobile numbers in Mumbai City, Gurugram, Noida and Bangalore, KYC first, calls "within India" — [Telnyx](https://telnyx.com/release-notes/india-mobile-numbers). Product page: "local India phone numbers across four licensed telecom districts", SIP "through its Mumbai anchorsite" — [Telnyx India](https://telnyx.com/india). Then a knowledge-base PR merged 6 Oct 2026 removed India local numbers: "we don't have coverage for this number type" — [PR #109](https://github.com/team-telnyx/knowledge-base/pull/109). India numbers carry a **12-month minimum commitment**; end-user name, address and ID required for resale — [Telnyx terms](https://telnyx.com/country-specific-requirements-terms-and-conditions-of-service). Price not stated. Anurag Bhatia lists Telnyx as a VNO — [post](https://anuragbhatia.com/post/2026/09/ip-telephony-in-india/) [S].
- **Vonage:** self-service number purchase not available for India — digest of [Vonage](https://api.support.vonage.com/hc/en-us/articles/20053288510620-India-Voice-Features-and-Restrictions) (direct fetch 403).
- **Sinch:** nothing found on +91 inbound DIDs. Unknown, not "no".
- **DIDWW:** announced India local numbers in Dec 2020 — [DIDWW](https://news.didww.com/2020/12/14/didww-adds-local-numbers-in-india-91); no current price or document list; its service terms disclaim reachability from all networks — [DIDWW appendix](https://didww.com/DIDWW-Service-Appendix) (digest).
- **didlogic:** from $8/month, incoming from $0.01 (unit not stated), 080 and 011 listed, porting not available — [didlogic](https://didlogic.com/virtual-phone-numbers/india/) [S].

**Who can sit where**
- "Anyone without a licence cannot connect IP with PSTN"; both legs must be in India; cloud-hosted PBX is a grey area; no number portability between IP telephony providers — [Anurag Bhatia](https://anuragbhatia.com/post/2026/09/ip-telephony-in-india/) [S, practitioner]. Regulatory tag: **grey for any cloud-terminated trunk, Plivo included**.

### Inferences
- Moving from Plivo to a VNO trades one rented number for a more expensive one. The motive would be redundancy or a specific feature, not cost [I].
- Arithmetic before GST and pulse rounding: Vobiz ₹545 / ₹725 at 100 / 500 min; Edesy landline ₹499 / ₹699 (60 s pulses lift this on short calls); Plivo ₹238 / ₹390 [I].
- A second carrier is valuable as a failover. The 45 s silent no-answer burst and the Plivo 402 outage in project memory are both single-carrier exposures, though that is a reliability argument rather than the one asked [I].

### Gaps
- Vobiz, Edesy and Boni upstream carriers and licence basis. Exotel's vSIP price, its KYC and whether it left Alpha. Whether any of these numbers can be a Jio forward target. Written quotes from Tata, Airtel, Jio, Vi: every operator figure above is a reseller's, an aggregator's or a list price without channel basis. Vendors hide prices, and no figure was invented to fill the gap.

## Direct operator connection: can a trunk or E1 be delivered to a GCP VM, and does Plivo go away?

### Takeaway
An E1 cannot reach a VM: it is a physical circuit needing a site and a gateway. An operator SIP trunk probably can reach a cloud IP where an account team agrees (Bolna's Airtel page is the only evidence), but the default sale is a circuit to a premises. **Plivo goes away for inbound immediately, and from the platform only if outbound moves too.**

### Cited Findings
- E1: 30 voice channels from 32 timeslots; PRI card or media gateway; operator installation visit; physical circuit to the office; prices unpublished, "they vary by operator, city and contract" — [Edesy, 2026](https://edesy.in/blog/sip-trunk-vs-pri-line-india-2026.md) [S]. A FreePBX thread reports Tata's PRI needs hardware on site — [FreePBX](https://community.freepbx.org/t/sip-trunk-in-india/68159) (digest).
- Airtel enterprise SIP "often deployed over dedicated circuits (ILL/MPLS)" — (digest, [CleveroTel](https://cleverotel.in/sip-trunk-provider-in-india/)); public-host delivery per [Bolna](https://www.bolna.ai/integrations/sip-airtel) [S].
- Under the old OSP rule, SIP trunks had to terminate over private last-mile access at the office; the OSP licence was later removed — [Anurag Bhatia](https://anuragbhatia.com/post/2026/09/ip-telephony-in-india/) [S].
- GCP private paths in Mumbai: Dedicated Interconnect at GPX and Tata Mumbai IDC (2017) — [Google](https://cloud.google.com/blog/products/gcp/gcp-arrives-in-india-with-launch-of-mumbai-region); Partner Interconnect partners include Tata Communications (2018), Sify (2019), CtrlS (2021) — [itbrief](https://itbrief.com.au/story/googles-partner-interconnect-service-now-generally-available), [Sify](https://www.sifytechnologies.com/news/sify-is-now-a-google-cloud-interconnect-partner/), [CRN](https://www.crn.in/?p=64029) (digests, stale). Airtel and Jio did not appear.
- Our host is in `asia-south1-c` (report, from instance metadata) [V, earlier]. Outbound still defaults to Plivo: `shared/fs_originate.py:53`, `dashboard/server.py:4732`, plus per-number trunks; the sentinel and health checks assume a CLI Plivo accepts (`shared/voice_health.py:76`) [V].

### Inferences
Three delivery shapes, in order of effort [I]:
1. **Public internet SIP to the VM**, IP allow-listed both ways. Zero hardware. Only possible if the operator agrees; ask first. External profile already listens on TCP and UDP 5080 (`ss` output, `/etc/freeswitch/vars.xml:428`) [V].
2. **Colo or office handoff with an SBC, then IPsec or Partner Interconnect to GCP.** Operator's L2/fibre terminates on a small SBC at a Mumbai data centre; VPN or interconnect to the VM. This shape fits the Jio pre-requisite list. The SBC is an added failure point and a cost not priced here.
3. **E1/PRI at a site, media gateway to SIP, tunnel to GCP.** Same as 2 with a TDM gateway. No advantage over SIP unless SIP is refused.

Concurrent-channel economics: Erlang B at 30 channels and 1% blocking is about 20 erlangs (standard table, from memory). If 15% of daily traffic falls in the busy hour, that is 8,000 minutes/day, or about 240,000 minutes/month. So 100 users at 500 min/month (50,000 minutes, about 4.2 busy-hour erlangs) use a fraction of one 30-channel trunk. DIDs run out before channels do [I, assumed traffic profile; AI calls may cluster differently].

### Gaps
- Each operator's written answer on cloud delivery; last-mile and colo prices; SBC cost; whether inbound is free on these trunks. Rate tables list outgoing only, so "inbound free" is my assumption [U].

## Number per user vs many users on one trunk: DID blocks and the Request-URI

### Takeaway
A 100-number series is the one structure that beats ₹200 per user, but only above roughly 40–85 users and before any circuit cost. Existing routing works unchanged **if** the carrier puts the full DID in the Request-URI. Exotel's docs say it does, although one page contradicts itself; for operators it is unconfirmed and short-digit delivery is a real risk.

### Cited Findings
- Tata: "100 free DIDs included" — [TTBS](https://www.tatatelebusiness.com/voice-services/sip-trunk-services/). Airtel reseller: 100 DIDs, 30 channels — [IndiaMART](https://www.indiamart.com/proddetail/airtel-sip-trunk-2853660715012.html). BSNL: 2 free per channel, up to 10 — (digest). Jio and Airtel use pilot plus range language — [FreePBX](https://community.freepbx.org/t/jio-sip-trunk-setup/96823), [altF9](https://altf9.tech/india-sip-trunk-3cx-recommendation/) [S].
- Exotel FQDN guide shows the ExoPhone in the R-URI user — [guide](https://support.exotel.com/support/solutions/articles/3000132410-exotel-virtual-sip-trunking-fqdn-based). Its TLS page is inconsistent: the table says Exophone, the example INVITE uses the customer's number — [TLS](https://docs.exotel.com/dynamic-sip-trunking/exotel-sip-trunking-tls-india) [S].
- Our routing: `Caller-Destination-Number` → digits only (`socket_server.py:548-549`) → `significant_digits` last 10 (`shared/phone.py:13,49-55`) → `phone_numbers.sig_digits` (`conversation_call_bridge.py:349-353`). `+91…`, `0…` and bare forms all resolve [V].
- If a registered DID has no agent: neutral fallback (`conversation_call_bridge.py:367-381`). If the digits match nothing: legacy active agent (`:388`), another tenant's by default [V].
- **Registered trunks break multi-DID today:** `render_gateway` writes `extension` from the trunk's single `caller_id` (`shared/freeswitch_config.py:101-104`). Per the Sofia docs, `extension` sets `destination_number`, and the value `auto_to_user` takes it from the To header — [SignalWire gateways](https://developer.signalwire.com/freeswitch/users-and-endpoints/gateways/); without it, destination equals the gateway username [S, official docs]. One user reported `auto_to_user` not working for them — [list, 2015](https://lists.freeswitch.org/pipermail/freeswitch-users/2015-May/112848.html).
- Live dialplan has a catch-all `plivo_inbound`, `.*`, ahead of per-DID files (`/etc/freeswitch/dialplan/public.xml`, report cites `:16-17`) [V], so a new carrier's call reaches the socket with no per-carrier dialplan.

### Inferences
- Break-even against Plivo, fixed fee only (₹15,000–20,000/month for 30 channels and 100 DIDs, weak sources): about 63–84 users at 100 min each; about 38–51 users at 500 min each. A circuit, SBC or colo pushes these up, and an unsold block is paid for regardless [I].
- At a full 100 users that is ₹150–200/user/month flat, against Plivo's ₹238–390. Worth doing at scale, not at pilot stage [I].
- A block also makes the DID pool, which the earlier report notes does not exist, largely a software allocation problem (one trunk row, 100 number rows) [I].
- **Must be asked of any operator:** "Is the full 10-digit DID in the Request-URI, or a pilot or short extension?" From memory, enterprise PRI/SIP in India often sends short digits; if so, a normalising dialplan step is needed before the socket, otherwise those calls fall to the `:388` tenant leak. A shared pilot with DID only in To needs the `auto_to_user` route or a dialplan `sip_to_user` condition [I].
- The shared-DID Diversion idea is unaffected by carrier choice in principle, but whether a given operator passes Diversion remains untested. One retained Plivo sample carries `ka.ims.ttl.in` (report), which suggests Plivo's DID may already sit on Tata's IMS, so a direct Tata trunk could behave similarly. That is a guess from one hostname [I].

### Gaps
- Actual INVITE from any operator trunk. Block sizes above 100, block pricing, whether Vobiz, Edesy or Exotel discount bulk (Edesy lists "bulk" with no price).

## Forward to a mobile SIM we control: gateway or spare Android

### Takeaway
Technically this removes every SIP carrier, and the number behind the forward becomes a mobile SIM. A multi-port VoLTE gateway works as a SIP peer; a spare Android does not, short of rooting one specific kind of phone. It costs more per user than Plivo at low minutes, handles one call per SIM, and the earlier report marks a LesCall-owned gateway fleet **prohibited** (one line, per the assignment).

### Cited Findings
- Hardware (earlier notes): Dinstar UC2000-VE 4-port ₹37,895–46,000, 8-port ₹65,264, 16-port ₹1,26,316; 2-port about ₹20,000 with VoLTE unconfirmed; 32-port about ₹1,30,000 (digest, URL attribution unverified) — [alternative_channels.md](../LesCall%20on%20personal%20phone%20numbers/alternative_channels.md) [S].
- Indian vendor Aria sells Jio VoLTE gateways with SIP v2.0 and TLS, starting at 4 ports — [Aria](https://ariatelecom.net/Jio-4G-Volte-Gateway) (digest). OpenVox SWG-3032 is listed as FreeSWITCH-compatible with SIP registration — [OpenVox](https://www.openvoxtech.com/swg-series-wireless-gateway/507-swg-3032-series) (digest). Patton SN5700 is a VoLTE/GSM gateway plus SBC — [Patton](https://www.patton.com/products/product_detail.asp?id=1727) (digest, no India evidence). No Jio field confirmation found.
- `mod_gsmopen` and chan_dongle: not installed here; `/usr/lib/freeswitch/mod` has `mod_g729`, `mod_opus`, `mod_rtc`, `mod_verto` among those searched, no `gsmopen` [V]. Earlier note: mod_gsmopen is beta, one modem per channel, Huawei E1550 era, and I would not assume VoLTE [S]. `chan_quectel` is an Asterisk driver with EC25 lists, and users struggle with VoLTE [S, earlier], so a USB-dongle build means a small Asterisk in front of FreeSWITCH [I].
- **Android:** the one open project, `Android-sip-gateway`, needs root, permissive SELinux and Qualcomm mixer paths (`VOC_REC_DL`, `Incall_Music`); one tested device (Redmi Note 7, LineageOS 17.1); VoLTE claimed but unexplained; 34 stars, 2 commits; warns that carriers may block SIMs — [GitHub](https://github.com/s-xander/Android-sip-gateway) [S]. A 2026 article says inject and capture of GSM call audio from an app are "No" — [ICT Innovations](https://ictinnovations.com/using-android-phone-as-gsm-gateway/) (page date inconsistent). Consistent with Android's `CAPTURE_AUDIO_OUTPUT` restriction in the report.
- Voice-only mobile plans as low as about ₹1,800/year, roughly ₹150/month — [Anurag Bhatia](https://anuragbhatia.com/post/2026/09/ip-telephony-in-india/) [S]. Personal-use plan terms are a contract risk, per the report [U].
- Legal status from the earlier report: **SIM box prohibited; one user's single-SIM gateway grey.**

### Inferences
- **Cost per SIM per month, flat regardless of minutes:** 8-port ₹65,264 over 36 months is ₹227 per port + ₹150 SIM = about ₹377; 4-port ₹42,000 is ₹292 + ₹150 = about ₹442. Excludes hosting, bandwidth, spares, and SIMs lost to enforcement. Against Plivo's ₹238 / ₹390, it loses at 100 min and roughly ties at 500 min [I].
- **Capacity:** one call per SIM. A second concurrent caller to that user gets busy, or whatever the operator's forwarding chain does [I].
- **Where it sits matters:** multiple SIMs in one rack is precisely the pattern TCCCPR Schedule IV names (report). [I]
- **Connection:** two shapes. (a) Gateway on a static IP sends INVITE to `:5080`, with each port mapped to a fixed called number. (b) Gateway behind NAT registers. FreeSWITCH-to-gateway registration via `render_gateway` needs the gateway reachable, which a NAT box is not, so a NAT box should register INTO FreeSWITCH as a directory user (next section) [I].
- Each SIM's 10 digits go in `phone_numbers` with `direction='inbound'`, so routing is unchanged, and the sentinel probe skips it (`shared/sentinel.py:449-453` selects `direction='both'`) [V for the filter].
- The report's rule "never put a mobile in `phone_numbers`" was written against personal numbers. A gateway SIM with `direction='inbound'` is a narrower case, but it would need a look before anyone relaxes that rule [I].

### Gaps
- Whether a mobile-to-mobile forward has different tone or CLI behaviour from mobile-to-landline. SIM count threshold under s.42(3)(d) (report, unresolved). Forward to a SIM on the same operator vs another.

## The user's own second SIM in a box at home or office

### Takeaway
Technically workable, and it is the only option where no business rents anything: the number behind the forward is the **user's own KYC'd mobile**. The box dials out to our FreeSWITCH, so no public IP or NAT mapping is needed. The catch is hardware: no verified single-port VoLTE product was found, the cheapest candidate is about ₹20,000 with VoLTE unconfirmed, and availability depends on home power and internet. WebRTC does not help on the GSM side.

### Cited Findings
- Internal profile is configured for this: `auth-calls=$${internal_auth_calls}` (`internal.xml:279`), `internal_auth_calls=true` (`vars.xml:421`), `apply-nat-acl=nat.auto` (`internal.xml:101`), `apply-inbound-acl=domains` (`internal.xml:128`), ws 5066 and wss 7443 (`internal.xml:321,325`), profile `context=public` (`internal.xml:87`) [V].
- The live directory has only stock numbered users (`/etc/freeswitch/directory/default/1000.xml` etc.), and `freeswitch_config.py`'s generated list is gateways, DID dialplan and ACL (`:6-9`), with no directory generator [V]. Per-user credentials would be new code.
- Listening on `10.160.0.8` for 5060, 5066 and 7443 [V via `ss`]. The GCP firewall could not be read, so external reachability of 5060 and 7443 is unknown [U]. `internal_ssl_enable=false`, `external_ssl_enable=false` (`vars.xml:424,430`) [V], so SIP over TLS would need enabling.
- Earlier report: "One user's single-SIM gateway: grey." Bluetooth HFP box: parts exist, no product found (report and `device_side_android_ios.md`).
- Twin SIM and eSIM: an eSIM deactivates the physical SIM, only one active at a time (anecdotal) — [alternative_channels.md](../LesCall%20on%20personal%20phone%20numbers/alternative_channels.md). Gateways in the listings take physical SIMs [I].

### Inferences
What it would look like [I]:
```
Caller -> user's main mobile -> conditional forward -> user's second SIM (own KYC)
       -> box (1-2 port VoLTE gateway | Pi + LTE module + small Asterisk | rooted Android)
       -> outbound TLS/UDP REGISTER to FreeSWITCH internal profile
       -> INVITE to fixed extension = that SIM's 10 digits
       -> context public -> catch-all -> socket :9000 -> same pipeline; per-DID routing unchanged
```
- **Cost:** ₹20,000 over 36 months is ₹556 + ₹150 SIM = about ₹700/month flat, and that hardware's VoLTE is unconfirmed. A Pi plus EC25 would be cheaper but has no confirmed Jio VoLTE and is a lab project. The user pays; LesCall pays nothing for telephony. So at 100 and 500 min it is about ₹700 either way, worse than Plivo unless buying a box beats a DID over a longer life [I].
- **Bandwidth:** Jio quotes about 100 kbps per G.711 channel (digest) — trivial at home. Consumer NAT and jitter are not; the home-to-Mumbai leg adds latency a data-centre carrier does not [I, unmeasured].
- **Double conversion:** VoLTE to G.711 in the box, then L16 8 kHz to the pipeline. The audio quality on FSMN and STT would need T8 from the report [I].
- **Why WebRTC does not help:** the hard part is getting cellular audio out of the radio. A browser or Verto session would only replace the SIP leg between box and FreeSWITCH; a phone cannot use it for a GSM call because third-party apps cannot reach call audio. Useful only if the box is a Linux machine that prefers wss to UDP [I].
- **Security:** a credentialed registrant in `public` must not reach other tenants' numbers. The user-to-DID binding has to be enforced at the socket or dialplan, because `did` is taken from what the box sends (`socket_server.py:548-549`). A compromised box could otherwise name any DID and bill that tenant. This is the same class as the `:5080` gap [I].
- **Outbound:** the box could dial out showing the SIM's number, and the commercial-call prohibition in the report applies. Keep inbound only [I].
- **Honest fit:** a small office with a reliable line, not a consumer product. Forwarding remains in the chain, with all its failure modes (report).

### Gaps
- Any commercial single-port VoLTE gateway on Indian bands; 2-port VoLTE confirmation; Jio field reports. Field reliability of any such box.

## Non-PSTN paths and foreign DIDs

### Takeaway
WhatsApp and a WebRTC link really do avoid a rented number, but neither answers a call dialled to the personal mobile; each is an extra door that a caller must choose. A foreign DID as forward target is the worst option: Jio blocks it, others cost ISD, and it is the wrong shape for the "both legs in India" rule.

### Cited Findings
- **WhatsApp:** covered in `alternative_channels.md`. Meta publishes a FreeSWITCH guide (sofia, TLS, SDES, Opus); user-initiated calls are free; 2,000/day limit for calling; number must leave the app; coexistence excludes calling. No new research.
- **WebRTC:** FreeSWITCH has `mod_verto` and `mod_rtc` loaded (`/etc/freeswitch/autoload_configs/modules.conf.xml:48,50`); `verto.conf.xml` binds 8081 and 8082 secure with `userauth=true`, codecs `opus,h264,vp8` [V]. Plivo's browser SDK would be ₹0.25/min — [Plivo pricing](https://www.plivo.com/voice/pricing/in/) [S], but it isn't needed here. A SIP client on the internal profile, or a WSS gateway with a new sink, are the two routes; port 9001 is loopback, unauthenticated and one-way — [platform_audit.md](../LesCall%20on%20personal%20phone%20numbers/platform_audit.md) [V, earlier].
- **Foreign DID:** Jio excludes international targets ([Jio](https://www.jio.com/help/faq/mobile/services/hd-voice/how-can-i-activate-call-forwarding-service/)). For Airtel, Vi and BSNL, no direct answer was found; Airtel and Vi sell ₹18 ISD packs (May 2026) — [TelecomTalk](https://telecomtalk.info/?p=1007811) (digest, rates not read). "Both legs of the call need to be in India" — [Anurag Bhatia](https://anuragbhatia.com/post/2026/09/ip-telephony-in-india/) [S].

### Inferences
- **WebRTC link or QR ("talk to my assistant"):** ₹0 carrier per call; only our compute and TURN egress. Loses: reach (the caller must open a link), caller identity (no CLI, so `caller_number` is empty and memory, contacts and WhatsApp follow-up have no key; `socket_server.py:539`), and any call dialled to the mobile. Gains wideband audio, but the VAD and level thresholds are tuned for telephony (audit Q5). Agent selection needs a token-to-agent mapping, or a synthetic digit key that fits `did` routing. An anonymous public endpoint needs rate limits, because each session spends LLM and TTS on our account. Regulatory tag: IP-only, no PSTN leg, lowest exposure [I].
- It is a useful **additive** door for a business's website or a printed QR. It does not solve the owner's question [I].
- **Foreign DID:** user pays ISD on every forwarded minute; prepaid or unlimited-domestic plans probably do not cover it [I]. CLI across the international leg is unknown [U]. Media would come back to our India VM from abroad. Regulatory tag: **obvious blocker** (ILD and both-legs) [I]. Excluded from any recommendation.

### Gaps
- ISD rate on an Airtel or Vi forwarded leg; whether those operators allow it at all. No test.

## What changes in the platform per option

### Takeaway
Inbound over any IP carrier needs almost nothing, because `:5080` has no gate and the catch-all answers everything. That convenience is also the security defect, and it must be closed before a second carrier arrives. The real variances are transport (TLS or TCP), Request-URI format, and CLI handling.

### Cited Findings
| Item | Today | Consequence for a non-Plivo carrier |
|---|---|---|
| Trunk row | `trunks` (`shared/db/schema.sql:35-50`), `provider` defaults `plivo` (`shared/telephony_admin.py:262,286`; `dashboard/telephony_api.py:212`); `:495` already says provider-agnostic | A row with another `provider` works; Plivo-only UI hint at `dashboard/telephony_api.py:359-361` [V] |
| Gateway XML | `render_gateway` (`shared/freeswitch_config.py:66-111`); transport appended at `:78-80` | Needed for **outbound** and registration only. An IP-auth inbound trunk needs no gateway to receive [V/I] |
| Registered trunk with many DIDs | `extension` = one caller_id (`:101-104`) | **Bug for multi-DID registered trunks** (Ozonetel-style). Needs `auto_to_user` or similar [V/I] |
| Inbound ACL | `auth-calls=false` (`external.xml:58`), `external_auth_calls=false` (`vars.xml:427`), no `apply-inbound-acl` in `external.xml` (grep empty); Kamailio `inactive` (`systemctl`); `provider_ips` feeds only Kamailio (`shared/telephony_admin.py:169-175,289`); `iptables` has no 5060/5080 rule; GCP firewall unread | Second carrier works instantly; so can anyone reaching the port. A single allow-list across carriers is needed; `render_acl` emits `lescall_providers` (`freeswitch_config.py:163-165`) but nothing enforces it [V] |
| Port and transport | UDP and TCP on 5080 (`ss`, `vars.xml:428`); TLS off (`vars.xml:430`; TLS port 5081 at `:429`). `PUBLIC_SIP_PORT` code default is 5060 (`dashboard/telephony_api.py:37`), `.env` 5080 (audit) | TCP carriers fit today if they allow 5080. Exotel samples show 5070 and TLS 443: ask whether port is configurable, or add a profile. TLS needs cert, `external_ssl_enable=true`, SRTP [V/I] |
| Public IP | `external_sip_ip`, `external_rtp_ip` set in `vars.xml:301,314` | Carriers allow-list this; confirm it is a reserved static IP [V; reservation U] |
| RTP | `switch.conf.xml:150-151` commented, so defaults | Open from each carrier's media range (Exotel 10000–40000 is their side) [V/I] |
| Codec | `global_codec_prefs=OPUS,G722,PCMU,PCMA,…`, `generous` (`vars.xml:255`, `external.xml:56`); `mod_g729` loaded (`modules.conf.xml:95`) | Exotel prefers PCMA, Edesy and Bolna's Airtel are PCMU/PCMA: fine. Ask for G.711, not G.729 or AMR (BSNL lists them) [V/I] |
| Pipeline | `mod_audio_stream` delivers mono L16 at 8 kHz (`socket_server.py:734-802`, `ws_audio_receiver.py:44,60`) | No change for any option [V] |
| DID routing | See previous section | Unchanged if full DID in R-URI; needs a normalising step if short [V/I] |
| Dialplan name | `plivo_inbound` catch-all | Name only; records every call to `/tmp/dialplan_test.wav` (report), which applies to all carriers [V] |
| CLI | `Caller-Caller-ID-Number` (`socket_server.py:539`) | Exotel: From "could be real, masked, or Exophone", PAI carries validated CLI ([guide](https://support.exotel.com/support/solutions/articles/3000132410-exotel-virtual-sip-trunking-fqdn-based)). Which FreeSWITCH uses needs a capture. A masked From collapses memory, contacts and WhatsApp per the report's identity defect [V/I] |
| Diversion | Nothing reads it (audit); logged at `socket_server.py:1932-1933` | Exotel's guides do not mention it. Same T1 test per new carrier [V/S] |
| Sentinel | Probes `direction='both'` joined to trunks (`shared/sentinel.py:449-457`) | Set `direction='inbound'` to skip; silence alert caveat per report [V] |
| Outbound | Default `plivo-outbound` (`shared/fs_originate.py:53`) | Stays unless deliberately moved [V] |
| Media location | `asia-south1-c` (report) | Exotel Mumbai PoP and Telnyx Mumbai anchorsite are in the same metro [S/I] |
| GSM-side cost | User's plan | Unchanged by carrier, except Jio's BSNL exclusion. A DID's series determines reachability from a forward. Untested for any vendor [U] |

### Inferences
- Minimum set for adding one IP carrier: trunk row, `phone_numbers` rows with `direction='inbound'`, a port and transport decision, firewall, an inbound allow-list, and one INVITE capture before any real user [I].
- The allow-list has to arrive with or before the carrier. A carrier that sends from a pool or changes IPs must be asked for its ranges. Enabling `apply-inbound-acl` on the external profile without Plivo's ranges would cut live traffic; seed.py notes `provider_ips` is intentionally empty for Plivo's (`shared/db/seed.py:39-43`) [V/I].

### Gaps
- Exotel's static IP approval flow; whether Exotel's `pstn` mode ("Connect-to-SIP only") is what we need rather than `flow` — [Exotel voicebot](https://developer.exotel.com/docs/sip-trunking/voicebot-integration) [S]. Which FreeSWITCH variable wins when From and PAI differ.

## Comparison table

### Takeaway
There is no free lunch. Plivo at ₹200 stays the cheapest for a pilot. An operator block overtakes it at roughly 40–85 users before circuit costs, and is the only route that removes Plivo and also reduces cost. A user's own SIM removes all rented numbers at a higher price and with lower reliability. Only an operator deal could remove the number, and none is on sale.

### Cited Findings
Costs are telephony only, per user per month, before GST and pulse rounding, and exclude the AI cost (report: about ₹2.99 per talk-minute all-in on one call). Sources are in the sections above. "Fixed" means minutes do not change the figure on my assumption that inbound is free [U]. USD at an assumed ₹90 [I].

| # | Option | Rented number behind? Whose? | 100 min | 500 min | Capacity | Setup time | Platform changes | Reliability | Risk (tag only) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | **Plivo DID (baseline)** | Yes, Plivo | ₹238 [S/I] | ₹390 [S/I] | Elastic, unstated | KYC, then minutes [S] | None | Proven here; single carrier | KYC, reseller question |
| 1 | Edesy | Yes, Edesy | ₹499 | ₹699 | Unstated | KYC about 24 h [S] | Row, port/transport, capture | Unproven; upstream unknown | Licence unstated |
| 2 | Vobiz | Yes, Vobiz | ₹545 | ₹725 | "Add channels" | 24 h, third-party [S] | Same | Launched 2024; SLAs maturing | Licence unstated |
| 3 | Exotel vSIP | Yes, Exotel | **Unknown** | **Unknown** | 20 vs 200 CPM per trunk | Unknown, plans plus KYC | Same; TLS/SRTP or port 5070 question | Docs say Alpha, no SLA | UL VNO per competitor |
| 4 | Telnyx +91 | Yes, Telnyx | **Unknown** | **Unknown** | Unstated | KYC; 12-month minimum | Same | Own sources conflict on coverage | Sell-through to end users needs IDs |
| 5 | didlogic / DIDWW / other foreign | Yes, foreign seller | About ₹810 | About ₹1,170 (didlogic, unit of $0.01 unstated) | Unstated | Up to 48 h docs [S] | Same | DIDWW disclaims reach; reach from Jio untested | Cloud/foreign grey |
| 6 | Twilio +91 toll-free | Yes, Twilio | Unknown | Unknown | Elastic | Unknown | Same | Toll-free as forward target untested | Local DIDs not listed |
| 7 | **Tata or Airtel 30 ch, 100-DID block, full** | Yes, LesCall's operator block | ₹150–200 + circuit [S weak/I] | Same, fixed [U] | 30 calls across users; fine for 100 | Weeks ("longer cycles"), contract [S] | Rows; R-URI check; SBC or colo if circuit | Carrier-grade; 99.5% SLA Tata [S] | Enterprise contract; reseller question |
| 8 | Same block, at 20 users | Yes, same | ₹750–1,000 + circuit [I] | Same | Same | Same | Same | Same | Same |
| 9 | Jio, Vi | Yes, operator | Unknown | Unknown | Jio 10–5,000 | Jio: L2 on premises | Same + site/SBC | Carrier-grade | Same |
| 10 | BSNL trunk | Yes, BSNL | Unknown | Unknown | 10–10,000 | Unknown | Same | **Jio users cannot forward to BSNL** | Same |
| 11 | E1/PRI at site | Yes, operator | Unknown, unpublished | Unknown | 30 per E1 | Site visit | Gateway, tunnel to GCP | Physical line | Site needed |
| 12 | Multi-port gateway, LesCall SIMs | SIM, LesCall's | About ₹377–442 flat [I] | Same | **1 call per SIM** | Days plus SIMs | Row per SIM; R-URI map; ACL | Fragile; SIM blocks | **Prohibited** per report |
| 13 | Home box, user's own second SIM | User's own SIM | About ₹700 flat, user pays [I] | Same | **1 call** | Ship a box | Directory generator; DID binding; TLS | Home power and net | Grey per report |
| 14 | Rooted spare Android | User's SIM | About ₹150 + old phone [I] | Same | 1 call | Lab | As 13 | One tested device | Grey; root |
| 15 | Foreign DID target | Yes, foreign | ISD unknown; high | High | Unstated | Hours | Row | Jio blocks | **Blocker** |
| 16 | WhatsApp Calling (not on the mobile's PSTN calls) | No number rented, the WhatsApp number itself | ₹0 inbound Meta | ₹0 | Vendor-claimed 1,000+ | Portfolio, limit | New TLS profile; Opus | Depends on Meta | Number leaves app |
| 17 | WebRTC link or QR (PSTN callers not served) | None | ₹0 carrier; compute only | ₹0 | Our compute | Days to weeks [I] | WSS endpoint, token map, abuse limits | High, our own | Lowest |
| 18 | Operator CDIV to SIP URI, truly no number | **None** | Not on sale | Not on sale | n/a | Operator deal | New domain route | Unknown | Needs operator |

### Inferences
- **If the goal is to stop depending on Plivo:** a second carrier in parallel on one or two DIDs costs little and proves everything (R-URI, CLI, Diversion, Jio forward acceptance). Edesy or Vobiz are the fastest to try; Exotel is the most established name but needs a quote. Ask Tata and Airtel for a written answer at the same time, because only they change the economics [I].
- **If the goal is literally no rented number:** only rows 13, 14, 16, 17 and 18 meet it. Row 13 is the only one of these that keeps ordinary phone callers without an operator deal [I].
- Nothing removes Plivo from outbound by accident: that takes a deliberate move and A2P declaration work that the report already flags [I].

### Gaps
- All "Unknown" cells need a vendor quote. Row 7 lacks a circuit price. No option above has been tested with a forwarded call, including Plivo (the report found zero real forwarded calls in the logs).
</content>
