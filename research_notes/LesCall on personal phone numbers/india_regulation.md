# India regulation: AI voice-agent SaaS attaching to users' personal mobile numbers (as of 2026-11-03)

Research for a founder, not legal advice. Evidence labels used below:
- **[PRIMARY]**: I read the instrument's own text (some TRAI PDFs are OCR scans, so quotes may carry small OCR repairs).
- **[FIRM]**: law-firm commentary, read directly.
- **[VENDOR]**: vendor docs. These show that vendor's practice, not the law.
- **[DIGEST]**: seen only as a search-result summary. The page or PDF was not opened, or it refused the fetch. Confirm before relying on it.

## What is the verdict for each method?

### Takeaway
The biggest constraint is not the attachment method. Since the February 2025 TCCCPR amendment, a business's commercial call from an ordinary 10-digit number is non-compliant whichever way it is connected. Inbound attachment by forwarding is the only personal-number method where I found no prohibition, and it is the lowest-risk option. No instrument I read expressly blesses it either.

| # | Method | Verdict (late 2026) | Main clause | Confidence |
|---|---|---|---|---|
| 1 | User forwards own number to a LesCall DID (inbound) | **Legal-leaning.** No prohibition found, no express permission found | None prohibits it. DoT's *401# USSD suspension affects how it is switched on. DPDP applies to callers | Medium; absence of a rule is inference |
| 2 | Outbound over Plivo showing a personal mobile as CLI | **Prohibited in practice; treat as prohibited in law** | Plivo docs; Telecom Act s.42(3)(b)/(c) read with s.3(8); TCCCPR reg 3 if commercial | High on Plivo; medium on the statutory characterisation |
| 3a | Multi-SIM gateway / SIM box run by LesCall or for customers | **Prohibited** | Act s.42(1), s.42(3)(d), s.3(1); TCCCPR Sch. IV "SIM Farm/SIM box" | High |
| 3b | One user's single-SIM gateway | **Grey.** Outbound commercial use is prohibited under TCCCPR | s.3(1)(c), s.42(1), s.42(3)(d) threshold not located; Third Schedule items 1, 2 | Low; no ruling or instrument draws this line |
| 3c | Bluetooth bridge to the user's own phone | **Grey.** Same outbound limit | No instrument addresses it | Low |
| 4 | App on the user's phone bridging the cellular call to VoIP | **Grey inbound.** Outbound commercial use is prohibited under TCCCPR | TCCCPR regs 2(ea), 3, 4; s.42(2)(b) if recording or tapping the user's own calls | Low |
| 5 | Port the mobile number to a SIP service | **No legal route found via Plivo or LesCall.** A licensed operator's own internet-telephony offer is the only opening | MNP regs (between access providers); DoT 2018 internet-telephony amendment | Medium |
| 6 | WhatsApp Business Calling API | **Legal-leaning.** Regulatory position thin; the platform's rules decide it | SIM-binding Direction 28 Nov 2025; TCCCPR scope untested | Low to medium |
| All | **Any commercial call from a personal 10-digit number** | **Prohibited** | TCCCPR reg 3(1)-(2), reg 2(bw) first proviso, reg 25 | High for businesses; personal use is open |

