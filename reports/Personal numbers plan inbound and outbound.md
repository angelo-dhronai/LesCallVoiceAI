# Plan: LesCall agents on personal numbers, inbound and outbound

**Date:** 3 November 2026. **Status:** proposal only. Nothing here has been built, tested or approved, and no call has been placed.

**Companion document:** `reports/Personal numbers TRAI rules and workarounds.md` covers what in these plans conflicts with the rules and how to work around it. Read it before choosing a plan: this document describes how each plan works, not whether it may be used.

**Evidence tags:** **[V]** read in our code, config or logs. **[S]** stated by a vendor, a standard or the press, not confirmed by us. **[I]** my inference. **[U]** unknown until tested. Effort ratings (small, medium, large) are my judgement, not estimates of time. Rupee figures are list prices or one measured call, not quotes.

**Terms:**

| Term | Meaning |
|---|---|
| **P** | The user's personal mobile number |
| **C** | The customer: the person calling P, or being called |
| **R** | A rented number shown to customers on outbound calls |
| **D** | A rented number the agent answers on, hidden from customers |
| **CCF** | Conditional call forwarding: the network forwards calls to P when P is busy, unanswered or unreachable |
| **HFP** | Bluetooth Hands-Free Profile, the way a car kit answers and places a phone's calls |
| **Gateway** | A device holding a SIM that connects phone calls to our server |

## 1. Which cases qualify

A plan fully qualifies only if both are true:

- **Inbound:** when someone dials P, the agent can answer.
- **Outbound:** when the agent calls someone, the customer sees P.

Only the user's own SIM can make the second one true. A call placed by a server over a carrier trunk cannot show P, so every full plan needs that SIM to place the call.

| Plan | Inbound on P | Outbound shows P | Unattended | iPhone | Calls at once | Rented number | State |
|---|---|---|---|---|---|---|---|
| **A. Forward + rented outbound** | Yes, CCF | **No.** Shows R, gives P as the way back | Yes | Yes | Many | Yes (D and R) | Closest to working: outbound runs today, forwarding untested |
| **B. Android app + CCF** | Yes, CCF | Yes, by conference | Partly | **No** | 1 | Yes (D) | Never prototyped |
| **C. Bluetooth box** | Yes, direct | Yes | Yes, near the phone | Yes in principle | 1 | **None** | No product found |
| **D. User's SIM in a gateway** | Yes, direct | Yes | Yes | Not applicable | 1 | **None** | Hardware exists; Jio doubtful; **the phone loses P** |
| **E. Manual merge + CCF** | Yes, CCF | Yes, by conference | **No**, the user dials | Yes | 1 | Yes (D) | Usable as a first test |

Plan A is partial, because it does not show P on outbound. It is included because it is the only plan with a legal route for business calls (see the companion document).

**Excluded, with the reason:**

| Route | Why it is out |
|---|---|
| Plivo or another carrier presenting P as caller ID | Refused: Plivo hangs up with `unknown_caller_id`, and Edesy returns `403 Caller ID Not Authorized` [S]. Our code would attempt it, so it needs a guard (section 3, F2) |
| Foreign route showing a +91 number | Closed on Plivo and Twilio [S], and treated as not legal. Not designed here |
| Android or iOS app reading call audio | Android reserves it for privileged apps [S]. I found no public iOS route [I] |
| Moving P onto SIP, or an operator "one number" service | No Indian product found [S, absence of evidence] |
| WhatsApp Calling | Not a phone call to P; the number must also leave the WhatsApp app [S] |
| Click-to-call bridge | The customer sees R and a human must answer, so it adds nothing to Plan A |
| Multi-SIM gateway run by us or for customers | Prohibited on the earlier research |

## 2. Forwarding always keeps a number behind it

A mobile's forward has to point at another phone number. The 4G voice standard also allows a SIP address, but only if the operator enables it, and no Indian operator was found selling that [S]. So Plans A, B and E always have a rented number behind them. Plans C and D have none, because the SIM itself connects to us.

| Plan | With Plivo | Without Plivo |
|---|---|---|
| A | D and R rented from Plivo | D and R from Edesy, Vobiz, Exotel or an operator block. Outbound gateway and default must move (section 8) |
| B | D from Plivo | D from any carrier. The customer sees P either way |
| C | Not needed | Not needed |
| D | Not needed | Not needed |
| E | D from Plivo | D from any carrier |

## 3. Foundation work, needed before any plan

