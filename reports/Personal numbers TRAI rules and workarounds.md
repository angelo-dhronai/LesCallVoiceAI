# TRAI and telecom rules: what conflicts with the personal-number plans, and the legal workarounds

**Date:** 3 November 2026. **Companion to:** `reports/Personal numbers plan inbound and outbound.md`, whose Plans A to E are referred to here.

**This is research, not legal advice.** A telecom lawyer needs to confirm it before anyone relies on it.

**How far to trust this document:**

- It is built from the first research round. A fresh TRAI research run was stopped before it saved anything, so **no new legal research went into this document**.
- The workarounds are my analysis, built from those findings. No regulator or lawyer has reviewed them.
- "Legal-leaning" usually means I found no rule against something, and not that a rule allows it.
- Nine instruments have not been read (section 8).

**Evidence tags:** **[P]** I read the instrument's own text. **[F]** law-firm commentary. **[VN]** a vendor's own documents, which show that vendor's practice rather than the law. **[D]** seen only as a press or search summary. **[I]** my inference.

## 1. The short answer

- **Inbound by conditional forwarding** looks acceptable. I found no rule against a subscriber forwarding their own number, though nothing expressly allows it either.
- **Outbound showing the personal number on an AI-placed business call** has no legal route that I could find, however the call is connected. That rules out outbound in Plans B, C, D and E for business use.
- **The workaround** is Plan A: forward inbound calls, call out from a rented declared number, say whose assistant is calling, and give the personal number as the way back. Customers won't see the personal number on the AI's call.
- **The nearest date** is about 17 November 2026. It applies to your existing outbound calls even if you never build this.

## 2. Dates

| Date | What | Status |
|---|---|---|
| About 18 Oct 2026 | General provisions of the third TCCCPR amendment, including flagged-number action | Passed, on my reading of clause 1(3) [P] |
| 1 Nov 2026 | CNAP: name display from KYC, on 4G and newer | Passed; sources are summaries [D] |
| 13 Nov 2026 | DPDP consent-manager registration | Secondary source [D] |
| **About 17 Nov 2026** | **Undeclared A2P calls count as spam; termination charge starts** | **Could be about 27 Nov** if counted from the gazette reference (28 Sep) [VN]. **Unresolved, so plan on the 17th** |
| 30 Nov 2026 | Real-time checks on the SIM-per-person limit | Secondary source [F] |
| About 17 Dec 2026 | Annexure stage: lower complaint threshold with an AI flag | Same counting question as above [P] |
| 31 Dec 2026 | SIM-binding compliance, reportedly | Sources conflict: 28 Feb, 1 Mar, 30 Mar or 31 Dec 2026 [F/D] |
| 13 May 2027 | DPDP main duties begin | Secondary sources [D/F] |

## 3. The rules in plain language

"TRAI" is often used for all of this, but there are four separate sets of rules.

| Source | What it controls | Why it matters here |
|---|---|---|
| **TRAI's TCCCPR** (2018, amended 2025 and 2026) | Who may place commercial and automated calls, from what numbers, and the penalties | The main barrier to outbound |
| **Telecommunications Act 2023** | Licensing, number use, SIM equipment, criminal offences | Gateways, caller ID, and whether LesCall is providing a telecom service |
| **DoT orders** | Practical controls such as how forwarding can be switched on | The 2024 `*401#` order |
| **DPDP Act 2023** | Personal data of callers | An AI answering strangers' calls and recording them |

### The rules that matter most

**1. An AI call is an "A2P" call.** Reg 2(ea) defines it as a call "initiated by an application, software system, or automated platform without direct human dialling", including "artificial voice" [P].

**2. A2P calls must be declared in advance.** Reg 4 says every Sender "shall declare to the Originating Access Provider (OAP), in advance" its A2P use and CLI ranges. An undeclared call "shall be treated as Unsolicited commercial communication" [P]. TRAI rejected an exception for calls where a live agent joins later, and says the rules "apply to all A2P calls" [P].