### Cited Findings
- Plivo says an India caller ID "Must use Plivo-rented Indian phone number" — [Plivo India calling](https://www.plivo.com/docs/voice/concepts/india-calling.md) [VENDOR]
- Exotel says "Caller ID masking is illegal in India and we won't be able to do that" — [Exotel outbound FAQ](https://support.exotel.com/support/solutions/articles/3000018241-everything-you-need-to-know-about-outbound-calls-on-exotel) [VENDOR]

### Inferences
- Product shape that fits the rules: inbound on the user's real number via forwarding; outbound from a rented, KYC'd, declared number in the correct series, with the user's personal number appearing only in the agent's spoken introduction.

### Gaps
- The verdicts on methods 1, 3b, 3c and 4 rest on not finding a rule. Counsel should test them.

## Method 1: user forwards their own number to a LesCall DID (inbound)

### Takeaway
I found no instrument prohibiting a subscriber from forwarding their own line to a business DID. Three real constraints apply: how forwarding is activated, the operator's plan terms, and data-protection duties to callers. TCCCPR is aimed at senders, so it should not bite on calls the user receives.

### Cited Findings
- DoT ordered USSD-based unconditional forwarding (*401#) discontinued "with effect from 15.04.2024 till further notice", with existing users re-activating by other modes, because of a fraud pattern — [Fone Arena](https://www.fonearena.com/blog/420565/dot-suspends-ussd-call-forwarding.html/amp); [DoT PDF](https://dot.gov.in/sites/default/files/28032024%20ussd.pdf) (the DoT link returned 404 to me) [DIGEST]
- Plivo inbound scope is "India to India", both call legs must stay in India, and violations fail with `violates_media_anchoring` — [Plivo India calling](https://www.plivo.com/docs/voice/concepts/india-calling.md) [VENDOR]
- Plivo: "A platform deployment in another region cannot legally carry the call"; hangup 4590 means the platform endpoint is not in India. The docs attribute this to TRAI without citing a clause — [Plivo calling in India](https://www.plivo.com/docs/voice-agents/sip-trunking/deploy/calling-in-india.md) [VENDOR]
- Only India-registered businesses can rent numbers; KYC is GST, COI or Udyam and "Business PAN alone is not accepted"; a Reseller needs a separate application per customer — [Plivo rent India numbers](https://www.plivo.com/docs/numbers/rent-india-numbers.md) [VENDOR]
- Jio plan terms reportedly limit plans to personal use and reserve the right to discontinue free voice benefits for "unauthorised telemarketing and commercial use". Airtel offers are reportedly for "personal and non-commercial use" (from a report on unlimited 5G) — [Trak.in](https://trak.in/stories/airtels-unlimited-5g-usage-is-limited-to-300gb-in-30-days-no-commercial-data-usage-allowed/); [TechPP](https://techpp.com/2017/10/13/reliance-jio-terms-conditions-opinion/) [DIGEST; the TechPP piece is from 2017]

### Inferences
- Inbound forwarding creates no outbound "Sender" event for the forwarded leg, so reg 3 should not reach it. I found no text saying so expressly.
- The onboarding flow should not rely on *401#, and must not resemble the scam ("dial this code") that caused the order. Have the user set forwarding in the operator's own app or phone settings, and keep proof it was the subscriber's act.
- The owner should confirm `lescall-runtime` media is in India, because Plivo fails the call otherwise.
- Forwarded minutes may be billed to the user, and heavy forwarding on a personal plan is a contract risk, not an offence.

### Gaps
- Whether the *401# suspension is still in force today, and whether it reaches MMI forwarding codes such as **21*. Coverage found is from 2024.
- Whether any operator bars forwarding to a commercial DID specifically. I could not read a current Customer Application Form or full T&Cs.

## Method 2: outbound SIP with a personal mobile as CLI over a cloud trunk

### Takeaway
Plivo will not do it and states the reason as Indian regulation. By statute, the strongest clauses are s.42(3)(b) and (c), with up to 3 years, up to Rs 50 lakh, cognizable and non-bailable. Whether presenting *your own* subscriber's number, with their consent, counts as "tampering" is untested, and I found no definition of "tamper" in the Act.

### Cited Findings
- Verified Caller ID: "**India:** Not applicable. Indian regulations require using Plivo-rented Indian numbers as caller ID." — [Plivo verified caller ID](https://www.plivo.com/docs/voice/concepts/verified-caller-id.md) [VENDOR]
- Calls from an India number must originate from a number rented on your Plivo account — [Plivo rent India numbers](https://www.plivo.com/docs/numbers/rent-india-numbers.md) [VENDOR]
- Non-India businesses "display a US or international Caller ID — not an Indian number" — [Plivo India calling](https://www.plivo.com/docs/voice/concepts/india-calling.md) [VENDOR]
- Act s.42(3): whoever "(b) uses telecommunication identifiers not allotted or permitted in accordance with sub-sections (8) and (9) of section 3; (c) tampers with telecommunication identifiers" is punishable with up to 3 years, up to Rs 50 lakh, or both. s.3(8): the Government may "allot telecommunication identifiers for use by authorised entities". s.42(7): cognizable and non-bailable — [Telecommunications Act 2023](https://www.trai.gov.in/sites/default/files/2024-09/Telecommunications_01012024.pdf) [PRIMARY]
- Section 42 has been in force since 26 June 2024 — [Verdictum](https://www.verdictum.in/news/central-government-announces-26th-june-2024-as-implementation-date-for-key-provisions-of-the-telecommunications-act-2023-1541136) [DIGEST]
- Telecom Cyber Security Rules 2024 r.4(2): no person shall endanger telecom cyber security by misuse of a telecommunication identifier "by (a) fraud, cheating or personation" — [Rules 2024](https://www.thc.nic.in/Central%20Governmental%20Rules/Telecommunications%20(Telecom%20Cyber%20Security)%20Rules,%202024.pdf) [PRIMARY]
- DoT launched the International Incoming Spoofed Calls Prevention System on 22 Oct 2024, for calls from abroad that appear to originate in India. Blocked calls were reported at about 1.35 crore per day at launch, down about 90% in two months. In January 2025 DoT asked TSPs to block carriers that repeatedly send spoofed traffic, with 20+ blocked — [MediaNama Oct 2024](https://www.medianama.com/2024/10/223-indian-spoofed-calls-prevention-system-blocks-1-35-crore-calls-amidst-false-positives-concerns/); [MediaNama Dec 2024](https://www.medianama.com/2024/12/223-dot-blocked-90-percent-spoofed-calls-two-months/); [Madhyamam](https://madhyamamonline.com/amp/india/centre-cracks-down-on-spoofed-international-calls-20-carriers-blocked-1373304) [DIGEST]
- DoT amended licences on CLI spoofing effective 1 Aug 2022, including ILD operators dropping calls with no CLI and using E.164 to identify improper CLI — [Tele.net](https://tele.net.in/?p=152001); [DoT PDF](https://dotws.cdot.in/sites/default/files/06072022%20ILD%20%20CLI%20spoofing_0001.pdf) (the DoT link refused connection) [DIGEST]
- CNAP: a DoT circular dated 13 Oct 2026 tells access providers to implement it from 1 Nov 2026 on 4G and newer, on by default, showing the KYC name. Licence Amendment 01/Licensing Policy/2026 widens the CLI definition to include the registered name. CLIR subscribers' names are not shown. Businesses can reportedly apply for verified preferred names — [Telangana Today](https://telanganatoday.com/mobile-users-to-see-callers-names-under-new-cnap-framework-from-november-1); [Legal Suvidha](https://legalsuvidha.com/latest-updates/dot-broadens-cli-definition-in-unified-license-161026); [Inc42](https://inc42.com/buzz/centre-directs-telcos-to-display-caller-names-from-november-1/) [DIGEST]

### Inferences
- The spoofed-call system targets calls entering from abroad, so a domestic route is not its target. But a +91 mobile CLI from any foreign egress is exactly what it blocks. A foreign-hosted setup is a dead end for two independent reasons.
- CNAP shows the name tied to the number's KYC. On a rented number the recipient may therefore see LesCall or the tenant's name, depending on who holds the KYC record. This is worth checking with Plivo before a tenant's calls go out.
- A user's written consent helps on intent and fraud. It does not solve s.3(8)'s "allotted" language, or the operator's refusal.

### Gaps
- I did not find the clause text of any Indian rule on +91 mobile CLI on SIP or PRI, or an intra-LSA "SIP-to-PSTN" rule. Plivo's statements are the best evidence I found. No clause for "CLI must be a Plivo-owned number" was located.
- No Zentrunk-specific India page was found. Plivo's current SIP docs do not state CLI rules on the trunking page.
- Whether Airtel, Jio or Tata validate CLI against the trunk's number block on domestic SIP or PRI traffic: no source.

## Method 3: GSM gateway / SIM box, single-SIM gateway, Bluetooth, and PSTN-VoIP mixing

### Takeaway
A commercial multi-SIM gateway is the clearest "no" in this report: it is unauthorised network or service provision plus a named detection target. The law has a numeric threshold for multi-SIM equipment, which suggests a single-SIM device may fall outside clause (d). That does not make it safe, because s.3(1) and s.42(1) still apply if LesCall is effectively providing a service through it.

### Cited Findings
- s.3(1): anyone intending to provide telecommunication services, establish or operate a network, or "(c) possess radio equipment" needs authorisation. s.2(t): "telecommunication service" means "any service for telecommunication" — [Act](https://www.trai.gov.in/sites/default/files/2024-09/Telecommunications_01012024.pdf) [PRIMARY]
- s.42(1): providing services or establishing a network without authorisation carries up to 3 years, up to Rs 2 crore, or both. This resolves the Rs 50 lakh versus Rs 2 crore conflict in secondary sources: both exist, for different subsections — [Act](https://www.trai.gov.in/sites/default/files/2024-09/Telecommunications_01012024.pdf) [PRIMARY]
- s.42(3)(d): possessing radio equipment without authorisation or exemption "that can accommodate more than specified number of subscriber identity modules". s.42(3)(f): wilfully possessing radio equipment knowing it uses unauthorised or tampered identifiers. s.42(6): abetment, attempt and conspiracy — [Act](https://www.trai.gov.in/sites/default/files/2024-09/Telecommunications_01012024.pdf) [PRIMARY]
- Third Schedule: item 1(a) possessing radio equipment without authorisation (other than s.42(3)(d)/(f) offences) and 1(b) SIMs beyond the notified number carry up to Rs 50,000 first, up to Rs 2 lakh each subsequent. Item 2: **using** a service or network "knowing or having reason to believe" it lacks authorisation carries up to Rs 10 lakh. Rows are matched by reading order in a two-column PDF extraction, so check against the printed table — [Act](https://www.trai.gov.in/sites/default/files/2024-09/Telecommunications_01012024.pdf) [PRIMARY]
- s.3(1) and s.3(6) were brought into force on 23 June 2026 by S.O. 3368(E) — [Simpliance](https://simpliance.in/India/EHS/govt_notification/Central/notification-appointing-23-june-2026-as-the-commencement-date-for-sections-31-and-36-of-the-telecommunications-act-2023-14815) [DIGEST]
- TCCCPR Sch. IV as amended requires operators to deploy "methods to detect the misuse of robotic calls, auto dialer calls or pre-recorded announcements, SIM Farm/SIM box type usage", and flags "all mobile numbers (MSISDN) associated with a device on which 4 or more mobile numbers... have been used within a month" — [Regulation 12 Feb 2025](https://www.trai.gov.in/sites/default/files/2025-02/Regulation_12022025.pdf) [PRIMARY]
- DoT advisory lists "SIM Box" among radio equipment for the s.42(3)(f) offence — [Sanchar Saathi PDF](https://www.sancharsaathi.gov.in/SancharSaathiDocuments/ImportantDocuments/Department%20of%20Telecommunications%20Cautions%20Citizens%20about%20Consequences%20of%20IMEI%20Tampering%20and%20Telecom%20Identifier%20Misuse.pdf); [Morung Express](https://morungexpress.com/govt-reiterates-consequences-of-imei-tampering-and-telecom-identifier-misuse) (the DoT PDF returned 403) [DIGEST]
- 2025 Amendment Rules (22 Oct 2025): TIUEs can be ordered to suspend identifiers (r.5), r.7A creates the MNV platform, r.8(4A) and a tampered-IMEI database apply to devices — [AZB](https://www.azbpartners.com/bank/telecommunications-telecom-cyber-security-amendment-rules-2025-notified/) [FIRM]
- OSP 2021: toll bypass "means transmission of PSTN calls, be it between India and foreign country or between two cities in India, using own network of an OSP". A centralised media gateway is allowed for receiving incoming PSTN calls but "will not be permitted for OSPs which make outgoing PSTN calls". The guidelines are "silent on the use of cloud based/hosted contact centre services by entities which are not TSPs", and non-TSP EPABX providers "will have to either tie up with a TSP or become a TSP" — [Khaitan ERGO 25 Jun 2021](https://khaitanco.com/sites/default/files/2021-06/Ergo_25062021_0.pdf) [FIRM]
- The OSP guidelines cover voice BPO over PSTN/PLMN/ISDN "but not data-based entities that facilitate calls via voice over internet protocols" — [BTG Advaya](https://www.legal500.com/intelligence/india/media-telecoms-it-entertainment/dot-revised-other-service-provider-osp-guidelines) [FIRM]

### Inferences
- Single-SIM gateway owned and used by the user, inbound only: exposure is mainly radio-equipment authorisation (a consumer handset is normal, a gateway box is less clearly so) and contract. If LesCall ships, runs or remotely manages the box, s.42(1) "provides telecommunication services" and s.42(6) abetment become realistic theories.
- Third Schedule item 2 means a *customer* of an unauthorised gateway setup is exposed too. That makes this a bad thing to sell.
- Bluetooth bridge: the phone stays the only radio device and no second SIM device is involved, so clause (d) looks inapplicable. The remaining questions are the same service-provision, TCCCPR and interception ones. **This is reasoning only; no source addresses it.**
- PSTN ↔ AI over a licensed trunk, which never returns to PSTN, is the ordinary Plivo pattern. Patching a PSTN inbound leg to a PSTN outbound leg through LesCall (human handoff) is the configuration closest to the toll-bypass concern, and counsel should review it.

### Gaps
- The "specified number" of SIMs under s.42(3)(d): I did not find the notification. This is the single most decision-relevant missing number for 3b.
- No 2022 OSP relaxation surfaced. I found only the 23 June 2021 guidelines, and I did not read DoT's own text.
- Whether LesCall is an OSP. An AI agent doing voice-BPO work is not addressed.
- No Indian ruling on a single-SIM gateway or personal Bluetooth bridge. No 2026 prosecution of single-SIM forwarding was found. Enforcement reports concern fraud operations.

## Method 4: device app bridging the cellular call to VoIP

### Takeaway
No instrument addresses this. Inbound-only on the user's own handset looks closer to Method 1 than to a SIM box, but it adds a live-call interception question. Outbound through the handset is what TRAI's detection regime is written to catch.

### Cited Findings
- A2P call: "a voice call that is initiated by an application, software system, or automated platform without direct human dialling and delivered to an individual telecom subscriber; including using autodialling, robo-calls and/or pre-recorded/artificial voice technologies" (reg 2(ea)) — [Regulation 18 Sep 2026](https://www.trai.gov.in/sites/default/files/2026-09/Regulation_18092026.pdf) [PRIMARY]
- TRAI's memorandum says bulk callers "take advantage of cheaper routes meant for normal person-to-person (P2P) calls", and that networks "may not be able to differentiate" human from artificial voice, so it relies on declaration and investigation — [Regulation 18 Sep 2026, Explanatory Memorandum](https://www.trai.gov.in/sites/default/files/2026-09/Regulation_18092026.pdf) [PRIMARY]
- UCC_Detect signals are call volume, recipient diversity, short average duration and a low incoming-to-outgoing ratio. Honeypots are mandated, at least 10 per LSA — [Regulation 12 Feb 2025](https://www.trai.gov.in/sites/default/files/2025-02/Regulation_12022025.pdf) [PRIMARY]
- s.42(2)(b): "intercepts a message unlawfully" carries up to 3 years, up to Rs 2 crore — [Act](https://www.trai.gov.in/sites/default/files/2024-09/Telecommunications_01012024.pdf) [PRIMARY]
- SIM-binding Directions (28 Nov 2025) require covered communication apps to stay linked to the SIM in the device. Reported legal basis is the Cyber Security Rules, and JSA's commentary says it relied on secondary sources — [IBA / JSA](https://prod-bo.ibanet.org/India-Sim-binding-mandate-recalibrating-digital-identity) [FIRM]

### Inferences
- A SIM that stays in the user's own phone is the point in this method's favour on SIM-box and SIM-binding logic.
- An app dialling from a personal number on the agent's instruction is A2P by plain reading. It would exhibit the listed detection signals if used for many recipients.
- The AI answering as the called party is different from an app capturing a live conversation between the user and a third party. The second is the riskier mode for s.42(2)(b) and privacy.

### Gaps
- No source addresses an app bridge. Android and iOS technical or store limits were not researched here (out of regulatory scope).
- Whether LesCall's app would be a designated communication app under the SIM-binding Directions is unknown. The Direction's text was not obtained.

## Method 5: number porting, VNO and licensing, and who the regulated party is

### Takeaway
MNP moves a number between licensed access providers. Plivo's India inventory is landline, 140 and 160 series, not mobile numbers. So a user cannot port a personal mobile to Plivo or LesCall on anything I found. The one opening is that an access licensee may lawfully offer the same mobile number over internet telephony, so a telco partnership is the only route.

### Cited Findings
- TRAI's Mobile Number Portability Regulations 2009 and amendments (7th 2018; 9th, effective 1 July 2024, targeting SIM-swap fraud) govern moving numbers between providers; "Access Provider" includes fixed providers in its definition — [TRAI PR 68/2009](https://trai.gov.in/sites/default/files/pr23sep09no68.pdf); [Tele.net](https://tele.net.in/trai-amends-mnp-rules-to-make-the-portability-process-faster-and-simpler/) [DIGEST]
- Plivo number types: landline series (022, 080…) for non-BFSI "Service and transactional calls"; 140 "Promotional voice calls only"; 160 for BFSI — [Plivo rent India numbers](https://www.plivo.com/docs/numbers/rent-india-numbers.md) [VENDOR]
- DoT's June 2018 amendment: "the mobile numbering series should be used for providing internet telephony by a licensee", "The same number may be allocated for cellular mobile service as well as internet telephony service", and it is "an un-tethered service from the underlying access network" — [AZB](https://www.azbpartners.com/bank/clarification-and-amendments-regarding-internet-telephony/) [FIRM]
- Telecommunications (Authorisation for Provision of Principal Telecommunication Services) Rules 2026, notified 24 June 2026 per Khaitan (another source says 23 June, G.S.R. 513(E)): five categories (unified, access, wireline access, internet, long distance); NSO or VNO routes; a wireline access authorisation only for VNOs, whose scope includes "wireline voice and non-voice services, internet and internet telephony"; a VNO may contract with several parent NSOs, except wireless access (one parent) — [Khaitan ERGO 26 Jun 2026](https://www.khaitanco.com/sites/default/files/2026-06/Ergo_Principal%20Telecom%20Authorisation%20Rules_26062026.pdf) [FIRM]
- The same Rules bar telecom network data from being "routed, shared or made available outside India", and carry LIM (lawful interception and monitoring) obligations — [Khaitan ERGO 26 Jun 2026](https://www.khaitanco.com/sites/default/files/2026-06/Ergo_Principal%20Telecom%20Authorisation%20Rules_26062026.pdf) [FIRM]
- A secondary table lists VNO unified national scope at Rs 10 crore minimum equity — [AA Plus Consultants](https://www.aaplusconsultants.com/article-telecom-authorisation-rules-2026.html) [DIGEST]
- TRAI's 2026 reg 13 proviso: the NSO must give a VNO a real-time DLT interface. In the memorandum, an intermediary's CLIs used by a principal entity for UCC lead to action "against the telecom resources of the intermediary, without prejudice to any other regulatory action that may be taken against the PE" — [Regulation 18 Sep 2026](https://www.trai.gov.in/sites/default/files/2026-09/Regulation_18092026.pdf) [PRIMARY]
- TRAI's memorandum: "whole PRI/SIP links need to be barred, and not individual DID numbers" — [Regulation 18 Sep 2026](https://www.trai.gov.in/sites/default/files/2026-09/Regulation_18092026.pdf) [PRIMARY]

### Inferences
- LesCall is best understood as a telecom *customer* (a Plivo Direct Brand or Reseller) unless it provides telecom services itself. "Any service for telecommunication" is broad, so resale of numbers without Plivo's Reseller application is a real s.42(1) question.
- On multi-tenant: the intermediary and whole-link statements mean one tenant's UCC could cost **all** tenants the shared trunk. Per-tenant numbers, per-tenant KYC and a separable trunk reduce this.
- A VNO licence would make LesCall the regulated party and add LIM, data-localisation and capital burdens. It would still not, on what I found, deliver a customer's existing mobile number.

### Gaps
- Fixed-line number portability status: no source found.
- Whether Airtel, Jio or Tata permit a mobile number as a SIP DID: no source found. Enterprise pages did not address it. TRAI's numbering recommendations of 6 Feb 2025 were not read.
- Plivo India's licence status and the Indian carrier behind its numbers: not stated in its docs.
- The final CPaaS and cloud-telephony position under the 2026 Rules, and "DID resale" rules: not confirmed. The draft reportedly allowed authorised CPaaS internet telephony; I could not confirm the final text. IP-1 scope was not found.

## Method 6: WhatsApp Business Calling API

### Takeaway
It uses no telecom trunk and no CLI, so the CLI and SIM-box issues fall away. Regulation is thin rather than favourable, and I found no primary Meta or DoT statement.

### Cited Findings
- Infobip lists India for business-initiated calls, requiring prior user permission. Limits differ by source (one request per 24 h; two in 7 days) — [Infobip](https://support.infobip.com/docs/whatsapp/whatsapp-business-calling/business-initiated-calling); [360dialog](https://docs.360dialog.com/docs/waba-basics/calling-api/business-initiated-whatsapp-calls) [VENDOR, DIGEST]
- Chakra: 72 hours to call after permission; a number must be on the Cloud API — [Chakra](https://chakrahq.com/article/whatsapp-cloud-api-calling-feature-details/amp/) [VENDOR, DIGEST]
- A vendor argues TRAI's DND and DLT rules cover SMS and voice, not WhatsApp — [RichAutomate](https://richautomate.in/blog/whatsapp-dnd-trai-rules-apply-india-2026) [VENDOR, DIGEST]
- SIM-binding compliance dates conflict: 28 Feb 2026 (JSA); extended to 30 Mar 2026 and then 31 Dec 2026 (NASSCOM, whose page refused my fetch); 1 Mar 2026 (GoTrust). WhatsApp's feature was still in development in Feb 2026 — [IBA / JSA](https://prod-bo.ibanet.org/India-Sim-binding-mandate-recalibrating-digital-identity); [NASSCOM](https://community.nasscom.in/communities/cyber-security-privacy/dots-mandate-sim-binding-whats-changing); [MediaNama](https://www.medianama.com/2026/02/223-whatsapp-dot-sim-binding-under-development/) [FIRM / DIGEST]
- A vendor reports a WhatsApp policy from 15 Jan 2026 barring general-purpose AI chatbots on the Business API — [2Factor](https://2factor.in/v3/lp/blogs/WhatsApp-Business-API-rules-in-India-(2026-updated).html) [VENDOR, DIGEST, unverified]

### Inferences
- A task-specific business agent may differ from the "general-purpose" chatbot that policy reportedly bars. Meta's text decides this.
- DPDP, consent and deception concerns follow the call regardless of the pipe.
- LesCall may itself be a TIUE, since it uses mobile numbers to identify customers. That carries suspension-order readiness duties under r.5.

### Gaps
- Meta's own India terms; whether a personal number can run the Calling API alongside the consumer app; and how SIM binding treats API numbers.

## How do the telemarketing rules apply, and is a personal number used for business caught?

### Takeaway
Yes. A business's commercial call from a personal 10-digit number is prohibited, and the sanction falls on **every telecom resource of that person**, including unused SIMs and devices. From about 17 Nov 2026, undeclared A2P calls are also deemed UCC. An AI agent cannot escape by having a human join later, since TRAI rejected that carve-out. I found no personal-assistant exception.

### Cited Findings
- Reg 3(1) (2025): access providers must ensure commercial communication "takes place only using registered headers or the number resources allotted to the Senders from special series". Reg 3(2): an unregistered Sender's resources "may be put under suspension or may also be disconnected" — [Regulation 12 Feb 2025](https://www.trai.gov.in/sites/default/files/2025-02/Regulation_12022025.pdf) [PRIMARY]
- Reg 2(bw) excludes transactional and service calls from UCC, **but** "any commercial communication made by a Sender which is not registered with any Access Provider... shall be treated as an Unsolicited Commercial Communication", and deceptive calls are also treated as UCC — [Regulation 12 Feb 2025](https://www.trai.gov.in/sites/default/files/2025-02/Regulation_12022025.pdf) [PRIMARY]
- Penalty ladder, reg 25: first violation, outgoing services of "all telecom resources including PRI/SIP trunks, SIMs etc." barred by all access providers for 15 days, "irrespective of whether those telecom resources were actually used". Subsequent: disconnected by all providers for one year, blacklisted, no new resources, "all the devices used for making UCC shall also be blocked" for one year. One outgoing-barred number may be kept — [Regulation 18 Sep 2026](https://www.trai.gov.in/sites/default/files/2026-09/Regulation_18092026.pdf) [PRIMARY]
- Trigger: 5 complaints in 10 days; time to act cut from 30 days to 5; complaint window 7 days — [TRAI PR 11/2025](https://trai.gov.in/sites/default/files/2025-02/PR_No.11of2025.pdf) [PRIMARY]
- From the 90-day stage (Annexure): 3 unique complaints in 10 days plus an AI flag on the CLI. Separately, under reg 21A (30-day stage on my reading of cl. 1(3)), five flagged CLIs of one Sender in 10 days starts graded action including KYC re-verification, and CLIs in 140xx, 1600xx, 1601xx may not be flagged to recipients — [TRAI PR 119/2026](https://www.trai.gov.in/sites/default/files/2026-09/PR_No119of2026.pdf) [PRIMARY]
- Reg 21A(3): operators map a flagged CLI to the Sender's "unique KYC identifiers" and find all resources across operators — [Regulation 18 Sep 2026](https://www.trai.gov.in/sites/default/files/2026-09/Regulation_18092026.pdf) [PRIMARY]
- New reg 4: "Every Sender shall declare to the Originating Access Provider (OAP), in advance, about the use of Application-to-Person (A2P) calls, along with... CLI ranges... Provided that any such call made by a Sender without prior declaration to the OAP, shall be treated as Unsolicited commercial communication" — [Regulation 18 Sep 2026](https://www.trai.gov.in/sites/default/files/2026-09/Regulation_18092026.pdf) [PRIMARY]
- Commencement (cl. 1(3)): after 30 days generally; after 60 days for amending regs 4, 6, 8(a), 18 (A2P declaration and termination charge); after 90 days for the Annexure. Counted from 18 Sep 2026: about 18 Oct, 17 Nov and 17 Dec. A vendor notes the e-Gazette reference contains 28 September, so the dates could be later — [Regulation 18 Sep 2026](https://www.trai.gov.in/sites/default/files/2026-09/Regulation_18092026.pdf) [PRIMARY]; [The Telephony Co](https://www.thetelephony.co/india/blog/tcccpr-third-amendment-dates) [VENDOR]
- Until then, 2025 reg 4 requires written advance notice of auto-dialer or robo-call use and its objective — [Regulation 12 Feb 2025](https://www.trai.gov.in/sites/default/files/2025-02/Regulation_12022025.pdf) [PRIMARY]
- Reg 35A: termination charge up to Rs 0.05 per minute on A2P calls, none on designated series — [Regulation 18 Sep 2026](https://www.trai.gov.in/sites/default/files/2026-09/Regulation_18092026.pdf) [PRIMARY]
- TRAI on scope: "the regulations apply to all A2P calls"; limiting them "may result in regulatory arbitrage"; live-agent exclusion rejected; "it would [be] practically difficult to distinguish whether an A2P call made from numbers other than designated series was used for commercial and non-commercial communication" — [Regulation 18 Sep 2026, EM](https://www.trai.gov.in/sites/default/files/2026-09/Regulation_18092026.pdf) [PRIMARY]
- Enforcement data: April 2026, 15,736 of 20,591 disconnections were SIP/PRI connections "used for A2P calls" — [Regulation 18 Sep 2026, EM](https://www.trai.gov.in/sites/default/files/2026-09/Regulation_18092026.pdf) [PRIMARY]
- Series: 140xx promotional for all sectors; 1600xx government and BFSI service/transactional; 1601xx utilities, logistics, courier service/transactional. 1600 calls may not be tagged or blocked — [Regulation 18 Sep 2026, EM](https://www.trai.gov.in/sites/default/files/2026-09/Regulation_18092026.pdf) [PRIMARY]; [SCC Online on 10 Jul 2026 clarification](https://www.scconline.com/blog/post/2026/07/18/trai-clarifies-1600-and-140-series-number-framework/) [FIRM]
- 2025 memorandum para 18: promotional auto-dialer calls "through 140-series numbers only"; service and transactional "through 1600 or any other series allotted" — [Regulation 12 Feb 2025](https://www.trai.gov.in/sites/default/files/2025-02/Regulation_12022025.pdf) [PRIMARY]
- 13 Aug 2024 Direction: promotional calls from unregistered senders over SIP, PRI or other resources must cease; all resources disconnected for up to two years; blacklisting shared within 24 hours — [Khaitan via Legal500](https://www.legal500.com/intelligence/india/media-telecoms-it-entertainment/new-direction-by-trai-to-reinforce-tcccpr-compliance) [FIRM]. The TRAI PDF 404'd for me
- Consent: an inquiry-based relationship lasts seven days and must be in writing or digital form, kept verifiably (reg 2(bb)(ii)). Non-existing-customer service calls need Explicit Consent valid seven days (reg 2(bh)(ii)) — [Regulation 18 Sep 2026](https://www.trai.gov.in/sites/default/files/2026-09/Regulation_18092026.pdf) [PRIMARY]
- Plivo: "Cold calling is prohibited in India" — [Plivo India calling](https://www.plivo.com/docs/voice/concepts/india-calling.md) [VENDOR]

### Inferences
- The Aug 2024 "up to two years" appears overtaken by the regulation's 15 days / one year ladder. The 2025 memorandum cites the older two-year figure only as the prior position.
- The 2024 direction is about promotional calls, but the 2025 reg 2(bw) proviso catches *any* commercial communication by an unregistered Sender. So labelling a call "service" does not save a personal-number caller.
- A consumer user's AI calling their dentist or family is not obviously commercial communication, but it is A2P on its face, and TRAI said all A2P is in scope. Reg 4 binds "Sender", so the argument turns on a definition I could not read. Treat personal outbound as grey.
- There is an open tension. TRAI itself notes "a large number of service and transaction calls are also made by the Senders using normal 10 digit numbers", and Plivo offers landline numbers for non-BFSI service calls, while reg 3(1) reads more strictly. Non-BFSI service use on regular numbers appears tolerated now, and may narrow as series are designated.

### Gaps
- The Principal Regulations 2018 definitions of "Sender" and "Commercial Communication" were not read (PDF link 404). The TRAI 13 Aug 2024 Direction was not read in primary form.
- How an individual or small trader registers as a Sender, and any differentiated Sender classes (reg 3(1) proviso lets TRAI create them), remain unknown.

## What are the AI-specific, DPDP and call-recording rules?

### Takeaway
India has no AI-voice statute I could find. "Artificial voice" now appears expressly in the TCCCPR A2P definition, which requires disclosure **to the operator**. I found no duty to tell the callee, but deception is treated as UCC. DPDP's main duties start 13 May 2027. On recording, I found no Indian statute setting a general consent rule for private recording, and case law conflicts.

### Cited Findings
- Reg 2(ea) and reg 4 as quoted above. The Telephony Co reads no in-call AI disclosure mandate into the amendment — [Regulation 18 Sep 2026](https://www.trai.gov.in/sites/default/files/2026-09/Regulation_18092026.pdf) [PRIMARY]; [The Telephony Co](https://www.thetelephony.co/india/blog/trai-a2p-ai-voice-call-rules-2026) [VENDOR]
- DPDP Act: Data Fiduciary "determines the purpose and means"; Data Processor "processes personal data on behalf of a Data Fiduciary" (s.2(i), (k)). s.8(1): the Fiduciary is responsible "irrespective of any agreement to the contrary", including processing on its behalf. s.8(2): a processor is used "only under a valid contract". s.3(c)(i): the Act does not apply to "personal data processed by an individual for any personal or domestic purpose" — [DPDP Act 2023](https://www.meity.gov.in/static/uploads/2024/06/2bf1f0e9f04e6fb4f8fef35e82c42aa5.pdf) [PRIMARY]
- Phasing from the 13 Nov 2025 notification (G.S.R. 843(E) and 846(E) per this source): immediate for Board provisions; 13 Nov 2026 for consent-manager registration; 13 May 2027 for substantive provisions including ss.3-5, 7-17 and Rules 3, 5-16. The page was last updated 14 Nov 2025, so later change is not excluded — [DPDPA.com](https://www.dpdpa.com/blogs/DPDPA_Implementation_Timeline.html); [AZB](https://www.azbpartners.com/bank/indias-digital-personal-data-protection-act-phased-rollout-and-key-compliance-milestones/) [DIGEST]
- Up to Rs 250 crore penalty per breach for a fiduciary — [Deccan Herald](https://www.deccanherald.com/india/centre-notifies-dpdp-rules-implementation-planned-in-phases-spread-over-12-18-months-3798465) [DIGEST]
- TCCCPR Explanation 3 to consent: "Nothing contained in these regulations shall exempt the Sender from its obligations, or liability under the Digital Personal Data Protection Act, 2023". TCCCPR consent covers the channel; DPDP covers processing — [Regulation 18 Sep 2026](https://www.trai.gov.in/sites/default/files/2026-09/Regulation_18092026.pdf) [PRIMARY]
- Interception Rules 2024 (dated 6 Dec 2024): rule 3 is "Interception of message or class of messages by authorised agencies", and "authorised agency" means law enforcement or security agencies. It governs the state, not private recording — [Rules 2024](https://www.thc.nic.in/Central%20Governmental%20Rules/Telecommunications%20(Procedures%20and%20Safeguards%20for%20Lawful%20Interception%20of%20Messages)%20Rules,%202024.pdf) [PRIMARY]
- Supreme Court, *Vibhor Garg v. Neha* (2025): secretly recorded spousal calls admissible in matrimonial proceedings. This is an evidence ruling. Telangana HC (reported July 2026) is described as holding recording without consent breaches privacy and is inadmissible, an apparent conflict — [The Week](https://www.theweek.in/news/india/2025/07/14/secretly-recorded-calls-can-be-used-in-divorce-cases-rules-supreme-court.amp.html); [LiveLaw](https://www.livelaw.in/amp/high-court/telangana-high-court/telangana-high-court-secretly-recording-spouse-calls-right-to-privacy-540791) [DIGEST]

### Inferences
- Business user: likely Data Fiduciary, LesCall likely Processor. LesCall becomes a fiduciary for anything it decides itself, such as cross-tenant analysis, quality-improvement use or its own WhatsApp follow-ups.
- Consumer personal user: s.3(c)(i) may exempt the individual, which could leave LesCall as the only entity with DPDP duties to third-party callers. Counsel should look at this hard.
- Forwarded callers did not choose LesCall. A notice at call start ("this call is answered by an AI assistant and may be recorded") is the practical answer to both the consent and deception questions, though I found no rule prescribing it.
- LesCall's existing policy of admitting to being an AI when asked (CLAUDE.md, RUNBOOK §66) is consistent with the reg 2(bw) deception proviso, but it stops short of proactive disclosure.
- The spousal cases concern evidence and marriage, so they are weak guides for a commercial recorder.

### Gaps
- DPDP Rules text was not read. Rule 3 notice content comes from secondary lists only.
- No India-specific AI-call advisory from DoT, TRAI or MeitY was found beyond the TCCCPR text.
- No authority directly on business call recording by a SaaS, or on the "one-party consent" idea. I could not find a statutory basis for it.

## What do user-side terms and SIM rules add?

### Takeaway
Using a personal SIM for business is mainly a contract breach unless it becomes commercial calling, where TCCCPR applies. The 9-SIM limit is live and enforced. I found no rule that says "a business may not use a personal SIM" as such.

### Cited Findings
- Telecommunications (User Identification) Rules 2026 (G.S.R. 750(E), notified about 21 Aug 2026) and DoT Circular 3/User Identification/2026 (17 Aug 2026): nine connections across all TSPs and LSAs, six in J&K, Assam and North-East; extra connections declined from 24 Aug 2026; real-time checks by 30 Nov 2026 — [Mondaq via Legal500](https://www.legal500.com/intelligence/india/corporate-commercial-law/overview-of-telecom-user-identification-guidelines) [FIRM]; [Simpliance](https://simpliance.in/India/EHS/govt_notification/Central/notification-of-the-telecommunications-user-identification-rules-2026-15230) [DIGEST]
- Reports stress August 24 is stronger enforcement of an existing limit, not a new limit — [Sanskriti IAS](https://www.sanskritiias.com/current-affairs/sim-card-rules-2026-what-changes-after-august-24-and-how-it-affects-mobile-users) [DIGEST]
- August 2023 reforms: PoS dealer verification and discontinuing bulk connections — [Deccan Herald](https://www.deccanherald.com/india/police-verification-of-sim-dealers-mandatory-bulk-connections-discontinued-to-curb-frauds-ashwini-vaishnaw-2650964) [DIGEST]
- s.29(a): no user shall furnish false particulars, suppress material information or impersonate "while establishing his identity for availing of telecommunication services". This is narrower than general use — [Act](https://www.trai.gov.in/sites/default/files/2024-09/Telecommunications_01012024.pdf) [PRIMARY]
- DoT warns subscribers may be liable for misuse of SIMs issued in their name — [TelecomTalk](https://telecomtalk.info/?p=1001941) [DIGEST]
- DoT's 7 Dec 2021 re-verification instructions, as quoted by TRAI: a UCC complaint can trigger re-verification; a licensee identifying a "non-bonafide user" may re-verify and disconnect on failure — [Regulation 18 Sep 2026, EM](https://www.trai.gov.in/sites/default/files/2026-09/Regulation_18092026.pdf) [PRIMARY]

### Inferences
- An operator disconnecting a business-used personal SIM has a contract basis and, after complaints, a regulatory one. For the user the practical loss is their main phone number, so LesCall should explain this at onboarding.

### Gaps
- Current full Airtel, Jio and Vi prepaid and postpaid terms and CAF were not read. The bulk or business-connection end-user KYC norm was not read in DoT's text.

## Who bears liability, and where is counsel needed?

### Takeaway
Liability is layered and can land on more than one party at once. The subscriber loses telecom resources under TCCCPR. LesCall faces the most serious theoretical exposure if it provides a service or hardware, through s.42(1) and abetment. Plivo's main lever appears to be contract and routing, though it could also face intermediary action.

| Party | Exposure | Source |
|---|---|---|
| Subscriber / business user | TCCCPR Sender sanctions on all their resources and devices; Third Schedule; DPDP fiduciary if a business | Reg 3, 25; Third Schedule items 1-2 |
| LesCall | s.42(1) if unauthorised service; s.42(3) if it sets CLI or runs gateways; s.42(6) abetment; intermediary loss of resources; DPDP processor contract or fiduciary role; possible TIUE orders | Act; 2026 EM; DPDP s.8; Cyber Rules r.5 |
| Plivo / Indian carrier | OAP duties, financial disincentives on operators, call failure and account action | PR 11/2025; Plivo docs |

### Cited Findings
- Financial disincentives on access providers: Rs 2 lakh, Rs 5 lakh, then Rs 10 lakh per instance for misreporting UCC; operators may take security deposits and must sign binding agreements with registered Senders and Telemarketers — [TRAI PR 11/2025](https://trai.gov.in/sites/default/files/2025-02/PR_No.11of2025.pdf) [PRIMARY]
- s.42(1), (2), (3), (6), (7) as quoted above; Third Schedule item 4 catches residual contraventions at Rs 25,000 first, then up to Rs 50,000 per day — [Act](https://www.trai.gov.in/sites/default/files/2024-09/Telecommunications_01012024.pdf) [PRIMARY; column mapping per the note above]
- Sender can represent to the OAP, decided within 7 business days, and later to TRAI — [Regulation 12 Feb 2025](https://www.trai.gov.in/sites/default/files/2025-02/Regulation_12022025.pdf) [PRIMARY]

### Inferences
- Before launch, LesCall should treat the Plivo Direct Brand versus Reseller choice, and the A2P declaration of CLI ranges, as due by about 17 Nov 2026. The owner should check whether Plivo has already issued a process for this.

### Gaps
- Questions for counsel:
  1. Is forwarding-based inbound for business users "providing telecommunication services"?
  2. Does a subscriber-consented CLI fall under s.42(3)(b)/(c)?
  3. The s.42(3)(d) SIM threshold, and whether a single-SIM gateway needs authorisation.
  4. Whether LesCall is a Reseller, OSP or TIUE.
  5. Who is the Sender and who is the principal entity when a tenant's agent calls.
  6. Whether personal-use A2P is in scope of reg 4.
  7. DPDP roles for third-party callers, especially consumer users.
  8. Recording notice wording.
  9. Whether PSTN-to-PSTN human handoff raises toll bypass.
  10. Whether the s.33-34 civil-penalty provisions behind the Third Schedule are in force. The June 2024 commencement list I saw does not include them, and I did not confirm a later notification.
- Primary texts I could not obtain and that counsel should read: TCCCPR 2018 principal regulations, TRAI 13 Aug 2024 Direction, OSP Guidelines 23 Jun 2021, Principal Authorisation Rules 2026, Cyber Security Amendment Rules 2025, User Identification Rules 2026, SIM-binding Directions, DoT *401# order, Amendment 01/Licensing Policy/2026.