| # | Work | Why | Files | Effort |
|---|---|---|---|---|
| F1 | Fix direction labelling: any tenant's dial in the last 120 s marks an inbound call outbound | It picks the wrong agent and stores our number as the customer | `conversation_call_bridge.py:438,467-473,594-604,667` | Small |
| F2 | Refuse registering a mobile as a normal number or as caller ID | The API accepts one today and the platform would try to present it [V] | `dashboard/telephony_api.py:381-400,444-446`, `dashboard/server.py:4825` | Small |
| F3 | Close port 5080 to everyone except carriers, after DevOps reads the firewall | A line matched by caller number is only as trustworthy as this gate | `reports/LesCall personal numbers without Plivo.md` section 6 | Small |
| F4 | New `personal_lines` table in migration `0032`. Never a `phone_numbers` row for P | Several subsystems treat those rows as dialable carrier numbers, and the daily probe would ring them | New; `shared/db/schema.sql:57-74` for contrast | Medium |
| F5 | Keep unknown numbers away from the founder's agent | A forgotten forward reaches another tenant's agent | `conversation_call_bridge.py:354,377-381,388`, `/etc/freeswitch/dialplan/public.xml:16-17` | Small to medium, risky |
| F6 | Stop the daily probe cutting real calls, and exclude these lines from silence alerts | It matches by destination, and quiet personal lines would alert every 26 h | `shared/sentinel.py:64,398-428,449-473` | Small |
| F7 | Guard the test call against a self-forward loop | The agent would answer itself on two billed legs | `dashboard/server.py:5223-5249` | Small |
| F8 | Spoken notice on the first line for forwarded callers, and a way to skip CRM treatment for family and friends | Callers did not choose LesCall; contacts and scoring are created automatically | Agent YAML; `conversation_call_bridge.py:1566-1603` | Small |

**Proposed table.** This replaces the separate `forwarding_sources` table suggested in the first report, so every plan shares one model:

```
personal_lines(
  owner, number, sig_digits UNIQUE,
  mode            -- forward | device | hfp | gateway
  did_id,         -- the rented number behind it, if any
  trunk_ref,      -- gateway or box identity, if any
  inbound_agent_slug, outbound_agent_slug,
  status, verified_at, verify_method, last_seen_at, label
)
line_leases(line_id, dial_or_conn_id, expires_at)   -- one call per SIM
```

**Proving ownership of P is required.** Whoever registers a mobile receives its calls. There is no SMS or OTP channel today (`shared/intelligence/automation.py:417-418`) [V], so the proof is either a call from P matched on its caller ID, or an AI call to P that speaks a code.

## 4. Plan A: forward inbound, rented number outbound

**Result:** callers dial P and reach the agent. The agent calls from R, says whose assistant it is, and gives P as the way back.

```
INBOUND
C dials P -> phone rings -> no answer, busy or unreachable
  -> network forwards to D -> Plivo or other carrier -> FreeSWITCH :5080
  -> socket_server.py -> ws_audio_receiver.py -> bridge finds agent by D -> inbound agent

OUTBOUND
LesCall -> carrier -> C sees R
Agent: "Calling on behalf of <name>. You can reach them on <P>."
Later: C dials P -> same inbound path -> agent answers, sharing history if C's number survives
```

**Setup for one user:**

1. Rent D and R, on a KYC'd account. R and D should be separate rows, because a `both` row gets probed.
2. Register D as `direction='inbound'` with the inbound agent, and R as `outbound` with the outbound agent.
3. The user sets a no-answer rule (timer 5–30 s), a busy rule and an unreachable rule in the operator app or phone settings. Do not use `*401#`, and do not use unconditional forwarding as the default.
4. The user reads the rules back using `*#61#`, `*#67#` and `*#62#` [S].
5. Run one test call in each direction. Declining a call usually triggers the busy rule, so the user gets a "send to assistant" gesture [S, vendor only].

**Platform changes:**

| Change | Files | Effort |
|---|---|---|
| None for a hand-set-up pilot, apart from F1–F3 and F8 | Routing already works by D [V]; a real forwarded call has never been captured | None |
| "Use my mobile" onboarding with per-operator steps | `frontend/src/pages/Telephony.tsx:395-397`, `frontend/src/components/ActivateAgent.tsx:60,80,103-104`, `frontend/src/pages/Overview.tsx:57-74` | Medium |
| Number pool or admin create and transfer | `shared/telephony_admin.py:294,315-320,556` | Small to medium |
| Forwarding health check: scheduled test call, plus a warning when D goes quiet | New | Medium |
| "Expected return" record so a callback gets the right framing. Today, a return within 120 s is labelled outbound | `conversation_call_bridge.py:438,667`, `calling-agent/backend/app/voice/conversation.py:3760-3790` | Medium, optional |
| Owner notification by WhatsApp template. Today it's the in-app bell only | `shared/intelligence/notify.py:28-41`, `shared/whatsapp/sender.py:88` | Small, optional |