**3. Business calls need the right kind of number.** Reg 3(1) requires commercial communication to use "registered headers or the number resources allotted to the Senders from special series" [P]. Under reg 2(bw), "any commercial communication made by a Sender which is not registered with any Access Provider" is treated as unsolicited, and so are deceptive calls [P]. Calling it a "service call" doesn't rescue a caller on a personal number [I].

**4. The number series.** [P/F]

| Series | For |
|---|---|
| 140xx | Promotional calls, all sectors |
| 1600xx | Government and BFSI service and transactional calls |
| 1601xx | Utilities, logistics and courier service and transactional calls |
| Landline (022, 080…) | Plivo offers these for non-BFSI service and transactional calls [VN]. TRAI notes many senders use normal numbers. This looks tolerated today and may narrow [I] |

**5. The penalty falls on the person, not only the number.** Under reg 25 [P]:

- **First violation:** outgoing barred on "all telecom resources including PRI/SIP trunks, SIMs etc." by all operators for 15 days, whether or not those resources were used.
- **Next violation:** all disconnected for one year, blacklisted, no new resources, and the devices used are blocked for one year.
- **Trigger:** 5 complaints in 10 days [P]. From the Annexure stage, 3 unique complaints in 10 days plus an AI flag [P].
- **Reach:** a flagged number is mapped to the Sender's KYC, across operators (reg 21A(3)) [P].

For a user, this means losing their main phone number.

**6. A whole trunk can be barred.** TRAI's memorandum says "whole PRI/SIP links need to be barred, and not individual DID numbers", and an intermediary's numbers can be acted against [P]. In April 2026, 15,736 of 20,591 disconnections were SIP/PRI connections used for A2P calls [P]. So one tenant's complaints could reach every tenant on the shared trunk, including forwarded inbound lines [I].

**7. Caller ID.** It is an offence to use "telecommunication identifiers not allotted or permitted" or to "tamper" with them, with up to 3 years and up to Rs 50 lakh, cognizable and non-bailable (Act s.42(3)(b),(c), s.42(7)) [P]. Whether a subscriber-consented number counts is untested, and "tamper" isn't defined [I].

**8. SIM equipment and unlicensed service.** Possessing radio equipment beyond a notified SIM count is an offence (s.42(3)(d)) [P]. Providing telecom services without authorisation carries up to 3 years and up to Rs 2 crore (s.42(1)) [P]. Knowingly **using** such a service is also exposed (Third Schedule item 2) [P]. Operators must detect "SIM Farm/SIM box type usage" [P].

**9. Forwarding activation.** DoT told operators to stop USSD-based forwarding from 15 April 2024 "till further notice", naming `*401#` [D]. I found no lifting notice. Whether it reaches other codes such as `**61*` is unknown.

**10. Other parties' terms.** Plivo says calls must use Plivo-rented numbers, "Cold calling is prohibited in India", both legs must stay in India, and only India-registered businesses can rent numbers [VN]. Operator plans reportedly limit benefits to personal, non-commercial use, which is a contract risk rather than an offence; those sources are old [D].

## 4. What in each plan conflicts

