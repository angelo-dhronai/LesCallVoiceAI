# Call forwarding on Indian mobile networks as a way to attach an AI voice agent to a personal number (as of 2026-11-03)

Reading key: **[OFFICIAL]** = operator, regulator or standards text. **[VENDOR]** = a company selling a related product (useful for practice, biased on numbers). **[PRESS]** = news coverage. **[ANECDOTE]** = forum or user report. Items older than 2 years (before Nov 2024) are marked **STALE?**.

Research limits that apply throughout:
- The DoT circular itself was not obtained; only press quotations of it.
- reddit.com, truecaller.com and web.archive.org could not be read with these tools, and newsbytes and mysmartprice returned 403. Reddit complaints and Truecaller's own help pages are therefore NOT covered.
- Page contents passed through a summarising fetch tool, so short quotes are as reported by it. Re-check any quote before publishing it.

## 1. DoT USSD suspension: current status and scope

### Takeaway
The order dated 28 March 2024 suspended "USSD-based call forwarding services" from 15 April 2024 "till further notice"; I found no notice lifting or extending it. In practice forwarding is alive in late 2026: operators document Settings and app routes, and Indian vendors are still onboarding users with `**61*`/`**62*`/`**67*` codes. No primary text settles whether the order legally covers MMI codes.

### Cited Findings
- **Order text as quoted.** "All licensees shall discontinue the existing USSD-based call forwarding services with effect from April 15, 2024, till further notice." Order dated 28 March 2024. **[PRESS] STALE?** — [Communications Today, 30 Mar 2024](https://www.communicationstoday.co.in/dot-directs-deactivation-of-ussd-based-call-forwarding-by-april-15/); [Asianet Newsable](https://newsable.asianetnews.com/technology/stop-using-401-code-for-activating-call-forwarding-from-april-15-dot-tells-telecom-companies-sbas3n)
- **Named scope.** The order says the "USSD based call forwarding facility, most commonly known *401# services for unconditional call forwarding services, is being misused". It names the unconditional service as the example and, as quoted, lists no conditional or MMI codes. **[PRESS] STALE?** — [OpIndia / search extract](https://www.opindia.com/2024/03/dot-orders-telecom-operators-to-discontinue-ussd-based-call-forwarding/); [APAC News Network](https://apacnewsnetwork.com/2024/03/dot-directs-telcos-to-cease-ussd-based-call-forwarding-shift-to-alternatives-by-april-15/)
- **Existing users.** They "may be asked to reactivate call forwarding services through alternative methods", so that services "are not activated without their notice". The article names no alternative methods. **[PRESS] STALE?** — [Communications Today](https://www.communicationstoday.co.in/dot-directs-deactivation-of-ussd-based-call-forwarding-by-april-15/)
- **Status in Oct 2026.** An Indian vendor's author "found no notice ending the order" and "no operator statement on which other codes it covers". **[VENDOR]** — [Autobot, 20 Oct 2026](https://getautobot.app/blog/call-forwarding-codes)
- **Jio help page.** It lists the MyJio app and handset Settings as the routes, and no dial codes. **[OFFICIAL, undated]** — [Jio help](https://www.jio.com/help/faq/mobile/services/hd-voice/how-can-i-activate-call-forwarding-service/)
- **Jio's MyJio control.** Added by Feb 2026, it only works on mobile data; on Wi-Fi the app asks the user to switch "for security purposes". **[PRESS]** — [TelecomTalk, 15 Feb 2026](https://telecomtalk.info/jio-now-users-manage-call-forwarding-myjio/1004442/)
- **Jio all-calls code.** One guide says the all-calls code "has been disabled by Jio on the instructions from Govt. of India", and all-calls forwarding must be set in phone settings. Seen as a search extract only; page date not confirmed. **[PRESS]** — [MySmartPrice](https://www.mysmartprice.com/gear/?p=252429)
- **Airtel still documents a dialer route.** Dial `*21`, the number, then call; `##21#` cancels; `*#21#` checks. It also documents an Airtel Thanks app route with all four forwarding types. It does not mention the DoT order. **[OFFICIAL, published 2 Apr 2024, updated 22 May 2026]** — [Airtel blog](https://www.airtel.in/blog/airtel-thanks-app/activate-and-deactivate-call-forwarding-in-airtel/)
- **Vendors use conditional MMI codes in 2026.**
  - Exotel tells Airtel/Jio users to dial `**67*`, `**61*`, `**62*` + Exotel number + `#`, says "same MMI codes work on Jio", and says Vi "may prompt a confirmation SMS". **[VENDOR]** — [Exotel, 25 May 2026](https://exotel.com/build/set-up-a-voice-agent-for-your-phone-number-in-an-afternoon/)
  - hiRobin registers three conditional rules by MMI. **[VENDOR]** — [hiRobin, 2026](https://www.hirobin.ai/blog/call-forwarding-failures-india)
- **Some codes may fail.** A vendor guide notes the suspension and says some codes may not work, without saying which. It advises Settings or the operator app as fallback. **[VENDOR]** — [CallerDesk, 8 Oct 2026](https://callerdesk.io/blog/turn-off-call-forwarding-jio-airtel-vi-bsnl/)
- **No 2026 forwarding rule found in adjacent DoT action.**
  - The July 2026 network authorisation rules coverage does not mention forwarding. — [Medianama, Jul 2026](https://www.medianama.com/2026/07/223-dot-telecommunications-authorisation-rules-2026/)
  - A DoT circular dated 13 Oct 2026 orders CNAP (calling name) on 4G/5G from 1 Nov 2026. — [ThePrint](https://theprint.in/india/no-more-unknown-callers-govt-makes-caller-name-display-mandatory-from-1-november/3072774/)
- **Fraud pressure continues.** DoT warns that it never asks subscribers to dial *401#, and urges them to disable forwarding they did not set up (date not shown). **[OFFICIAL via press]** — [News On Air](https://newsonair.gov.in/government-issues-advisory-against-calls-impersonating-dot-threatening-people-to-disconnect-mobile-numbers). A later scam warning covers `*21*` capturing OTP calls. **[ANECDOTE]** — [LinkedIn post](https://www.linkedin.com/posts/surender-singh-a28711132_cybersecurity-fraudalert-onlinesafety-activity-7374109097681600512-U60M)

### Inferences
- Treat the order as still in force. Its target in practice was the unconditional `*401`-style flow, and that is the path most likely to be blocked (Jio especially).
- Conditional forwarding (`**61/62/67` or the Jio equivalents) has live 2026 commercial use by Exotel and hiRobin, which is good evidence it functions but is not proof of legal clearance.
- Handset Settings and operator apps are the only routes operators document clearly and the safest to build onboarding around, with codes as the alternative.
- My reasoning, not sourced: `**21*`-style strings are GSM supplementary-service MMI rather than USSD in the strict protocol sense. The order's quoted wording does not draw this distinction, so do not rely on it as a legal argument.
- I found no re-verification, periodic re-consent or mandatory notification requirement after 2024. Absence from my searches is weak evidence.

### Gaps
- The DoT circular's full text and reference number were not found on dot.gov.in or elsewhere. Get it from a telecom counsel or by asking DoT or an operator's regulatory desk.
- Whether any operator enforces at network level per code, per operator and per circle. **5-minute test:** on one Jio, Airtel, Vi and BSNL SIM each, try the code, then Settings, then the app, for each of the 4 types. Log the result and read it back with the status code.

## 2. Per-operator support and activation

### Takeaway
All four operators support all four forwarding types. Only Jio's official text explicitly allows landline destinations, and it excludes BSNL numbers, the user's own Jio number and international numbers. That BSNL exclusion matters if a DID's underlying carrier is BSNL.

### Cited Findings
**Code table** (2026 vendor compilation, matching a 2022 press list; operator pages publish fewer of these). **[VENDOR]** — [Autobot, Oct 2026](https://getautobot.app/blog/call-forwarding-codes); **STALE?** [TelecomTalk, Jan 2022](https://telecomtalk.info/how-to-start-and-stop-call-forwarding/493353/)

| Type | Airtel / Vi / BSNL set | Cancel | Jio set | Cancel |
|---|---|---|---|---|
| All | `**21*n#` | `##21#` | `*401*n` | `*402` |
| Busy | `**67*n#` | `##67#` | `*405*n` | `*406` |
| No answer | `**61*n#` | `##61#` | `*403*n` | `*404` |
| Unreachable | `**62*n#` | `##62#` | `*409*n` | `*410` |
| Cancel everything | | `##002#` | | `*413` |

- **Jio codes conflict across sources.**
  - Some guides swap `*403` and `*409`. — [search compilation including Gizbot](https://www.gizbot.com/amphtml/telecom/news/how-to-deactivate-call-forwarding-in-jio-072564.html)
  - Exotel says MMI works on Jio. — [Exotel](https://exotel.com/build/set-up-a-voice-agent-for-your-phone-number-in-an-afternoon/)
  - `##002#` "may not work on every Jio connection"; use `*413`. — [CallerDesk](https://callerdesk.io/blog/turn-off-call-forwarding-jio-airtel-vi-bsnl/)
- **Jio [OFFICIAL, undated].**
  - MyJio path: Profile > Mobile Settings > Service Settings > Call Forwarding > Voice/Video, then SIM verification and a 10-digit number. Handset path: Settings > Call > Advance settings > Call forwarding.
  - "Call forward/divert can be done to all mobile and landline numbers", except BSNL numbers, the user's own Jio number and international numbers. An active plan is required. — [Jio help](https://www.jio.com/help/faq/mobile/services/hd-voice/how-can-i-activate-call-forwarding-service/)
- **Airtel [OFFICIAL].** Dialer and Airtel Thanks app. — [Airtel blog, updated May 2026](https://www.airtel.in/blog/airtel-thanks-app/activate-and-deactivate-call-forwarding-in-airtel/)
- **Vi [VENDOR].** Codes as above; status checks `*#67#`, `*#61#`, `*#62#`. — [Autobot](https://getautobot.app/blog/call-forwarding-codes). I found no official Vi page.
- **BSNL.** +91 is required before the number; no status codes are published. **[VENDOR]** — [Autobot](https://getautobot.app/blog/call-forwarding-codes). BSNL Chennai 2021 circulars confirm forwarding to landlines and to other operators' mobiles on prepaid and postpaid. **[OFFICIAL] STALE?** — [prepaid](https://www.chennai.bsnl.co.in/latestnews/2021/FEB/web%20copy%20-%20Free%20call%20forwarding%20facility%20under%20GSM%20Prepaid%20mobile%20services_2021_02_04_16_02_58.pdf); [postpaid](https://www.chennai.bsnl.co.in/latestnews/2021/FEB/web%20copy%20Free%20call%20forwarding%20facility%20under%20GSM%20Postpaid%20mobile%20services%20and%20rationalization%20of%20postpaid%20plan199_2021_02_04_16_03_21.pdf)
- **Landline-series forward targets are in commercial use.** hiRobin publishes 8035738787 (Android) and 8031340381 (iOS) as its forward targets, and advises retrying with +91 if the carrier rejects the code. **[VENDOR]** — [hiRobin](https://www.hirobin.ai/blog/call-forwarding-failures-india)
- **iPhone.** The Settings menu forwards all calls only; conditional forwarding requires codes. Exotel adds that Live Voicemail must be off or carrier forwarding never triggers. **[VENDOR]** — [Exotel](https://exotel.com/build/set-up-a-voice-agent-for-your-phone-number-in-an-afternoon/); [CallerDesk](https://callerdesk.io/blog/turn-off-call-forwarding-jio-airtel-vi-bsnl/)
- **1600 and 140 series.** 1600 is designated for service and transactional calls by regulated BFSI entities and government; 140 is for promotional calls. **[OFFICIAL via press]** — [Outlook Business on TRAI's statement](https://www.outlookbusiness.com/news/phone-calls-from-1600-number-series-cannot-be-blocked-dnd-app-can-block-calls-from-140-series-trai); [TRAI PR 91/2026](https://trai.gov.in/sites/default/files/2026-07/PR_No91of2026.pdf)
- **Plivo India numbers.** Domestic numbers only, inbound and outbound; KYC required; India data-region account required. Media must be anchored in India, otherwise hangup code 4590. **[VENDOR/OFFICIAL for Plivo]** — [Plivo coverage](https://www.plivo.com/sip-trunking/coverage/in/); [Plivo India guide](https://www.plivo.com/docs/voice-agents/sip-trunking/deploy/calling-in-india.md)

### Inferences
- A geographic 080 or 022 DID is the right target class. hiRobin's numbers are direct evidence for 080, and Jio's text explicitly allows landlines.
- 1600 and 140 are the wrong class: they are defined by outbound use-case and sector. I found nothing saying they accept inbound or forwarded calls, and I would not design around them.
- Dialled-in activation will be unreliable on iPhone and Jio. Assume a per-operator, per-OS onboarding matrix with a read-back step.

### Gaps
- Which underlying carrier serves Plivo's India DIDs (Jio's BSNL exclusion would bite if BSNL), and whether Plivo offers 080 or 022 specifically. Plivo's pages did not say. Ask Plivo support, and run the Jio test in section 8.
- Official operator statements on forwarding to toll-free or 1600 numbers, and prepaid versus postpaid differences. I found no prepaid/postpaid rule except Exotel's tone-removal terms (section 3 and 7) and BSNL's plan wording.

## 3. Tariff

### Takeaway
No operator tariff I found says forwarded calls are excluded from unlimited plans, and none says they are included. The evidence points to "forwarded leg is an outgoing call from the subscriber" without being conclusive, so treat this as unverified and test it with a live balance check.

### Cited Findings
- **Jio.** "Call forward service is free of cost", with an active plan required. It does not say whether the forwarded leg consumes plan minutes. **[OFFICIAL, undated]** — [Jio help](https://www.jio.com/help/faq/mobile/services/hd-voice/how-can-i-activate-call-forwarding-service/)
- **Airtel.** "You will be charged appropriate charges if you forward calls to a long-distance or toll number." No amounts given. **[OFFICIAL, 20 Mar 2024] STALE?** — [Airtel blog](https://www.airtel.in/blog/postpaid/call-forwarding-and-its-use/)
- **BSNL.** Forwarding on prepaid and postpaid is charged at the plan's voice rate. **[OFFICIAL, circle circular, 2021] STALE?** — [BSNL Chennai prepaid](https://www.chennai.bsnl.co.in/latestnews/2021/FEB/web%20copy%20-%20Free%20call%20forwarding%20facility%20under%20GSM%20Prepaid%20mobile%20services_2021_02_04_16_02_58.pdf). Earlier cheap-forwarding vouchers were withdrawn. **[PRESS] STALE?** — [TelecomTalk](https://telecomtalk.info/?p=156236)
- **Vendor summaries.**
  - "The GSM standard treats the forwarded leg as an outgoing call from your home network" (its reading of ETSI TS 122 082). Abroad, both legs are charged on Airtel. — [Autobot](https://getautobot.app/blog/call-forwarding-codes)
  - The forwarded leg is usually paid by the SIM owner "at operator rates". — [Edesy, 2026](https://edesy.in/blog/forward-business-number-to-multiple-phones-india-2026.md)
- **Unlimited plans.** Airtel's current plans list unlimited local, STD and roaming voice. The article does not mention forwarding. **[PRESS]** — [TelecomTalk, Aug 2026](https://telecomtalk.info/airtel-entry-level-unlimited-plans-august-2026/1010873/)

### Inferences
- On an unlimited domestic plan the likely outcome is zero marginal cost to the user. This is an inference from "unlimited" plan language plus "billed as a regular call"; I have no operator confirmation.
- The original caller should pay only for the call they dialled, since the forwarded leg is attributed to the forwarder (per the vendor summaries above). I found no Indian operator text stating this.

### Gaps
- Real plan T&Cs covering forwarding, any fair-usage limit and any per-minute rate. None were found for Jio, Airtel, Vi or BSNL.
- A search result referred to Airtel unlimited benefits being for "personal and non-commercial use". I could not trace that to a current Airtel page. If it holds, a commercial-use question arises for business users, so check current terms.
- **5-minute test:** on a prepaid SIM with a small talktime balance and no unlimited pack (or an unlimited pack with a usage screen), note the balance, take a 3-minute forwarded call, then check the balance and the operator's usage record. Repeat for each operator.

## 4. Signalling: what the SIP side sees

### Takeaway
Nothing public documents what Indian interconnects deliver to a Plivo trunk on a forwarded call. Plivo's and Twilio's docs both call forwarding information carrier-dependent. Build on one dedicated forward-to DID per user unless a capture proves the forwarding party arrives reliably.

### Cited Findings
- **Plivo Voice API.** `ForwardedFrom` = "Original number (if call was forwarded). Carrier-dependent." `X-PH-<HeaderName>` carries custom SIP headers for SIP calls. Docs do not state a direction. **[OFFICIAL for Plivo]** — [Plivo XML overview](https://docs.plivo.com/docs/voice/xml/overview.md)
- **Plivo Zentrunk.** I found no documentation on Diversion or History-Info passthrough. A Plivo blog lists custom header pass-through as something to verify with any provider. — [Plivo blog](https://www.plivo.com/blog/sip-trunking-for-voice-ai-agents/). Inbound setup is a number attached to an inbound trunk. — [Plivo trunks API](https://www.plivo.com/docs/sip-trunking/api/trunks)
- **Twilio.** `ForwardedFrom`: "set only when Twilio receives a forwarded call", "its value depends on the caller's carrier", "Not all carriers support passing this information." **[OFFICIAL for Twilio]** — [Twilio TwiML docs](https://www.twilio.com/docs/voice/twiml)
- **Exotel.**
  - Its 2026 forwarding guide says the caller's number is available as `{contact_uri}` in the bot, without saying how it is obtained. **[VENDOR]** — [Exotel](https://exotel.com/build/set-up-a-voice-agent-for-your-phone-number-in-an-afternoon/)
  - Its forwarding doc concerns calls forwarded OUT of Exotel (the agent sees the ExoPhone as caller ID), so it does not apply here. — [Exotel docs](https://developer.exotel.com/docs/call-support/call-features/call-forwarding)
- **Standards.**
  - RFC 5806 defines Diversion (who the call was diverted from and why). — [RFC 5806](https://datatracker.ietf.org/doc/html/rfc5806)
  - In IMS, TS 24.604 CDIV counts diversions from History-Info entries, mapping busy to cause 486 and no-reply/unconditional to 480 (per a change request). — [24.604 CR drafts](https://3gpp.org/ftp/tsg_ct/WG1_mm-cc-sm_ex-CN1/TSGC1_128e/Inbox/drafts/C1-210769-24.604-g00-editorial-rev1.docx); [spec page](https://3gpp.org/dynareport/24604.htm)
- **Diversion can be lost on the platform side.** A European user forwarding an Orange mobile through a SIP provider says the provider sends Diversion, but Retell's webhook shows only `x-*` headers. He needs it to identify which agent forwarded. Unanswered at time of reading. **[ANECDOTE]** — [Retell community](https://community.retellai.com/t/inbound-sip-diversion-header-not-visible-in-webhook-custom-sip-headers/3307). Other carriers do send it: — [DIDWW docs](https://doc.didww.com/voice/outbound-trunks/routing-dialing/call-forwarding.md)
- **hiRobin's public guide lists one target number per OS**, not one per user. **[VENDOR]** — [hiRobin](https://www.hirobin.ai/blog/call-forwarding-failures-india)

### Inferences
- hiRobin's single published number per OS suggests the forwarding party can be recovered on its Indian route. That is my inference. It may also be a simplification of its docs or a number pool, or it could match by some other means. It is the strongest hint available and is not proof.
- Dedicated DID per user is the only identification scheme that does not depend on interconnect behaviour. It costs one rented DID per user. A shared DID pool becomes viable only if Diversion, History-Info or `ForwardedFrom` proves reliable across all four operators.
- Original caller CLI is probably preserved (Exotel exposes a caller number), but Edesy's "Network decides" shows no one states this for Indian networks. It must be measured. — [Edesy](https://edesy.in/blog/forward-business-number-to-multiple-phones-india-2026.md)

### Gaps
- Actual INVITE contents on a Plivo India DID from each operator's forward: From, P-Asserted-Identity, Diversion, History-Info. I found no public capture.
- Whether the trunk or an Indian gateway removes these headers; no source covers Indian ISUP/SIP interworking.
- **5-minute test (settles this):** take a spare Plivo DID not bound to a live agent and route it to a capture point (`sngrep` or `sofia global siptrace on` on the FreeSWITCH host). From a Jio, Airtel, Vi and BSNL SIM set unconditional and then busy forwarding to the DID, and call each SIM from a known third number. Compare From, PAI, Diversion and History-Info. Run it on a spare number so no caller reaches a live agent during the test.

## 5. Conditional-forward timing and behaviour

### Takeaway
The no-answer timer is set in 5-second steps from 5 to 30 s, with the user's phone ringing during it, so a person can always answer first. A declined call generally follows the busy rule, which gives users a "send to the AI" gesture. Behaviour with call waiting and dual-SIM is the least documented part and needs testing.

### Cited Findings
- **Timer range.** 5–30 s in steps of 5; the operator sets the default. An extended `**61*number**20#` form is "not published by any operator". **[VENDOR]** — [Autobot](https://getautobot.app/blog/call-forwarding-codes). hiRobin offers 5, 10, 15, 20, 25 or 30 s. **[VENDOR]** — [hiRobin](https://www.hirobin.ai/blog/call-forwarding-failures-india). A 2022 guide gives `**61*number*seconds#` for Airtel, in multiples of 5. **[PRESS] STALE?** — [TelecomTalk](https://telecomtalk.info/how-to-start-and-stop-call-forwarding/493353/)
- **When the timer starts.** In IMS CDIV the no-reply timer starts on the first 180 Ringing; default duration is a provider option, and the user may change it as a network-provider option. **[OFFICIAL]** — [TS 24.604 text via iTecSpec](https://www.itecspec.com/3gpp/24.604/s/4.5.2.6.1). Sample defaults: Clearwater 20 s, Metaswitch example 30 s (not Indian networks). — [Clearwater](https://clearwater.readthedocs.io/en/latest/Clearwater_Call_Diversion_Support.html); [Metaswitch](https://docs.rhino.metaswitch.com/ocdoc/go/xref/sentinel-volte-documentation/4.1/sentinel-volte-administration-guide/mmtelcdiv)
- **Decline goes to busy.** "Networks usually report a rejected call as busy", so declined calls follow the busy rule and unanswered calls the no-reply rule. **[VENDOR]** — [hiRobin](https://www.hirobin.ai/blog/call-forwarding-failures-india)
- **What the caller hears.** On "most other operators" the caller may hear "Please wait your call has been Forwarded"; Vodafone forwards at switch level without it. **[VENDOR, Mar 2023] STALE?** — [Exotel support](https://support.exotel.com/support/solutions/articles/135841-i-have-an-existing-phone-number-how-can-i-use-it-with-exotel-)
- **Removing that tone.** Offered for Airtel and Vodafone customer-facing numbers only. Number must be corporate-owned and in the same region as the ExoPhone. Airtel also requires postpaid plus a signed letter on company letterhead. **[VENDOR, Mar 2023] STALE?** — [Exotel support](https://support.exotel.com/support/solutions/articles/3000062877-requirements-for-call-forwarding-from-your-customer-facing-number-to-exophone-)
- **Dual-SIM.** The forwarding command must reach the correct SIM's network; Android handles SIM-to-radio assignment inconsistently. On Jio dual-SIM phones a code can show success while the IMS core never records the rule. **[VENDOR, no independent source]** — [hiRobin](https://www.hirobin.ai/blog/call-forwarding-failures-india)
- **Not-reachable activation.** Airtel, Jio and Vi all offer this type; a phone that is off or out of coverage triggers it. — [TelecomTalk](https://telecomtalk.info/how-to-start-and-stop-call-forwarding/493353/)

### Inferences
- The forwarding tone is a notable product issue: a personal-number user's callers may be told their call was forwarded before the AI says anything. Consumer accounts appear not to qualify for Exotel-style removal. Whether it still plays in 2026 per operator is unknown.
- Timing guidance: 15–20 s gives a person a real chance to answer while keeping callers from hanging up. This is my judgment, with no Indian abandonment data behind it.

### Gaps
- **Busy-forward with VoLTE call waiting on:** I found no Indian source. My unverified understanding of GSM behaviour is that call waiting takes precedence, so the second caller gets a waiting tone and the busy rule does not fire unless the waiting call is rejected or times out. **5-minute test:** with call waiting on, then off, put the SIM on a call and ring it from a third phone.
- Whether an active call on SIM 1 makes SIM 2 "not reachable" or "busy" on dual-SIM dual-standby phones. **Test:** same as above, with the call on the other SIM.
- Whether the timer really applies per operator. **Test:** set 5 s and then 30 s, time from first ring to answer at the DID, per operator.
- Listen for the forwarding tone on each operator during the section 4 test, and record it.

## 6. Prior art in India and abroad

### Takeaway
This is already a commercial pattern in India. hiRobin, Autobot, CatchMyCall, Truecaller Assistant and Exotel's self-serve guide all rely on, or appear to rely on, conditional forwarding, typically to one assistant number with an app-driven setup and verification. hiRobin's own telemetry is the only reliability figure, and it is unflattering.

### Cited Findings
- **hiRobin (India).** Registers busy, no-reply and unreachable rules by MMI, users do not type them, and never uses unconditional. A combined code sets all three. Verification is by `*#67#`, `*#61#`, `*#62#`, `*#002#`. It says it monitors whether calls arrive and re-registers rules on drops. **[VENDOR]** — [hiRobin](https://www.hirobin.ai/blog/call-forwarding-failures-india)
- **hiRobin's failure figures.** Airtel 42.6%, Jio 30.7%, Vi 30.7%, BSNL 30.4%: declined or missed calls on confirmed-forwarding numbers that never reached the destination. Self-reported telemetry; "They aren't carrier-published figures." No sample size or window. Dates on the page conflict (Jul vs Sep 2026). Its meta text says "up to 1 in 3", but Airtel exceeds that. **[VENDOR]** — [hiRobin](https://www.hirobin.ai/blog/call-forwarding-failures-india)
- **Autobot (India).** Answers forwarded calls on busy, no-answer or unreachable, in Hindi, English and 9 other languages; 10-minute call limit; Business plan; India only. **[VENDOR]** — [Autobot](https://getautobot.app/blog/call-forwarding-codes)
- **CatchMyCall (India, launched 1 Oct 2025).** AI answers on busy, unreachable and no-answer rules; "integrated with telecom APIs for seamless call forwarding with sub 2 second latency"; app onboarding; 7-day trial with 50 minutes. Forwarding setup is not explained. **[PRESS release]** — [The Wire / PTI](https://m.thewire.in/article/ptiprnews/catchmycall-launches-across-india-transforming-call-management-into-a-personal-ai-assistant)
- **Truecaller Assistant (India, launched Jul 2023).** Android only at launch; English, Hindi, Hinglish; ₹149/month after 14 days. The launch article does not mention forwarding. **[PRESS] STALE?** — [GSMArena](https://www.gsmarena.com/truecaller_assistant_india_launch_price_details-news-59245.php)
- **Truecaller activation.** A later guide describes dialling an activation code per operator (Airtel, Jio, Vi/BSNL) with Wi-Fi off, a post-call success/failure screen, and redialling on failure. Search extract only, roughly early 2025; fetch was blocked. **[PRESS]** — [NewsBytes](https://www.newsbytesapp.com/news/science/truecaller-assistant-activation-steps-in-india/story). A US-era guide shows the same pattern: choose carrier, then tap to call the Assistant number. **STALE?** — [Gadgets To Use, 2023](https://gadgetstouse.com/blog/2023/02/02/truecaller-assistant-to-screen-calls/)
- **Exotel (India).** Published a self-serve 2026 guide for forwarding a personal number to a voice agent. — [Exotel, May 2026](https://exotel.com/build/set-up-a-voice-agent-for-your-phone-number-in-an-afternoon/). Exotel has long pushed customers to forward an existing number, because ExoPhones cannot be published under TRAI's 13th amendment (its wording). **STALE?** — [Exotel support, 2023](https://support.exotel.com/support/solutions/articles/135841-i-have-an-existing-phone-number-how-can-i-use-it-with-exotel-)
- **Reliance Jio Phone Call AI** (Aug 2024) is a recording, transcription and translation feature, per its AGM coverage; I did not find a forwarding link. **STALE?** — [Business Today](https://www.businesstoday.in/amp/technology/news/story/reliance-agm-2024-jio-launches-phone-call-ai-a-one-stop-solution-for-recording-transcribing-translating-calls-443514-2024-08-29)
- **CallHippo** bills two legs when forwarding FROM its number to a mobile, which is the reverse direction. — [CallHippo, Nov 2024](https://callhippo.ladesk.com/396279-What-is-call-forwarding-and-what-are-the-charges-associated)

### Inferences
- The strongest competitor pattern is conditional-only, app-driven setup, read-back verification, plus ongoing monitoring and re-registration. Nobody serious here uses unconditional forwarding as the primary mode.
- Read hiRobin's 30–43% carefully. It is not a clean per-call failure rate, and a vendor selling monitoring benefits from a large number. It still indicates that drift and misses are a real, routine issue that needs monitoring.

### Gaps
- I could not cover Truecaller help pages, Reddit, Play Store reviews, MyOperator, Knowlarity, Sarvam, Bolna, Google Voice, Hiya, Goodcall, Rosie or Dialpad personal-number flows. I found no user-complaint corpus, and I will not characterise the reported reliability of Truecaller Assistant in India.
- Whether Truecaller's Assistant is still sold in India on the same terms in 2026; no 2026 source was found.

## 7. Failure modes

### Takeaway
The main risk is silent loss of the rule combined with a user who never notices. Plan for scheduled verification and a test-call health check, plus handling of OTP calls and the scam-lookalike problem. Several requested failure modes have no evidence either way.

### Cited Findings
- **Silent decay.** Rules can decay after re-registration, roaming, a SIM re-seat or a carrier config refresh. Airtel and Vi run both circuit-switched and VoLTE paths, and a rule set on one may not be honoured on the other. **[VENDOR, uncited technical claims]** — [hiRobin](https://www.hirobin.ai/blog/call-forwarding-failures-india)
- **Handset- or SIM-specific refusal.** A Jio SIM refused forwarding on a Galaxy S24 Ultra while Airtel and Vi worked; putting the SIM in another phone to set it up then fixed it. Search extract; dual-SIM results vary by device. **[ANECDOTE, Dec 2024 thread]** — [Samsung community](https://r2.community.samsung.com/t5/Galaxy-S/All-calls-forward-not-working-in-S24U/m-p/17820546/highlight/true)
- **Billing state blocks activation.** Number must have no outstanding balance; if activation fails, forward to a different operator's number and back. **STALE?** — [Exotel support, 2023](https://support.exotel.com/support/solutions/articles/3000062877-requirements-for-call-forwarding-from-your-customer-facing-number-to-exophone-). Jio requires an active plan. **[OFFICIAL]** — [Jio help](https://www.jio.com/help/faq/mobile/services/hd-voice/how-can-i-activate-call-forwarding-service/)
- **OTP interception.** Forwarded numbers can send OTP and 2FA voice calls to the target, the mechanism scammers exploit. **[ANECDOTE, warning]** — [LinkedIn post](https://www.linkedin.com/posts/surender-singh-a28711132_cybersecurity-fraudalert-onlinesafety-activity-7374109097681600512-U60M); [Tribune](https://www.tribuneindia.com/news/delhi/new-cyber-scam-trick-asks-you-to-dial-a-code-beware/amp)
- **Roaming.** On Airtel abroad both legs are charged. **[VENDOR summary]** — [Autobot](https://getautobot.app/blog/call-forwarding-codes)
- **Destination blocks.** DND or incoming restrictions on the destination can block forwarded calls. **[VENDOR, low quality]** — [Qoli, Jul 2025](https://www.qoli.ai/blogs/jio-call-forwarding-code)
- **Loops.** IMS AS counts diversions from History-Info, which is the standards-level loop limiter. — [24.604 CR draft](https://3gpp.org/ftp/tsg_ct/WG1_mm-cc-sm_ex-CN1/TSGC1_128e/Inbox/drafts/C1-210769-24.604-g00-editorial-rev1.docx)
- **Plivo side.** TRAI media anchoring fails with hangup code 4590 if the platform endpoint is outside India. **[VENDOR]** — [Plivo India guide](https://www.plivo.com/docs/voice-agents/sip-trunking/deploy/calling-in-india.md). A Vapi support post and an Edesy article claim domestic SIP trunking or VoIP-to-PSTN is restricted, while Plivo presents India trunking as supported. **Conflict, unresolved.** — [Vapi support](https://support.vapi.ai/t/23095467/plivo-sip-india); [Edesy, 2026](https://edesy.in/blog/sip-trunk-providers-india-ai-calling-2026.md)

### Inferences
- **Monitoring.** Run a read-back or a synthetic test call to each user's number on a schedule, and treat "agent has received no forwarded calls in N days" as a warning. A silently dead rule looks identical to a quiet phone.
- **Onboarding trust.** Instructions to "dial this code" look exactly like the fraud DoT warns about. Prefer the operator app or Settings where possible, show the number being set, and prominently explain how to turn it off (`##002#`, or `*413` on Jio).
- **Agent behaviour.** An AI answering a bank's OTP call should not read out or log the code. Conditional-only forwarding reduces but does not remove this exposure.
- **Offboarding.** A user who cancels a subscription and leaves the rule set would have callers reach a dead or reassigned DID. Keep a DID reserved through a grace period with an explanatory message.

### Gaps
No evidence was found on the following, and I will not infer an answer:
- Whether forwarding rules survive a mobile number port. **Test:** ask any user who ported, or check the status after a port.
- Whether device reset alone affects network-stored rules. **Test:** factory-reset a test phone, then read back. The rule should be network-side per the hiRobin description, but this is unverified.
- Rule behaviour during national roaming.
- Carrier spam tagging of a forwarded CLI, or Truecaller labelling on forwarded calls; nothing found.
- Whether callers are ever billed long-distance for a forwarded call; no Indian source found.
- Interaction with CNAP (live from 1 Nov 2026 per [ThePrint](https://theprint.in/india/no-more-unknown-callers-govt-makes-caller-name-display-mandatory-from-1-november/3072774/)): no source covers which name appears on a forwarded call.
- A forwarding loop in practice between a user's SIM and a DID that dials back out. **Test:** set a loop on test SIMs only, and watch the FreeSWITCH log for repeated INVITEs.