**Costs per user per month [I]:** ₹200 for each rented number, plus ₹0.38/min at a 30-second pulse [S]. D alone comes to ₹238 at 100 minutes and ₹390 at 500. AI cost is about ₹2.6/min on top, based on one measured call. The user's airtime on the forwarded leg is unconfirmed [U].

**Limits and failure handling:**

| Failure | What happens | Handling |
|---|---|---|
| Rule silently lost | Calls ring out; looks like a quiet phone | Health check. One vendor reports 30–43% of missed calls never arrive, with no sample size [S] |
| Carrier rewrites the caller's number to P | All callers share one history; their brochures go to the owner | Test T1. Disable memory, contact and WhatsApp identity on that operator if so |
| "Your call is being forwarded" announcement | Caller hears it first; agent could read it as a machine | Test T5, and check `machine_line.py:29` |
| Forward target refused (Jio excludes BSNL numbers) | Forward can't be set | Test T2; choose another number class or carrier |
| User cancels but forgets the rule | Calls go to a dead line or a stranger's agent | Keep D reserved through a grace period, with a neutral answer |

## 5. Plan B: Android app, conference outbound, CCF inbound

**Result:** on Android, the customer sees P on the agent's outbound call. Inbound works as in Plan A.

The app never touches call audio, so Android's audio block doesn't apply. It uses the phone's own conference: the phone calls both C and D and merges them, and the agent hears C through D [I].

```
ORDER B (test this first)
LesCall -- push --> app: "call C, use line D, token T"
app calls D -> FreeSWITCH answers; greeting stays silent
app calls C -> D goes on hold; C answers
app merges and mutes the phone -> tells LesCall "merged"
LesCall arms the greeting -> agent talks to C

C <-> carrier conference <-> D <-> FreeSWITCH <-> agent
                ^ the user's phone stays in the call, muted
```

Order A calls C first, which leaves C on hold while D connects. A third variant has LesCall call P from R and the app auto-answer, which saves one airtime leg but needs `originate_cmd` to separate the dialled number from `lescall_dest`, and P's own CCF may divert that leg.

**A better version may exist:** explicit call transfer would hand C to D and free the phone (`Call.transfer` on Android API 37). Whether Jio, Airtel or Vi support it is unknown [U].

**Platform changes [I]:**

| Change | Files | Effort |
|---|---|---|
| Android default-dialler app: dial pad, full in-call screen, push handling | New app | Large |
| Device API: register, call events (`d_connected`, `answered`, `merged`, `merge_failed`, `customer_ended`, `busy`), plus `dial` and `end_call` pushes | New; no device channel exists today [V] | Medium to large |
| Map device events onto dial results | `dashboard/server.py:5138-5145` (`_parse_fs_result`) | Medium |
| Tell the bridge who C is, because the leg arrives with caller = P. Today it stores the customer as our DID or as the owner's mobile [V] | `conversation_call_bridge.py:581-604,611-697` | Small to medium |
| Gate the greeting on "merged". It would fire as soon as D connects today | `pipeline.py:1170-1183`, new request type at `socket_server.py:1073-1103` | Medium |
| Suppress watchdogs and beep close until merged. A hold message plus a join tone can look like a mailbox beep | `pipeline.py:725-755`, `conversation.py:3330-3336`, `audio_events.py:48-57` | Medium |
| Two-way hangup through the app | `conversation_call_bridge.py:2039-2049,2123` | Medium |
| One call per line; a busy line defers without spending an attempt | `line_leases`, `shared/intelligence/autodial.py`, `dashboard/server.py:5660-5684` | Medium |
| Bill from the merge, so ring and hold time aren't billed as talk | `shared/usage_metering.py:14-24,96-118` | Small to medium |

**Limits:**

- **Android only.** iPhones have no equivalent route [I].
- **Must be the default dialler**, replacing the user's own.
- **Phone must be on, online and idle.** Xiaomi, OnePlus and Samsung rank worst for killing background apps [S], and push can be delayed or blocked.
- **The phone is occupied for the whole call**, and the user can't take another call.
- **The user pays airtime** on one or two legs [U].
- **Answer detection is uncertain.** A call can show active before the person answers [S].

## 6. Plan C: Bluetooth box paired to the phone