| Plan | Part | Conflict | Rule | Position | If ignored |
|---|---|---|---|---|---|
| **A** | Inbound by conditional forward | No direct conflict. Activation route and contract terms | `*401#` order; operator terms | **Legal-leaning** | Plan benefits at risk; no offence found |
| **A** | AI answering and recording strangers | Callers didn't choose us | DPDP from May 2027; recording law unsettled | **Manageable** | Up to Rs 250 crore per breach for a fiduciary, from 2027 [D] |
| **A** | Rented numbers for many users | Are we reselling? Whose KYC? | Plivo Reseller rule; s.42(1) | **Open question** | Service-provision exposure |
| **A** | Cloud SIP to our server | "Anyone without a licence cannot connect IP with PSTN"; cloud PBX is grey, according to one practitioner [D] | Practitioner view; OSP guidelines unread | **Grey**, and it applies to today's production as well | Needs a lawyer |
| **A** | Outbound from a rented number | Must be declared, in the correct series, with consent | Reg 3, reg 4 | **Legal if done properly** | Calls treated as spam; penalties; trunk barred |
| **B** | Inbound | As Plan A | As Plan A | **Grey to legal-leaning**; no instrument addresses an app | As Plan A |
| **B** | **Outbound, business** | Commercial A2P from a personal number | Reg 2(ea), 2(bw), 3, 4, 25 | **Not legal** | Loses all the person's numbers |
| **B** | Outbound, personal errands | A2P on its face; "Sender" definition unread | Reg 2(ea), 4 | **Grey** | Lawyer first |
| **B** | App with a live call | Would be closer to interception if it captured other people's conversation | s.42(2)(b) | **Grey**; conference doesn't read audio, though the server hears the call | Up to 3 years and Rs 2 crore if it applies |
| **C** | Inbound | No instrument addresses a Bluetooth bridge | s.3(1), s.42(1) if we run it | **Grey** | Service-provision theory |
| **C** | **Outbound, business** | Same as B | Same as B | **Not legal** | Same as B |
| **D** | SIM device | Radio equipment; threshold not found | s.3(1), s.42(3)(d) | **Grey for one SIM. Prohibited for multi-SIM** | s.42(1) and abetment if we ship or run it |
| **D** | **Outbound, business** | Same as B, and also resembles the SIM-box pattern operators detect | Reg 3, Sch. IV | **Not legal** | Same as B |
| **E** | Outbound, human dials | A person dials, so arguably not "without direct human dialling", but the voice is artificial, and any commercial call by an unregistered Sender is still caught | Reg 2(ea), 2(bw), 3 | **Business: not legal. Personal: grey** | Same as B |
| **E** | Inbound | As Plan A | As Plan A | **Legal-leaning** | As Plan A |
| All | Voice OTPs in forwarded calls | We would transcribe and store them [I] | DPDP; misuse concerns | **Manageable** | Sensitive data held in transcripts |
| Excluded | Trunk showing P | Refused, and also the s.42(3) question | s.42(3)(b),(c); Plivo | **Treat as not legal** | Criminal exposure |
| Excluded | Foreign route showing +91 | It is what DoT's spoofed-call system blocks | DoT; s.42(3) | **Treat as not legal** | Criminal exposure |
| Excluded | Multi-SIM gateway | Clearest prohibition | s.42(1), s.42(3)(d), Sch. IV | **Not legal** | Up to Rs 2 crore; customers exposed too |

**The key point:** connecting differently doesn't change the outbound answer. For a business, the problem is the personal number being used for commercial calling, whichever way it reaches the network.

## 5. Legal workarounds

Each one is my analysis, not legal clearance.

| # | Workaround | What people get | What the customer sees | Position | Remaining risk |
|---|---|---|---|---|---|
| **W1** | Conditional forward set in the operator app or phone settings | Agent answers calls to P | They dial P as normal | Legal-leaning | Absence of a rule; contract terms |
| **W2** | Rented, KYC'd, declared number in the correct series, with the person's name in the introduction | AI calls on the person's behalf | R, and their name spoken | Legal if declared and consented | Landline series may narrow; consent rules apply |
| **W3** | W2, plus P given as the way back, with W1 on P | Both directions linked to P | R first, then they call P | Legal-leaning | Untested; the callback goes to the agent, so say so |
| **W4** | Customer calls first: publish P; website or WhatsApp prompt | Conversation on P with no outbound event | P | Legal-leaning | Depends on customers acting |
| **W5** | The person calls normally with no agent on the line; the forward handles missed returns | Real calls from P | P | Ordinary use. Commercial use may still be caught | "Sender" definition unread |
| **W6** | Web link or QR code | Talking to the agent without a phone network leg | Their browser | Lowest exposure | Doesn't serve calls to P |
| **W7** | WhatsApp Calling on a second business number | Both directions on WhatsApp | The WhatsApp number | Legal-leaning, thin | Meta's terms decide; business-initiated needs opt-in; a reported AI-chatbot policy is unverified |
| **W8** | Verified business name through CNAP or preferred-name options | A recognisable name on R | R plus a name | Unknown | Sources are summaries. Ask Plivo whose name shows |
| **W9** | Ask an operator about offering P on internet telephony | P truly on SIP, through a licensee | P | Unknown. A 2018 DoT amendment lets a licensee use the same number for both [F] | No product found. It would need a commercial deal, and the A2P rules still apply |
| **W10** | Written clarification from TRAI, DoT or the operator on personal-use AI calls | Certainty for Plans B and C on personal errands | P, if allowed | Unknown | May take time, or the answer may be no |

