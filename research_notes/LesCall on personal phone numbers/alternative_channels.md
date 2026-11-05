# Alternative channels for putting an AI voice agent on a person's own number in India (no forwarding, no handset app)

Research date: 2026-11-03.

How to read the evidence:
- Meta, Plivo, Twilio, Sarvam, LiveKit and Business Today pages were read through a summarising fetcher. Treat "quotes" from them as close to verbatim, and re-open the page before relying on exact wording.
- Items marked "(search summary)" were seen only in a search-result digest. The underlying page was not opened.
- Vendor blogs are marked as such. Several contradict Meta's own docs.

## Bottom line: which channels actually keep the personal number working?

### Takeaway
No channel I could verify gives an AI agent the person's GSM number while the number keeps working on their phone. WhatsApp Business Calling is the only technically clean and well-documented route, and it fits FreeSWITCH directly. But it takes the number off the WhatsApp app (coexistence excludes calling), and it only reaches WhatsApp callers. SIM gateways do work technically, but the SIM leaves the phone and they carry an obvious regulatory risk. I found no Indian operator product that attaches a mobile number to SIP, and verified caller ID is explicitly closed for India at Plivo and effectively closed at Twilio.

### Cited Findings (summary table; details and sources in the sections below)

| Channel | Technically feasible | Connects to FreeSWITCH via | Cost signal | Personal number still works? | India status (Nov 2026) |
|---|---|---|---|---|---|
| WhatsApp Calling on full Cloud API | Yes | SIP/TLS + SDES + Opus (Meta publishes a FreeSWITCH guide), or Graph API + Janus | User-initiated free; business-initiated about ₹0.3885/min on a vendor's reading of Meta's INR card | GSM voice: yes (inference). WhatsApp app on that number: no | Available; India is not on the business-initiated exclusion list |
| WhatsApp Calling under coexistence (Business app + API) | No | n/a | n/a | n/a | Meta's table lists voice/video calls as not supported on Cloud API for these numbers |
| WhatsApp consumer-app number | No direct route; it must first move to the Business app or the Cloud API | n/a | n/a | n/a | Coexistence onboarding describes WhatsApp Business app accounts only |
| VoLTE/GSM gateway with the user's SIM | Yes, hardware exists | SIP from gateway to FreeSWITCH | About ₹20,000 (2-port) to ₹46,000 (4-port) | No, the SIM leaves the phone; I found no verified twin-SIM | Hardware on sale; SIM-box enforcement risk |
| Operator mobile-on-SIP (FMC, "one number") | Not found | n/a | n/a | n/a | No product verified at Airtel, Jio, Vi or Tata |
| Verified caller ID (outbound only) | No | n/a | n/a | n/a | Plivo: not applicable to India. Twilio: outbound to India only from non-Indian numbers |
| Port the mobile number to a SIP carrier | Not found | n/a | n/a | No, a port would remove it from the phone (inference) | One 2026 source says IP-telephony numbers cannot be ported at all |
| Dedicated DID (baseline: a new number) | Yes, already in production | Existing Plivo path | ₹200/month + ₹0.38/min, 30 s pulse | Personal number untouched, but it is not the number the agent answers on | Live; KYC required |

### Inferences
- The honest product framing is a choice: (a) forward or publish an alias (out of scope here, baseline below), or (b) a "WhatsApp line" on a number the user can give up on the WhatsApp app. Option (b) suits a business or second number far better than a real personal one.
- Outbound on the user's real number over GSM has no verified legitimate path in India, other than the SIM physically sitting in a gateway.

### Gaps
- Written answers from Airtel, Jio, Vi and Tata enterprise sales are the only way to close the operator question. Web evidence shows absence, not proof.

## WhatsApp Business Calling API: status, limits, price, signalling

### Takeaway
It is live and documented, and India is eligible for both directions. Inbound is free from Meta. Outbound needs per-user opt-in and is priced close to Plivo per minute, with finer billing pulses. Meta now supports SIP directly and publishes a FreeSWITCH guide, which fits LesCall's stack well. The entry bar is a 2,000/day messaging limit.