**Result:** the SIM stays in the phone, the customer always sees P, and no number is rented. It works on Android and iPhone in principle [I].

A small computer acts like a car kit. It answers and dials through the phone over Bluetooth, and carries the call audio to our server.

```
OUTBOUND
FreeSWITCH -> box -> Bluetooth dial -> phone's SIM -> C sees P

INBOUND
C dials P -> phone rings -> box answers after N seconds -> FreeSWITCH -> agent
```

**Hardware [S]:** ESP32 or a Linux board (`esp_hf_client`, or oFono with BlueALSA). Field reports are old and mixed, and no commercial product was found. Each box handles one phone within Bluetooth range.

**Platform changes [I]:**

| Change | Why | Files | Effort |
|---|---|---|---|
| Box firmware and service | New device: Bluetooth control, audio bridge, registration, update and logging | New | Large |
| Per-box login on FreeSWITCH's internal profile | A home box sits behind a router, so it has to connect in. Only stock users exist and no generator does [V] | `internal.xml:87,279`, `vars.xml:421`, `shared/freeswitch_config.py:6-9` | Medium |
| Enable encryption, and keep the port open | TLS is off (`vars.xml:424`). **This conflicts with the suggestion to close 5060 and 7443**, so decide before DevOps closes them | `vars.xml:424`, internal profile | Small |
| New dial type | Outbound always uses `sofia/gateway/...` today, so a registered box needs another form | `shared/fs_originate.py:78-106`, `dashboard/server.py:4781-4830` | Small to medium |
| Bind each box to its line | A hacked box could otherwise name any tenant's number | `personal_lines`, `conversation_call_bridge.py:348-367` | Medium |
| One call per line | Same as Plan B | `line_leases` | Medium |
| Audio check | Bluetooth call audio is narrowband and processed by the phone; our VAD is untested on it | Test against `pipeline.py` and FSMN | Medium |

No customer-identity or greeting change is needed: `lescall_dest` already carries C, there is no conference, and hangup travels normally [I].

**Limits:**

- **The phone has to stay in range**, so a user who leaves with the phone also takes the line away.
- **Audio route:** the user's phone call audio is taken over when the box is active, and how the user picks up a call needs designing.
- **iPhone dialling on a locked phone is unverified** [U].
- **Power and internet at the box's location** decide availability.

## 7. Plan D: user's SIM in a gateway, and Plan E: manual merge

### Plan D

**Result:** a clean line in both directions and no rented number, but **the phone no longer has P**, because the SIM has moved into the gateway. It fits a dedicated business mobile, not a person's main phone.

```
OUTBOUND: FreeSWITCH -> gateway -> SIM -> C sees P
INBOUND:  C dials P -> gateway -> FreeSWITCH -> agent
```

A second SIM in the gateway does not solve this, because it has its own number. No twin-SIM product was found in India; the one same-number service found is for paired smartwatches, from 2018-era sources [S].

**Platform changes [I]:**

- Add a gateway through `shared/freeswitch_config.py:66-111`. Static IP: it sends calls to `:5080` and needs a firewall entry. Behind a router: it needs the box login from Plan C.
- Exclude it from the probe (`shared/sentinel.py:449-473`), and take a line lease per call.
- Decide whether the "no mobile in `phone_numbers`" rule has an exception here. I recommend using `personal_lines` instead.

**Hardware [S]:** dealer listings put a 2-port at about ₹20,000 and a 4-port at ₹37,895–46,000. **No single-port VoLTE model was verified**, and Jio reports on modules are poor, including failed voice and calls that never ring.

### Plan E

**Result:** the user dials the customer, adds D, merges and mutes. The agent then talks. It works on any phone, including iPhones, using no app [I].

```
User dials C (C sees P) -> Add call -> dials D -> Merge -> Mute
Inbound: CCF to D, as in Plan A
```

It is not an agent product, because a person is needed on every call. Its value is that it answers most of Plan B's unknowns with no code: hold wording, join tone, merge delay, and audio quality.

For regular use, it needs the same "who is C" and "merged" handling as Plan B, triggered by a dashboard button rather than an app (user taps "Merged", and enters or selects C). Hangup stays manual.

## 8. Moving off Plivo, for plans that keep a rented number

Plivo isn't the deciding factor for any plan. What changes is who bills for D and R.