**Recommended package: W1 + W2 + W3**, with W4 and W6 as extra ways in.

**What this doesn't achieve:** customers won't see P on an AI-placed business call. I found no compliant way to do that. Keeping call volumes low isn't a workaround, because the rules attach to the sender, and this document gives no method of avoiding detection.

**Personal, non-commercial use.** An AI booking a dentist for an individual isn't obviously commercial, but it is A2P on its face. I'd treat it as grey, and hold Plans B and C until a lawyer or a written clarification (W10) gives a basis.

## 6. What LesCall would need to do

| # | Action | Applies to | Where we stand |
|---|---|---|---|
| 1 | Declare A2P use and CLI ranges to the operator, through Plivo | **All outbound today** | **Not established. Most urgent** |
| 2 | Confirm which series each of the five live numbers uses, and whether any traffic is promotional | All outbound | Not established |
| 3 | Move each tenant to its own KYC'd numbers where possible, and ask whether trunks can be separated | All tenants | Trunks share a Plivo host [V from audit]; separation unknown |
| 4 | Settle Plivo's Direct Brand or Reseller position | Plan A | Unknown; Plivo needs a separate application per customer if Reseller [VN] |
| 5 | Keep consent records: an enquiry lasts 7 days and must be written or digital, kept verifiably; non-customer service calls need explicit consent, valid 7 days [P] | All outbound | To check against the contact and campaign flows |
| 6 | Keep the DND check | All outbound | The follow-up autodialer has one (`shared/intelligence/autodial.py:131`). Confirm it covers every dial path |
| 7 | Refuse a mobile as caller ID | Everything | **Not done:** `dashboard/telephony_api.py:381-400` accepts it |
| 8 | Onboard with the operator app or settings, never `*401#`, and keep proof the subscriber did it | Forwarding | To build |
| 9 | Spoken notice: "This call is answered by an AI assistant and may be recorded" | Forwarded calls, ideally all | I found no rule requiring it, but it addresses consent and deception. Today the agent admits to being an AI only when asked (`calling-agent/backend/app/voice/ai_disclosure.py`) |
| 10 | Tell users plainly: a rented number sits behind the forward, how to cancel, who pays airtime, and the risk to their number if used for spam | Forwarding | To write |
| 11 | Tenant terms: declare purpose, no unconsented calling, responsibility for numbers | All tenants | To write, with a lawyer |
| 12 | DPDP: Processor contract with business users; notice; retention; handling of voice OTPs in transcripts | Forwarding | To design before May 2027. A consumer user may be exempt under s.3(c)(i), which could leave LesCall holding the duties [I] |
| 13 | Keep media in India | All calls | Host is in `asia-south1-c` [V] |
| 14 | Complaint handling: stop calling a number on complaint, and watch per-tenant complaint signals | All outbound | To check |

## 7. Who carries the risk

| Party | Exposure |
|---|---|
| **User or business** | Loss of all their numbers and devices under TCCCPR; DPDP duties if a business |
| **LesCall** | s.42(1) if we provide an unauthorised service; s.42(3) if we set caller ID or run gateways; abetment under s.42(6); losing shared trunks; DPDP duties |
| **Plivo or carrier** | Operator duties; they may refuse calls or act against the account |

## 8. What is still unread or unresolved

**Not read in the original text:**