### Cited Findings
**Status and India**
- User-initiated calling is available wherever Cloud API is. Business-initiated calling is available everywhere except the US, Canada, Egypt, Vietnam and Nigeria, and the business number's country code must be on the supported list. The page does not name India; India qualifies by not being excluded — [Meta calling overview](https://developers.facebook.com/docs/whatsapp/cloud-api/calling/).
- Meta opened the calling API in India, Indonesia, Brazil and Mexico in June 2024 (search summary) — [Exotel newsroom](https://exotel.com/newsroom/whatsapp-business-calling-service-wont-disrupt-traditional-toll-free-channels/).
- Vendors say India supports both directions — [ChatMitra, 27 Sep 2026](https://chatmitra.com/blog/whatsapp-business-calling-api/); [Chakra, updated 24 Jul 2026](https://chakrahq.com/article/whatsapp-cloud-api-calling-feature-details).

**Prerequisites**
- The number must be "in use with Cloud API (not the WhatsApp Business app)", and the business needs a daily messaging limit of at least 2,000 unique recipients. Test and sandbox numbers are exempt — [Meta calling docs](https://developers.facebook.com/documentation/business-messaging/whatsapp/calling/).
- New portfolios start at a limit of 250. The three paths to 2,000 are business verification, partner verification, or 2,000 delivered template messages to unique numbers outside service windows within 30 days. Meta then reviews quality. Verification is a path, not a hard requirement — [Meta messaging limits](https://developers.facebook.com/documentation/business-messaging/whatsapp/messaging-limits). ChatMitra agrees that verification is not required — [ChatMitra](https://chatmitra.com/blog/whatsapp-business-calling-api/).
- Conflict: a vendor blog claims "Tier 2, 10,000+ contacts", 30 days of GREEN quality and 7–14 days of BSP approval — [RichAutomate, 2 May 2026](https://richautomate.in/blog/whatsapp-business-calling-api-india-2026-implementation-guide). This contradicts Meta's stated 2,000. I would trust Meta. The same blog also says the Meta charge is ₹0, which contradicts Meta's pricing page.

**Permissions and limits**
- Permission comes from a request message (free-form inside the service window, or a template outside it), from the user calling the business (needs the callback setting), or from the business profile. Temporary permission lasts 7 days. Permanent permission has existed since 3 Nov 2025 and lasts until revoked — [Meta user call permissions](https://developers.facebook.com/docs/whatsapp/cloud-api/calling/user-call-permissions/).
- Maximum 1 permission request per 24 h and 2 per 7 days. Both reset on any connected call. 2 consecutive unanswered calls prompt the user to reconsider; 4 auto-revoke. Maximum 100 connected calls per 24 h. Permission messages incur messaging charges — [Meta user call permissions](https://developers.facebook.com/docs/whatsapp/cloud-api/calling/user-call-permissions/). The per-user cap rose from 10 to 100 per day on 19 Dec 2025 — [Meta changelog](https://developers.facebook.com/documentation/business-messaging/whatsapp/calling/). The scope wording (number vs business-user pair) is ambiguous on Meta's page. ChatMitra reads it as per customer.
- Vendor blogs repeating "72 hours / one call" look stale against Meta's 7-day rule ([example](https://kwikfy.com/blog/whatsapp-business-calling-api-guide), search summary; which blog made the claim was not verified).
- Other vendor-reported limits: 10,000 new calls per 24 h per number; concurrency 1,000 vs 5,000 (sources conflict); calling paused 7 days on a feedback restriction; Meta voicemail after up to 30 s — [ChatMitra](https://chatmitra.com/blog/whatsapp-business-calling-api/).

**Pricing**
- "All user-initiated calls are free." Business-initiated calls are billed by duration, destination and volume tier in 6-second pulses. An INR rate card exists, and a payment method is required. Recording and transcription are currently free — [Meta calling pricing](https://developers.facebook.com/documentation/business-messaging/whatsapp/calling/pricing/).
- India list rate: ₹0.3885/min for the first 50,000 min/month, on the INR card effective 1 Oct 2026, excluding tax — [ChatMitra](https://chatmitra.com/blog/whatsapp-business-calling-api/). Another vendor gives $0.005/min for India — [Chakra](https://chakrahq.com/article/whatsapp-cloud-api-calling-feature-details). These are roughly consistent. I could not open Meta's rate card itself.
- India accounts must migrate to INR billing by 31 Dec 2026 — [Meta pricing](https://developers.facebook.com/docs/whatsapp/pricing/).

**Signalling and codecs**
- Three modes: Graph API + webhooks with WebRTC media (ICE, DTLS, SRTP); SIP with WebRTC media; SIP with SDES. Opus is the table codec, with a note: "Additional audio codecs supported: PCMA, PCMU" — [Meta calling docs](https://developers.facebook.com/documentation/business-messaging/whatsapp/calling/).
- SIP needs TLS (mandatory), digest auth with a Meta-generated per-number password, a cert covering the configured hostname, firewall rules for Meta IPs, and an enable step via `POST /<PHONE_NUMBER_ID>/settings`. With SIP enabled, calling Graph API endpoints stop working for that number, and only one SIP server per number is effective. Business-initiated offers must support ICE, DTLS-SRTP and Opus unless SDES is enabled — [Meta SIP docs](https://developers.facebook.com/docs/whatsapp/cloud-api/calling/sip/).
- Conflict: the overview note lists PCMA/PCMU, while the SIP page's SDP examples show Meta offering only Opus — [Meta SIP docs](https://developers.facebook.com/docs/whatsapp/cloud-api/calling/sip/). Plan on Opus on the WhatsApp leg.

**FreeSWITCH integration options**
- Meta publishes a FreeSWITCH-via-SIP guide using sofia, TLS, SDES (`rtp_secure_media`) and Opus on the WhatsApp leg, with an allowlist check on Meta's source IPs. It covers both directions, and uses a 1 s silence to avoid clipping. The port guidance conflicts internally (5081 vs 5061). It also publishes FreeSWITCH + Graph API + Janus (Janus converts DTLS to SDES) and three Asterisk patterns. Marked informational, no support — [Meta integration examples](https://developers.facebook.com/documentation/business-messaging/whatsapp/calling/integration-examples).
- Opus at 48 kHz with 20 ms ptime; the partner acts as DTLS client (search summary) — [Meta integration patterns](https://developers.facebook.com/documentation/business-messaging/whatsapp/calling/integration-patterns).
- Re-INVITEs reportedly fail, per a Jambonz write-up (search summary, not verified on FreeSWITCH) — [Nimblea.pe](https://nimblea.pe/blog/whatsapp-business-calling-with-sip).
- Pipecat has a WhatsApp transport: Cloud API webhooks plus SmallWebRTC per call (search summary) — [Pipecat docs](https://docs.pipecat.ai/api-reference/server/services/transport/whatsapp).
- LiveKit Connectors (1 Sep 2026): `AcceptWhatsAppCall` takes the SDP offer, with no SIP trunk. Generally available on LiveKit Cloud. $0.004/min (Ship), $0.003/min (Scale). Concurrency of 5/20/100 by plan — [LiveKit blog](https://livekit.com/blog/introducing-livekit-connectors).

### Inferences
- Best fit for LesCall: SIP + SDES into the existing FreeSWITCH, then the same media path. FreeSWITCH would decode Opus and hand L16 to `mod_audio_stream`, so the AI pipeline would not change. This needs testing. A new sofia TLS profile, a public cert, and Meta IP rules are the real work. Janus, Pipecat and LiveKit would each add a component LesCall does not need.
- WhatsApp audio is wideband, so forcing it down to L16 8 kHz throws away quality. Sending 16 kHz would be worth testing.
- Cost per 3-minute call: about ₹1.17 on WhatsApp outbound (0.3885 × 3) vs ₹1.14 on Plivo domestic. Inbound is free from Meta, against ₹0.38/min on Plivo. The 6 s pulse favours WhatsApp on short calls, because Plivo bills in 30 s pulses.
- The 2,000 messaging limit is per business portfolio, not per end user. A person onboarding their own individual number would need their own business portfolio to qualify. That is a real barrier for true "personal" users, and LesCall's own portfolio cannot obviously lend it, because the number's identity is a business one.

### Gaps
- I could not open Meta's INR rate card; the ₹0.3885 rate is second-hand.
- I could not verify the template category or cost for a permission request.
- FreeSWITCH + Opus + SDES is untested in this environment.

## Coexistence and the "number stays on the phone" question

### Takeaway
Coexistence does not carry calling, per Meta's own feature table and at least two BSPs. So the calling API requires the number to leave the WhatsApp app entirely, and a consumer-app number cannot enter directly.

### Cited Findings
- Meta's coexistence onboarding page lists "Voice and video calls" as not supported on Cloud API. Calling in the WhatsApp Business app itself is unchanged after onboarding. It needs Business app version 2.24.17+ and has fixed 20 mps throughput — [Meta onboarding Business app users](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/onboarding-business-app-users/).
- 360dialog: "Three features are off-limits on a Coexistence number", one being voice calls through the Calling API. Meta disconnects the API link after about 14 days without activity in the app — [360dialog, sources accessed Oct 2026](https://360dialog.com/blog/whatsapp-coexistence/). Whats.team says the same (search summary) — [Whats.team](https://whats.team/blog/whatsapp-api-coexistence-mode).
- ChatMitra says Meta's calling docs do not explain this case and advises asking the provider — [ChatMitra](https://chatmitra.com/blog/whatsapp-business-calling-api/). That is not a contradiction of Meta's onboarding table, but it shows the calling docs are silent.
- A number already on Cloud API cannot switch into coexistence — [Telnyx coexistence docs](https://developers.telnyx.com/docs/messaging/whatsapp/coexistence) (search summary).
- The coexistence page describes existing WhatsApp Business app accounts. Its `BUSINESS_DOWNGRADE` disconnect reason covers re-registering on the consumer app — [Meta onboarding page](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/onboarding-business-app-users/). This implies consumer accounts do not qualify, though Meta does not state it outright.
- Messaging-only coexistence remains useful and is separate from calling.
- SIM binding: DoT's 28 Nov 2025 directive covers WhatsApp and others. Compliance was reportedly moved to 31 Dec 2026, with enforcement from 1 Jan 2027 — [Telangana Today](https://telanganatoday.com/whatsapp-sim-binding-rules-what-users-need-to-know-from-january-2027); [Outlook Money](https://www.outlookmoney.com/news/new-sim-binding-rules-from-january-2027-whatsapp-telegram-snapchat-to-be-affected) (search summaries). Sources conflict on timing, and one reported 1 Mar 2026 — [Siasat](https://www.siasat.com/rule-to-link-whatsapp-other-messaging-apps-to-sim-come-into-force-on-march-1-3390716/amp/).

### Inferences
- A personal number moved fully to Cloud API keeps ringing as a GSM number on the phone, because WhatsApp registration and the SIM are separate. But the person loses that number's WhatsApp app, including personal chats, and would need another number for it. For a true personal number this is the deal-breaker.
- WhatsApp calls come only from people who choose to call on WhatsApp. An ordinary dial still rings the handset. The agent therefore covers part of the person's calls, and the caller's experience differs from dialling the number. Exotel's CTO made the same point: "I don't think WhatsApp voice is going to be a replacement for regular PSTN" — [Exotel newsroom](https://exotel.com/newsroom/whatsapp-business-calling-service-wont-disrupt-traditional-toll-free-channels/).
- Business verification is not strictly needed, but a person without a business would still need a Meta business portfolio. This fits small traders better than salaried individuals.

### Gaps
- How SIM binding treats Cloud API numbers or coexistence companions: no source found.
- Whether Meta will extend calling to coexistence: no announcement found.
- A hands-on confirmation of the coexistence table in current Meta docs after 2026 changes would be prudent.

## BSP support in India

### Takeaway
Exotel is the only large Indian provider with public WhatsApp calling material. I found no evidence that Interakt or Gupshup sell calling. LesCall can probably sidestep the BSP by connecting SIP itself, but it would need to check how that works for a number whose messaging sits with Interakt.

### Cited Findings
- Exotel introduced a WhatsApp Business Calling API beta in Aug 2024, carrying over IVR and agent features — [Exotel blog](https://exotel.com/blog/introducing-whatsapp-business-calling-api-beta/). A Dec 2025 post says inbound and outbound are "actively developing and supporting" — [Exotel](https://exotel.com/blog/voice-calling-on-whatsapp-business/) (search summary).
- Exotel AgentStream streams bots over PSTN, WhatsApp, in-app and WebRTC; the "majority of voice bot companies" claim is its own — [CXOToday](https://cxotoday.com/media-coverage/exotel-cements-leadership-in-ai-voice-infrastructure/) (search summary).
- Plivo's pricing page shows "$0.0040/min for WhatsApp", but the search index marks the page as very old — [Plivo pricing](https://www.plivo.com/pricing/) (search summary, stale; verify).
- RichAutomate advertises ₹0.40/min (Client Pay) and ₹0.60/min (SaaS Pay) as its own BSP fee — [RichAutomate](https://richautomate.in/blog/whatsapp-business-calling-api-india-2026-implementation-guide) (vendor; other claims doubtful).
- Meta's new account model reportedly lets one number be shared across partners with separate billing, for example a messaging partner plus a calling partner. Confirm both support it — [ChatMitra](https://chatmitra.com/blog/whatsapp-business-calling-api/).

### Inferences
- LesCall's Interakt-held numbers would be the awkward case. The Meta-direct Cloud API numbers can enable SIP directly. Interakt numbers depend on partner sharing, or on Interakt supporting calling.

### Gaps
- Searches returned nothing on calling at Gupshup, Interakt, AiSensy or Wati. Absence in search is not proof; ask them directly.
- Current Exotel prices are unpublished.

## GSM / VoLTE gateways

### Takeaway
The hardware exists and is cheap for a business but expensive per person. The defining problem is not technical: the SIM has to leave the phone, and I found no Indian twin-SIM product to solve that. Jio needs VoLTE-capable hardware. SIM-box enforcement in India is a clear flag for the legal stream.

### Cited Findings
**Prices (dealer listings, Nov 2026 searches, seller and GST vary)**
- Dinstar UC2000-VE 4-port VoLTE: ₹42,000–46,000 — [Uptimum](https://uptimumtech.in.indiagri.in/p/d/dinstar-uc-2000-ve-4-port-volte-gateway-317). ₹37,895, plus 8-port ₹65,264 and 16-port ₹1,26,316, per an IndiaMART seller — [IndiaMART](https://m.indiamart.com/impcat/gsm-gateway-devices.html). A 2-port UC2000-VE is about ₹20,000 and a 32-port about ₹1,30,000 (search summaries; which URL carries which listing was not verified; VoLTE not confirmed on the 2-port).
- No single-port VoLTE Dinstar listing was found.
- Yeastar TG series offers LTE trunks with VoLTE listed — [Yeastar](https://www.yeastar.com/gsm-cdma-umts-gateways/). A TG200 LTE-A 2-port is sold abroad — [The Telecom Spot](https://www.thetelecomspot.com/products/yeastar-neogate-tg200-lte-a-voip-gateway). A reseller warns that bands such as B2/B4/B12 and VoLTE provisioning depend on module and carrier — [Flytec](https://flyteccomputers.com/yealink-yst-tg800lte-a).

**DIY modules**
- `asterisk-chan-quectel` forks list EC25 with voice over USB and UAC — [GitHub fork](https://github.com/IchthysMaranatha/asterisk-chan-quectel). Quectel's forum gives the AT+QPCMV procedure, and users report struggling with VoLTE on EC25 variants — [Quectel forum](https://forums.quectel.com/t/ec25-au-brazil-lte-not-sucess-call-over-lte-volte/2575). No confirmed Jio VoLTE working setup found.

**Networks**
- A reseller says "All Jio SIMs support VoLTE by default" — [Prune](https://prune.co.in/help-center/india-sim/jio/does-jio-sim-support-volte-and-5g). A 2017 Airtel earnings summary notes Jio as the VoLTE-only operator — [Medianama](https://www.medianama.com/2017/08/223-airtel-q1-fy18-earnings-call/). Dated, but consistent with Jio's 4G-only launch.
- Airtel has retired 3G circle by circle and kept 2G for feature phones (older reports) — [Communications Today](https://www.communicationstoday.co.in/airtel-3g-service-now-shut-down-in-kerala-upgraded-to-4g/). Vi shut 3G in 5 circles by Q3 FY24 — [TelecomTalk](https://telecomtalk.info/vodafone-idea-to-shut-down-3g-completely-in-fy25/915663/).
- Jio and Vi asked TRAI for a 2G/3G shutdown plan in early 2024, and Vi later challenged the framing. No date confirmed — [VoiceNData](https://www.voicendata.com/news/jios-stance-on-the-shutdown-of-2g-and-3g-challenged-by-vodafone-idea-3651302). I found no formal Indian 2G sunset schedule. Sept 2026 dates in search results are for Vietnam.

**Twin SIM / eSIM**
- Airtel users report that an eSIM deactivates the physical SIM; only one is active at a time — [TechEnclave forum](https://techenclave.com/t/pros-and-cons-of-using-esim/249354); [how-to site](https://www.exhibit.tech/trending-tech-news/how-to-activate-an-esim). A forum user says the only two-device case is a watch secondary eSIM — [Team-BHP](https://www.team-bhp.com/forum/gadgets-computers-software/281323-esim-new-anxiety-3.html). All anecdotal. Attribution of each quote among these forum links was not verified.
- No official twin-SIM or multi-SIM voice product was found for Airtel, Jio or Vi.

**Legal flag (legal stream to assess)**
- A DoT complaint led to a Bengaluru bust with 6 SIM boxes and 133 SIMs — [Deccan Herald](https://www.deccanherald.com/india/karnataka/bengaluru/duo-held-for-illegally-routing-international-telephone-calls-as-local-3665360). A Delhi HC record describes SIM-box-like bulk SIM misuse and "automated calling", prohibited under DoT guidelines dated 20.05.2024 — [Delhi High Court](https://delhihighcourt.nic.in/app/showFileJudgment/SKS21012026BA1642026_175316.txt) (snippet only). A 2026 practitioner blog: "Anyone without a licence cannot connect IP with PSTN in India" — [Anurag Bhatia](https://anuragbhatia.com/post/2026/09/ip-telephony-in-india/).

### Inferences
- Connection to FreeSWITCH is straightforward: the gateway registers to or trunks with FreeSWITCH over SIP (G.711), and the rest of the media path is unchanged. Per-person cost is the cheap part relative to the logistics: a physical box somewhere, the SIM out of the phone, a mailed or hosted SIM.
- Without a second active SIM, the user no longer has their phone on that number. That fails the objective unless the user keeps a different SIM and forwards back, which is forwarding again.
- An EC25 build could cost far less than a Dinstar, but carries high VoLTE/IMS risk on Indian carriers. Treat it as a lab experiment, not a product.
- A watch-eSIM profile on a gateway is the only "dual registration" idea left. I found no evidence that it is possible, and I expect operators tie such profiles to paired watches.

### Gaps
- Ejoin, Synway, OpenVox, Grandstream, `chan_dongle` and `mod_gsmopen`: searches returned nothing usable. I would not assume VoLTE support for `chan_dongle` or `mod_gsmopen`, whose documentation I did not see.
- Jio-specific compatibility of any gateway is unconfirmed. Vendor test evidence is needed. Yeastar's band list above does not obviously match Indian LTE bands (from memory, Jio uses B3/B5/B40; verify).
- Airtel/Jio official eSIM second-device policy: no primary source.

## Operator-based attachment: can a mobile number ring in the cloud?

### Takeaway
I could not find any Indian operator product that places an existing 10-digit mobile number on SIP or in a cloud. Everything verifiable concerns landline, DID, or 140/160-series numbers. US analogues exist but are carrier-internal and show no SIP or AI hook.

### Cited Findings
- Plivo's rentable Indian series are landline (022, 080 and other city series), 140 (promotional), and 160 (BFSI). Mobile numbers are mentioned only as a 10-digit format, not as rentable — [Plivo rent India numbers](https://www.plivo.com/docs/numbers/rent-india-numbers.md).
- Tata Smartflo case studies keep existing PSTN/landline or SIP-trunk numbers, but none addresses mobile numbers — [private bank](https://www.tatatelebusiness.com/case-studies/smartflo-optimises-helpdesk-solutions-for-private-banks-data-processing-and-customer-service-unit/); [e-commerce](https://www.tatatelebusiness.com/case-studies/fashnear-technologies-an-e-commerce-company-streamlines-its-business-communication-with-secure-and-flexible-smartflo/). Smartflo virtual numbers are cloud numbers — [Tata](https://www.tatatelebusiness.com/virtual-number/).
- Airtel: the only FMC trace is a 2007 report of a planned common-number service. It appeared in a search digest, and I could not tie it to a specific URL, so treat it as uncited and irrelevant to 2026. Airtel SIP trunking exists for enterprises — [Bolna integration page](https://www.bolna.ai/integrations/sip-airtel). No current FMC page was found.
- Jio's SIP trunk FAQ says delivery is on Ethernet and GPON last mile — [Anurag Bhatia, Sep 2026](https://anuragbhatia.com/post/2026/09/ip-telephony-in-india/). This suggests a fixed product, not a mobile one.
- Practitioner summary: both call legs must be in India, the IP-to-PSTN part is limited to authorised access providers, and cloud-hosted PBX status is a grey area — [Anurag Bhatia](https://anuragbhatia.com/post/2026/09/ip-telephony-in-india/).
- Number portability: "there is no way to port a number from one provider to another" for IP telephony DIDs — [Anurag Bhatia](https://anuragbhatia.com/post/2026/09/ip-telephony-in-india/). A 3CX vendor guide says landline/DID LNP is possible — [altF9](https://altf9.tech/india-sip-trunk-3cx-recommendation/). These conflict, and neither cites DoT or TRAI.
- US analogues: T-Mobile DIGITS gives up to 5 devices per number; Verizon One Talk rings desk and mobile together. The coverage is from 2016–17 with nothing on SIP or AI hooks — [T-Mobile press](https://www.t-mobile.com/news/press/digits-launch); [No Jitter](https://www.nojitter.com/whats-t-mobile-digit). Their current status was not checked. AT&T Office@Hand was not researched.

### Inferences
- A US-style "number in the network" is an operator feature on the IMS core, so it cannot be replicated from outside. In India it would have to be an operator deal, not an integration.
- MNP only moves the number to another mobile operator. Google Voice-style or "hosted number" routes have no visible Indian path.

### Gaps
- Airtel IQ FMC, "Mobile SIP", Jio cloud telephony, Vi Business Smart Convergence and "One Number", Airtel "Business Number": I could not verify any of these from primary pages. Searches returned no current product pages. Marketing names in the brief remain unconfirmed.
- DoT/TRAI primary text on whether a mobile-series number may terminate on SIP: not found.
- Twilio Hosted SMS: no India evidence found. It would not solve voice anyway.

## Verified caller ID (outbound on the user's own number)

### Takeaway
Closed for India at Plivo in writing, and blocked at Twilio's route level. The Retell and Vapi "verified" flows do not establish an Indian route.

### Cited Findings
- Plivo: India "Not applicable. Indian regulations require using Plivo-rented Indian numbers as caller ID." Verified caller ID works by OTP and is "primarily designed for US compliance" — [Plivo verified caller ID](https://www.plivo.com/docs/voice/concepts/verified-caller-id.md).
- Plivo India: calls from an Indian number must originate from a number rented on your account, with no arbitrary caller IDs — [Plivo rent India numbers](https://www.plivo.com/docs/numbers/rent-india-numbers.md). Both call legs stay in India, or calls fail with `violates_media_anchoring` — [Plivo India calling](https://www.plivo.com/docs/voice/concepts/india-calling).
- Twilio: "Outbound calls to India can only be made from international (non-Indian) numbers." The domestic row is N/A. Consent is required; voice OTP may be blocked; verified caller ID is not addressed — [Twilio India voice guidelines](https://www.twilio.com/en-us/guidelines/in/voice).
- A third-party help article says +91 caller ID to +91 destination calls stopped working from 1 Aug 2024 (search summary, not checked against a regulator) — [GoHighLevel help](https://help.gohighlevel.com/support/solutions/articles/155000003232) or [agency mirror](https://agencysupport.ladesk.com/354388-How-to-Set-Up-Verified-Caller-ID-Use-your-number-for-Voice-Calls).
- Retell, 6 Feb 2026: a user asked to use their Indian Airtel mobile as outbound caller ID via OTP. Support pointed to SIP trunking and a Telnyx verification approach, and said: "I cannot confirm from the available documentation whether Indian mobile numbers are specifically supported" — [Retell community](https://community.retellai.com/t/enable-verified-caller-id-indian-airtel-number-for-outbound-calls/789.md).
- Vapi forum: a client can verify their main number without porting it, for use as caller ID (search summary; page fetch returned 429; no India mention seen) — [Vapi support](https://support.vapi.ai/t/26928144/masking-calls).

### Inferences
- A verified-CLI route via a foreign carrier would mean presenting a +91 CLI from an international route. That is exactly what Indian anti-spoofing controls target, so it should be treated as closed. Legal stream should confirm.

### Gaps
- Exotel's position on verified caller ID: not found.

## Competitor and comparable landscape

### Takeaway
Everyone I could verify on the "own number" pitch uses conditional forwarding or a device feature. Indian voice-AI platforms use provider DIDs and do not promise a personal number. None of the sources I saw describes a SIM-free, forwarding-free attachment in India.

### Cited Findings
- **Truecaller:** free AI voicemail launched 18–19 Dec 2025 for Android in India, 12 languages. Users must enable call forwarding on their number; recordings stay on device. Premium adds the interactive Assistant — [Business Today](https://www.businesstoday.in/amp/technology/news/story/truecaller-launches-free-ai-powered-voicemail-for-android-with-12-indian-languages-507367-2025-12-19). Assistant launched in India in July 2023 at ₹149/month (₹99 promo) (stale) — [GSMArena](https://m.gsmarena.com/truecaller_assistant_india_launch_price_details-news-59245.php).
- **Hiya AI Phone:** app answers unknown calls and screens them; US only at launch; $9.99/month or $99.99/year — [Help Net Security](https://www.helpnetsecurity.com/2025/01/28/hiya-ai-phone/); [App Store](https://apps.apple.com/us/app/-/id6474703665).
- **Google Call Screen:** on-device; Pixel-only rollout in India reported — [Gulf News](https://gulfnews.com/technology/media/google-ramps-up-ai-scam-protection-in-india-1.500355149) (search summary; date and models unclear). Earlier coverage from June 2025 said it was not offered — [Nokiamob](https://nokiamob.net/2025/06/26/googles-call-screening-feature-finally-coming-to-pixel-phones-in-india/).
- **Jio:** JioPhonecall AI (2024) records and transcribes, opt-in via MyJio. It does not answer as an agent, as far as reported — [Medianama](https://www.medianama.com/2024/11/223-google-pixel-working-on-ai-replies-feature-for-contextual-screening-of-phone-calls/) (search summary, attribution uncertain).
- **Goodcall:** forward your line or use an included number; if forwarding lapses, calls miss the AI. Price conflict: $66/$108/$208 (annual) vs $79–249 — [Macha](https://www.getmacha.com/blog/goodcall-complete-guide); [CloudTalk, May 2026](https://www.cloudtalk.io/blog/goodcall-pricing/). Sources include competitors.
- **Retell:** custom telephony by SIP trunk or Dial-to-SIP. No native Indian caller-ID path confirmed — [Retell community](https://community.retellai.com/t/enable-verified-caller-id-indian-airtel-number-for-outbound-calls/789.md).
- **Vapi:** forum evidence only for verified caller ID; official call-forwarding docs cover `transferCall`, not inbound attachment — [Vapi docs](https://docs.vapi.ai/call_forwarding).
- **Sarvam voice agents:** bring-your-own telephony across Exotel, Twilio, Smartflo, Pulse, Intalk, Vobiz and Plivo, or rent a number from Sarvam. No personal-mobile path stated — [Sarvam](https://docs.sarvam.ai/voice-agents/deploy/telephony/bring-your-own). Smartflo needs "at least one Smartflo phone number" — [Sarvam Smartflo](https://docs.sarvam.ai/voice-agents/deploy/telephony/smartflo).
- **Bolna:** "go live with Indian numbers", Airtel SIP integration — [Bolna blog](https://blog.bolna.ai/indian-voice-ai-platform-for-businesses/); [Bolna Airtel](https://www.bolna.ai/integrations/sip-airtel).
- **Ringg:** $0.06 (Enterprise) / $0.10 (Flexible) per min, numbers extra — [Ringg blog](https://www.ringg.ai/blogs/best-voice-ai-agents-for-indian-languages) (vendor; the review citation is second-hand).

### Inferences
- Truecaller's use of forwarding, at Truecaller's scale in India, is strong evidence that no better consumer path was available to it, though this is my reading and not a Truecaller statement.
- Indian platforms' silence on personal numbers fits the Plivo and Twilio rules above.

### Gaps
- Dialzara, Rosie, Smallest.ai, Bland: no usable results.
- Airtel AI spam offering: no result returned.
- Apple call screening in India: no India-specific source.
- Retell's and Vapi's official "existing number" onboarding pages were not retrieved (a Vapi docs URL returned 404, a forum returned 429). Their flow is known only from forums.
- Mechanism of Truecaller's interactive Assistant is reported for voicemail only; I assume it is the same but did not verify.

## Baseline: dedicated DID as a personal alias

### Takeaway
A Plivo DID costs ₹200/month plus ₹0.38/min with 30-second pulses. That is about ₹238/month at 100 minutes and ₹390/month at 500 minutes before taxes. The catch for personal users is KYC, which expects business documents, and resellers need a separate application per customer.

### Cited Findings
- India domestic: inbound and outbound ₹0.38/min; browser SDK/WebRTC ₹0.25/min each; number rental ₹200/month; 30 s pulse — [Plivo India pricing](https://www.plivo.com/voice/pricing/in/). Tax treatment is not stated on the page.
- Only India-registered businesses may rent Indian numbers or use domestic routes; KYC first; payment in INR to Plivo's Indian entity — [Plivo India calling FAQ](https://plivo.com/docs/faq/voice-api-and-SIP-trunking/india-calling).
- KYC documents conflict: one valid document among GST, COI or Udyam (first application sealed and signed; 080/022 review about 5 min) — [Plivo rent India numbers](https://www.plivo.com/docs/numbers/rent-india-numbers.md). Another page wants a COI and GST certificate — [Plivo India calling](https://www.plivo.com/docs/voice/concepts/india-calling) (search summary).
- Resellers need a separate application per customer, and a subaccount's application backs only that subaccount's numbers — [Plivo rent India numbers](https://www.plivo.com/docs/numbers/rent-india-numbers.md).
- Landline series is for service/transactional traffic; 140 series is for promotional voice — [Plivo rent India numbers](https://www.plivo.com/docs/numbers/rent-india-numbers.md). Inbound rules cover India-to-India calls — [Plivo India calling](https://www.plivo.com/docs/voice/concepts/india-calling).
- Third-party figures differ, for example ₹0.60/min local and ₹0.34/min SIP (search summary, vendor) — [FreJun](https://frejun.com/teler-blog/voice-api-provider-india/). Prefer Plivo's own page.

### Inferences
- Arithmetic: 100 min = 200 + 38 = ₹238; 500 min = 200 + 190 = ₹390. Pulse rounding adds to this for short calls.
- At this price a dedicated DID is low-cost for a business but not trivial as an individual's subscription input.
- An individual with no GST, COI or Udyam document does not fit the application form under their own name. If LesCall holds all numbers under its own KYC, whether that counts as reselling is a question for Plivo and the legal stream.
- The number the caller sees is an 080 or 022 landline, not a mobile, which is a visible change from a "personal number".

### Gaps
- The cost of the forwarding leg charged by the user's mobile operator: not researched here (forwarding is out of scope for this note).
- Whether Plivo would accept per-user applications at consumer scale, and any number-count caps: not documented on the page.
- Number masking products: no specific India pricing found.
</content>