| Step | Files or systems |
|---|---|
| Add a trunk row with another provider | `shared/db/schema.sql:35-50`, `shared/telephony_admin.py:262,286`, `dashboard/telephony_api.py:212,359-361` |
| Add number rows, `direction='inbound'` for D | `shared/sentinel.py:449-457` |
| Allow the carrier in the firewall **and** in `trunks.provider_ips` | Section 6 of the second report |
| Capture one call first: does the carrier send the full number and the true caller? | `socket_server.py:539,548-549,1932-1933` |
| If digits are short, normalise them, or calls fall to the founder's agent | `conversation_call_bridge.py:388`, `shared/freeswitch_config.py:101-104` |
| Outbound: add a gateway and use a number owned on that carrier | `shared/freeswitch_config.py:66-111`, `dashboard/server.py:4824-4830` |
| Change the default gateway and health checks | `shared/fs_originate.py:53`, `dashboard/server.py:4732`, `shared/voice_health.py:76` |

**Cost per user per month, inbound, before GST [I]:**

| Option | 100 min | 500 min | Note |
|---|---|---|---|
| Plivo | ₹238 | ₹390 | Cheapest for a pilot |
| Edesy | ₹499 | ₹699 | Useful as a backup carrier |
| Vobiz | ₹545 | ₹725 | Same |
| Tata or Airtel block of 100 numbers | ₹150–200 at 100 users | Same | Plus circuit cost; weak prices; cloud delivery needs negotiating; beats Plivo at about 40–85 users |
| Exotel | Unknown | Unknown | Needs a quote |

## 9. Tests that decide the plan

Every test uses our own SIMs and test numbers. Register the spare number with **no agent bound** and `direction='inbound'`, because an unregistered number reaches the founder's agent. The T tests are defined in full in the first report.

| Test | What it settles | Plans |
|---|---|---|
| T1 Signalling, on Jio, Airtel, Vi and BSNL | Whether the caller's number survives a forward; whether P appears | A, B, E |
| T2 Jio to our number class | Whether Jio accepts the target | A, B, E |
| T3 Activation by operator and phone, especially iPhone + Jio | The onboarding instructions | A, B, E |
| T4 Timing: timer, decline, busy, call waiting, dual SIM | What we can promise users | A, B, E |
| T5 What the caller hears; how the agent classifies it | Greeting design | A, B, E |
| T6 Who pays | Disclosure to users | A, B, E |
| T7 Rule decay over a week | Health-check frequency | A, B, E |
| T8 Forwarded audio through VAD and STT | Tuning | A, B, E |
| T9 Forward loop | Loop guard | A, B, E |
| **M1 Manual merge on each operator** (new) | Hold wording, join tone, merge delay, audio on the merged leg | B, E |
| **M2 Transfer check on an API 37 phone** (new) | Whether network transfer works | B |
| **B1 Bluetooth bench test on Android and iPhone** (new) | Auto-answer, locked-phone dialling, audio quality through VAD and STT | C |
| **G1 Single-SIM gateway on Jio and Airtel** (new) | Whether voice works at all on VoLTE | D |

## 10. Recommended order

Each stage needs your yes. The companion document decides which stages are allowed.

| Stage | Do | Move on when |
|---|---|---|
| 0 | DevOps reads the firewall. A2P declaration confirmed with Plivo and a lawyer. Lawyer briefed | Both answered |
| 1 | F1, F2, F3 | Inbound and outbound calls still pass |
| 2 | T1–T6, M1 | Caller number survives on the main operators; forward is accepted |
| 3 | **Plan A pilot:** one or two business users with their own KYC | Rules hold through T7; no routing errors |
| 4 | Plan A product: F4–F8, onboarding, health check | Pilot users stay on it |
| 5 | Only if a lawyer names a basis: M2, B1, then a Plan B or Plan C prototype, on our own test numbers, for personal use only | Merge or Bluetooth is stable, and the legal answer is in writing |
| 6 | Backup carrier, then an operator block when users approach 40–85 | Quotes confirm the arithmetic |

Plan D stays out unless a customer wants a dedicated business mobile and a lawyer has cleared the single-SIM question. Plan E stays a test tool.

## 11. Decisions for you

| Decision | Recommendation |
|---|---|
| Is Plan A enough, given customers don't see P on outbound? | Yes for now. It is the only plan with a legal route for business calls |
| Business users or consumers first? | Business. A ₹200 number doesn't reach a consumer price, and the data-protection position is clearer |
| Who holds KYC on forward-target numbers? | Each business user for the pilot; a lawyer decides the rest |
| Fund a Plan B or C prototype? | Not until the merge test and a legal view are in |
| Close ports 5060 and 7443? | Yes, unless you choose Plan C or a router-based Plan D |
| Move off Plivo? | Not for the pilot. Add a backup carrier afterwards |