1. TCCCPR 2018 principal regulations, including the definitions of "Sender" and "Commercial Communication"
2. TRAI's 13 August 2024 Direction
3. OSP Guidelines of 23 June 2021
4. Principal Authorisation Rules 2026
5. Cyber Security Amendment Rules 2025
6. User Identification Rules 2026
7. SIM-binding Directions
8. DoT's `*401#` order
9. Licence Amendment 01/Licensing Policy/2026

**Also unresolved:**

- The notified SIM count under s.42(3)(d), which decides the single-SIM question
- 17 vs 27 November for the A2P date
- Whether the `*401#` order reaches other forwarding codes
- Whether the DPDP Rules text changes the roles described above, since it wasn't read
- Current full Jio, Airtel and Vi terms
- Which Indian carrier sits behind Plivo's numbers, and Plivo's licence status

## 9. Questions for the lawyer

1. Is forwarding-based inbound for business users "providing telecommunication services"?
2. Does cloud termination of forwarded calls raise the IP-to-PSTN question, including for today's production?
3. Is LesCall a Reseller, an OSP or a TIUE?
4. Who is the Sender and who is the principal entity when a tenant's agent calls?
5. Is the 17 or the 27 November date correct, and how is A2P declared through Plivo?
6. Does personal-use AI calling fall under reg 4?
7. Does a subscriber-consented number fall under s.42(3)(b) or (c)?
8. What is the s.42(3)(d) SIM threshold, and does a single-SIM gateway or Bluetooth box need authorisation?
9. Does a human-dialled call with an AI voice count as A2P?
10. What are the DPDP roles for third-party callers, especially under consumer users?
11. What should the recording notice say?
12. Does connecting a call back to a person's phone raise toll bypass?
13. Are the ss.33–34 civil-penalty provisions behind the Third Schedule in force?

## 10. Main sources

- [TRAI TCCCPR amendment, 18 Sep 2026](https://www.trai.gov.in/sites/default/files/2026-09/Regulation_18092026.pdf) and [press release 119/2026](https://www.trai.gov.in/sites/default/files/2026-09/PR_No119of2026.pdf)
- [TRAI TCCCPR amendment, 12 Feb 2025](https://www.trai.gov.in/sites/default/files/2025-02/Regulation_12022025.pdf) and [press release 11/2025](https://trai.gov.in/sites/default/files/2025-02/PR_No.11of2025.pdf)
- [Telecommunications Act 2023](https://www.trai.gov.in/sites/default/files/2024-09/Telecommunications_01012024.pdf)
- [DPDP Act 2023](https://www.meity.gov.in/static/uploads/2024/06/2bf1f0e9f04e6fb4f8fef35e82c42aa5.pdf) and [AZB on phasing](https://www.azbpartners.com/bank/indias-digital-personal-data-protection-act-phased-rollout-and-key-compliance-milestones/)
- [Plivo: India calling](https://www.plivo.com/docs/voice/concepts/india-calling.md), [verified caller ID](https://www.plivo.com/docs/voice/concepts/verified-caller-id.md), [renting India numbers](https://www.plivo.com/docs/numbers/rent-india-numbers.md)
- [Communications Today on the `*401#` order](https://www.communicationstoday.co.in/dot-directs-deactivation-of-ussd-based-call-forwarding-by-april-15/)
- [Khaitan on OSP 2021](https://khaitanco.com/sites/default/files/2021-06/Ergo_25062021_0.pdf), [Khaitan on Authorisation Rules 2026](https://www.khaitanco.com/sites/default/files/2026-06/Ergo_Principal%20Telecom%20Authorisation%20Rules_26062026.pdf), [AZB on internet telephony](https://www.azbpartners.com/bank/clarification-and-amendments-regarding-internet-telephony/)
- [The Telephony Co on the amendment dates](https://www.thetelephony.co/india/blog/tcccpr-third-amendment-dates), [Anurag Bhatia on IP telephony in India](https://anuragbhatia.com/post/2026/09/ip-telephony-in-india/)

Full clause quotes and every other source are in `research_notes/LesCall on personal phone numbers/india_regulation.md`.
