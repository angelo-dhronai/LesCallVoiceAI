# Outbound calls that appear from a user's personal mobile number: mechanics, CCF's role, with and without Plivo (as of 2026-11-03)

Scope and status. Technical analysis only: TRAI and legality are set aside at the owner's request and covered by another track. **Nothing here was prototyped or tested; no call was placed.** Code was read read-only on branch `Bettercallimprov`, including uncommitted changes. Paths are repo-relative unless they start with `/etc`.

Tags: **[V]** verified (read in our code/config, or in the spec or vendor reference text). **[S]** vendor-claimed or press. **[I]** my inference. **[U]** unknown. 3GPP text was read through whatthespec.net, an unofficial auto-rendered copy of V19.0.0 (it states that 3GPP's server is authoritative), and through a summarising fetcher, so re-open a clause before quoting it formally. Notation: **P** = the user's personal mobile, **C** = the customer, **R** = a rented DID shown to customers, **D** = a rented DID used as a hidden rendezvous (R and D can be the same row).

Per-call rupee figures use Plivo's list price and one measured all-in call. They are illustrations, not quotes.

## Core truth: does CCF have any role in originating a call?

### Takeaway
No. Call forwarding acts only on calls addressed TO the served number, and both the IMS and the circuit-switched specs say in terms that the subscriber's ability to originate is unaffected. CCF cannot set a CLI, place a leg, add the agent to a live call or trigger anything. Its whole outbound contribution is the return path: when C calls P back, it can deliver that call to the agent.

### Cited Findings
- [V] CFU applies to "communications which are addressed to the served user's address"; CFB to those that "meet busy"; CFNR where "the connection is not established within a defined period of time"; CFNRc to "all incoming communications, when the user is not reachable" — [TS 24.604 V19.0.0 §4.2.1.2-5](https://whatthespec.net/friendlyspec/spec/24.604/19.0.0).
- [V] Each definition says the same thing about the outgoing direction: "The served user's ability to originate communications is unaffected by the CFU supplementary service" (likewise CFB, CFNR, CFNRc, and Communication Deflection) — [TS 24.604](https://whatthespec.net/friendlyspec/spec/24.604/19.0.0).
- [V] The only link to origination is an optional reminder "when the served user originates a communication" — [TS 24.604](https://whatthespec.net/friendlyspec/spec/24.604/19.0.0). The circuit-switched equivalent: "the ability of the served mobile subscriber to originate calls is not affected", with a NotifySS status on each outgoing call while forwarding is active — [TS 24.082](https://whatthespec.net/friendlyspec/spec/24.082).
- [V] TS 24.082 covers registration, erasure, activation, deactivation, interrogation and network invocation of CFU, CFB, CFNRy and CFNRc; CFU forwards "all incoming calls" "without being offered" — [TS 24.082](https://whatthespec.net/friendlyspec/spec/24.082).
- [V] Whether the calling party is told about the forward is a subscription option in both specs, which is consistent with the forwarding announcement reported on some Indian operators — [TS 24.082](https://whatthespec.net/friendlyspec/spec/24.082); [prior note, Exotel 2023](/home/angelo/lescall/research_notes/LesCall%20on%20personal%20phone%20numbers/india_call_forwarding.md) [S].

### Inferences
- **Exactly what CCF can contribute to an outbound workflow [I]:**
  1. A return call to P reaches the agent instead of going unanswered (patterns a and b).
  2. While the SIM is busy on an agent call, a second caller to P can reach the agent through the busy rule rather than failing. This depends on call waiting, which the earlier research left unverified.
  3. Timing choice: no-reply lets the user answer first.
- **What it cannot do [I]:**
  - The "phone's own CCF" in pattern (c) cannot help. Forwarding and deflection operate on an incoming session before it is established. Neither can move an established leg or add a party. That is ECT or conferencing, which are different services.
  - It does not affect the CLI C sees.
  - It cannot make a call "from" P when P's SIM is not involved.
- **CCF can harm outbound [I]:** any design where LesCall rings P (patterns d-variant and f) meets P's own forwarding. A no-reply or unconditional rule diverts LesCall's leg to D, and the agent ends up talking to itself on two metered legs. This is the same class as the existing test-call loop (`dashboard/server.py:5241-5249`, per `platform_audit.md`).

### Gaps
- Forward behaviour on Indian networks (CLI preservation, announcement, DID class) remains untested. See tests T1-T6 in the earlier report.

## Call-flow and limits of each pattern

### Takeaway
Only patterns where P's own SIM places the leg (b, c, d, e) show P, and of those only e (the box holding or bonded to the SIM) is clean for the agent: it looks like one more trunk, hangup propagates, and the existing identity path works with no change. c and d put a carrier conference between the agent and C, which adds a setup gap and breaks hangup and end-of-call symmetry. ECT would be cleaner but I found no public evidence that Jio, Airtel or Vi offer it. a and f show a rented number. g is refused by carriers.

### Cited Findings

**(a) Call-back return path. CLI shown first: R. Return number: P only if C was told it.**
```
Agent --Plivo/any trunk--> C            C sees R; call log = R
... later ...
C dials P --network CCF--> D --SIP--> FreeSWITCH --> agent (inbound)
```
- [V] Works on today's routing: D resolves the agent through `phone_numbers` (`conversation_call_bridge.py:351-367`); customer number on an inbound leg = caller id (`conversation_call_bridge.py:581-585`, `594-595`). Memory keys on owner plus last 10 digits (`:512-568`), so the outbound call on R and the return via P share one history under one owner, if the carrier preserves C's CLI [U, T1].
- [V] A return within 120 s matches `call_links` exactly and labels the leg outbound (`conversation_call_bridge.py:438,467-473,667`), and any tenant's dial in that window also flips the label through the time-only match. This is the known defect.
- [I] The catch: C only ever saw R, and a callback to R already reaches the inbound slot. So (a) has value only when P is already known to C, or the agent or a WhatsApp message gives out P.

**(b) Missed-call return. Real SIM ring, C's log shows P.**
```
User or app dials C from P, hangs up after N seconds   C's log = missed call from P
C dials P --CCF (no-reply/busy/unconditional)--> D --> agent (inbound)
```
- [V] Identity is clean because C is the true caller. Direction reads inbound, so the agent opens with the inbound greeting (`calling-agent/backend/app/voice/conversation.py:3760-3790`) and has no idea it "rang first" unless context exists.
- [I] Needs an "expected return" record that outlives the 120 s window (`OUTBOUND_DIAL_MATCH_WINDOW_S`, `conversation_call_bridge.py:438`). Within 120 s the existing match labels it outbound and picks the outbound agent, which is the wrong framing.
- [I] Weakest on conversion: a call happens only if C chooses to return it, and no response data exists. A one-ring pattern also resembles a known scam shape (from memory, not sourced). With a no-reply rule C waits through P's ring first (5-30 s per [Autobot via prior report](https://getautobot.app/blog/call-forwarding-codes) [S]).
- [I] Android automation is possible by `placeCall` then disconnect; iOS needs a human.

**(c) Trigger: conference or ECT from the handset.**

*Conference (manual):*
```
User: dial C on P  ->  Add call: dial D  ->  Merge  ->  mute
C <--> carrier conference <--> D <--SIP--> FreeSWITCH <--> agent
                 ^ user's handset stays in, muted
```
- [V] The Android APIs exist: `hold()`, `conference(Call)`, `mergeConference()`, gated by `CAPABILITY_MERGE_CONFERENCE` per call — [android.telecom.Call](https://developer.android.com/reference/android/telecom/Call). Merge results depend on carrier configuration; for example, `KEY_SUPPORT_IMS_CONFERENCE_EVENT_PACKAGE_BOOL` decides whether participants are returned — [CarrierConfigManager binding](https://learn.microsoft.com/en-us/dotnet/api/android.telephony.carrierconfigmanager.keysupportimsconferenceeventpackagebool?view=net-android-37.0).
- [S] Participant limits found are not Indian and not operator statements: 6 on Verizon VoLTE, 5 on 2G/3G per ASUS — [Verizon community](https://community.verizon.com/t5/iPhone-6/can-i-make-conference-call-with-my-iphone-6/m-p/778938); [ASUS](https://zentalk.asus.com:443/t5/faq/how-do-i-use-the-conference-call-feature/ta-p/225035). Jio's own conference apps are separate products — [Business Today](https://businesstoday.in/technology/reliance-jio-launches-jiogrouptalk-conference-calling-app-for-android-users/story/321994.html). Merge limits on native Airtel/Vi dialers: not found.
- [S] One old report says conference controls misbehaved with VoLTE on — [Pocketables, 2015](https://pocketables.com/2015/04/how-to-get-conference-call-management-options-back-in-android-5-1-on-the-nexus-6.html/amp). Too old to weigh much.

*ECT (the handset hands C to D and drops off):*
```
P--C active. Blind: P sends REFER(target=D) to its AS.
AS rewrites Refer-To to its own URI, stays in path. C's UE sends INVITE -> AS -> D.
C's UE sends BYE to P (non-assured), or P sends BYE after NOTIFY success (assured).
Result: C <--network--> D <--SIP--> agent. P's handset is free.
```
- [V] Roles and flow per spec: transferor UE issues REFER; the transferor's AS swaps Refer-To for an ECT URI and "remains in the signalling path", which allows target anonymisation; the transferee UE sends the INVITE and the BYE to the transferor; consultative adds Replaces — [TS 24.629 V19.0.0 §4.2.1, 4.5.2.1, 4.5.2.4, 4.5.2.5](https://whatthespec.net/friendlyspec/spec/24.629/19.0.0).
- [V] Non-conforming REFERs are handled "depending on operator policy", and announcements "if required by local policy" — [TS 24.629 §4.5.2.4.1.2.2](https://whatthespec.net/friendlyspec/spec/24.629/19.0.0).
- [V] Circuit-switched ECT needs both calls to exist, one held and the other active or alerting; an `SSErrorStatus` rejection covers no ECT subscription; the network connects the calls and drops the subscriber — [TS 24.091](https://whatthespec.net/friendlyspec/spec/24.091/19.0.0).
- [V] Android: `Call.transfer(Uri, boolean)` (assured or blind) and `Call.transfer(Call)` (consultative), citing TS 24.629 — [android.telecom.Call](https://developer.android.com/reference/android/telecom/Call); [.NET binding, API 37](https://learn.microsoft.com/en-us/dotnet/api/android.telecom.call.transfer?view=net-android-37.0).
- [V] AOSP has had an IMS ECT implementation since about 2020, and it reads a carrier flag, returning false if no config bundle is available — [AOSP Telephony commit](https://android.googlesource.com/platform/packages/services/Telephony/+/fdc7c236a7d8d2322ebd663f0dd33318000888a4%5E%21/); [merge](https://android.googlesource.com/platform/frameworks/opt/telephony/+/ce39aaea41%5E). One device overlay sets `carrier_allow_transfer_ims_call_bool=true` for one carrier — [device config](https://git.sim1222.com/sim1222/device_xiaomi_miatoll/commit/7418db2fcf110eb8616d06246eaae0f07e00e69a). The AOSP default was not established.
- [V] Vendor core products can omit parts: one VoLTE AS document says consultative ECT and out-of-dialog REFER are unsupported — [Rhino Sentinel VoLTE](https://docs.rhino.alianza.com/ocdoc/go/xref/sentinel-volte-documentation/4.1/sentinel-volte-architecture/explicit-communication-transfer). This shows ECT is not universal in deployed cores; it says nothing about Indian ones.
- [U] **ECT on Jio/Airtel/Vi: unknown.** Two targeted searches found no operator page, code or forum report of mid-call transfer; results were only about forwarding — [search result set, e.g. TelecomTalk](https://telecomtalk.info/?p=493353). That is absence of evidence, not proof of absence.
- [V] Charging: spec says only "classical charging models", that the AS may generate extra events for the new leg, and the transferee's AS may switch charging off when the transferee was the terminating party — [TS 24.629 §4.2.1, 4.5.2.4.2.1, 4.5.2.7.3](https://whatthespec.net/friendlyspec/spec/24.629/19.0.0). No Indian tariff found.
- [V] What C sees under CS ECT: a notification carrying the other party's number, subject to presentation — [TS 24.091](https://whatthespec.net/friendlyspec/spec/24.091/19.0.0).

**(d) App-triggered (companion Android app).**
```
Order B (DID first, recommended to test):
LesCall --FCM--> app: "call C, line D, token T"
app: placeCall(D)  FreeSWITCH answers; greeting MUST stay gated
app: placeCall(C)  D auto-held (network hold)
C answers -> app: conference(), setMuted(true)
app -> LesCall: "merged"  -> arm greeting

Order A (customer first): C answers -> C on hold while D connects -> merge. C hears a gap.

Variant C (server-originated, one less airtime leg):
LesCall originates to P from R with lescall_dest=C -> app auto-answers (never rings the user)
app: placeCall(C) -> merge -> "merged" -> arm greeting
```
- [V] Requirements: `ROLE_DIALER`, a dial pad and a full `InCallService`; emergency calls always use the preloaded dialer — [InCallService](https://developer.android.com/reference/android/telecom/InCallService). The app never touches audio, so the `CAPTURE_AUDIO_OUTPUT` block does not apply.
- [V] FCM: high-priority messages give a brief allowance, standby buckets cap them per day (excess downgraded to normal), and an app under battery restriction gets no FCM at all — [Android Developers Blog, 2018](https://android-developers.googleblog.com/2018/09/notifying-your-users-with-fcm.html) (old; re-verify). dontkillmyapp ranks Xiaomi, OnePlus and Samsung 5/5 on background kills, undated — [dontkillmyapp](https://dontkillmyapp.com/).
- [U] Whether the dialer role exempts a background `placeCall` was not confirmed. A search returned nothing on point.
- [S, generic consumer guides] Devices may require the first call held before a second is added, and merge failures are attributed to carrier limits — [example guide, low quality](https://www.invotech.dut.ac.za/?p=31662).
- [V] Machine-line corpus proves a real network hold announcement exists on our calls: "the person you are speaking with has put your call on hold, please stay on the line", repeating about every 45 s (`calling-agent/backend/app/voice/machine_line.py:21-28`). So a held party may hear words, not silence. Under Order A that is C, under Order B it may be the agent [I].

**(e) Hardware on the SIM.**
```
Bluetooth HFP:  FreeSWITCH --SIP--> box (HF role) --ATD via Bluetooth--> phone --SIM--> C
Single-SIM gateway: FreeSWITCH --SIP--> gateway/module --SIM (own)--> C
Audio returns the same way as SCO or modem PCM, then L16 8 kHz into the existing pipeline.
```
- [V] ESP32's stack exposes `esp_hf_client_dial()` and audio over (e)SCO; BlueALSA + oFono do HF on Linux; `chan_mobile` needs one adapter per phone and disconnects on range loss — [ESP-IDF](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/bluetooth/esp_hf_client.html); [BlueALSA](https://github.com/arkq/bluez-alsa/wiki/Using-BlueALSA-with-HFP-and-HSP-Devices); [Asterisk](https://docs.asterisk.org/Configuration/Channel-Drivers/Mobile-Channel/Mobile-Channel-Concepts/). Field reports are old and mixed — [FreePBX](https://community.freepbx.org/t/chan-mobile-working-well-with-iphone-4s/13494); [Asterisk](https://community.asterisk.org/t/chan-mobile-one-way-voice-with-galaxy-s5/70017). No commercial product was found (prior note).
- [V] Connection to FreeSWITCH: a gateway row with `register` mode and an `extension` param already renders (`shared/freeswitch_config.py:66-111`, lines 83-104), and `originate_cmd` always egresses through a named gateway (`shared/fs_originate.py:76-106`). `mod_gsmopen` is one interface per modem and beta — [FreeSWITCH docs](https://developer.signalwire.com/freeswitch/FreeSWITCH-Explained/Modules/mod_gsmopen_7143567).
- [S, anecdotes] Modules on Jio are shaky: an EC25-E failed voice on Jio while Vodafone worked; "ghost calling" where the modem shows ringing but the destination never rings; firmware-dependent IMS — [Quectel forum](https://forums.quectel.com/t/eg25-g-problem-with-jio-sim/5277); [Quectel forum](https://forums.quectel.com/t/looking-for-a-4g-usb-lte-dongle-that-supports-voice-circuit-switched-voice-or-volte-pcm-audio-audio-over-usb-ideas-and-suggestions/51403). VoLTE is optional on EC25-E — [Quectel](https://www.quectel.com/product/ec25.htm). No confirmed working Jio VoLTE gateway setup was found.
- **Second SIM with the same number:**
  - [S] Airtel users report an eSIM deactivates the physical SIM (prior note). No general twin-SIM voice product was found at Airtel, Jio or Vi.
  - [S] The one same-number, two-device service found is paired smartwatch eSIM: JioEverywhereConnect (one number on iPhone and Apple Watch), Airtel on Infinity plans, Vodafone Idea — [Deccan Chronicle 2018](https://www.deccanchronicle.com/amp/technology/gadgets/240418/airtel-jio-4g-lte-apple-watch-series-3-india.html); [Digit](https://www.digit.in/how-to/mobile-phones/how-to-activate-jio-esim-and-use-the-same-esim-on-both-iphone-and-apple-watch-65407.html); [GSMArena](https://m.gsmarena.com/vodafone_india_customers_can_now_use_their_mobile_number_with_their_apple_watch-news-43737.php). All sources are 2018-era; current terms unverified.
  - [I] That shows operators can provision one MSISDN on two subscriptions, but as an entitlement bound to a paired watch, not as something a gateway can obtain.
  - [I] A secondary SIM on the same account has a different number, so its CLI is that SIM's, not P. It is legitimately the subscriber's own SIM, but does not meet the objective.

**(f) Click-to-call bridge.**
```
LesCall --R--> P rings, user answers
LesCall --R--> C rings; C sees R
Bridge C <-> P/agent inside FreeSWITCH
```
- [S] Exotel documents this shape: "Exotel calls the agent first (Leg A)... Customer sees ExoPhone as caller ID (not agent's personal number)" — [Exotel Making Outgoing Calls, search extract](https://developer.exotel.com/docs/call-support/call-features/outgoing-calls).
- [V] We have no bridge primitive today (`platform_audit.md` Q6: no `uuid_bridge`, `uuid_transfer` or bridge application found), though `mod_conference` is loaded (`/etc/freeswitch/autoload_configs/modules.conf.xml:55`).
- [I] For an AI agent this pattern is pointless unless the human is supervising. C still sees R, so it does not move toward the goal.

**(g) Presenting P over a trunk: what is in the message and why it fails.**
- [V] FreeSWITCH puts `origination_caller_id_number` into the INVITE; generated gateways set `caller-id-in-from=true` (`shared/freeswitch_config.py:106`), and `originate_cmd` sets no PAI or Privacy (`shared/fs_originate.py:87-106`). So P would appear as the From user part. PAI would be one more channel variable.
- [V] The platform does not block it: `caller_id` is just the digits of the number row (`dashboard/server.py:4824-4830`), and the comment states the carrier is the enforcer (`dashboard/server.py:4803-4807`).
- [S] Plivo: "Plivo numbers are required as caller ID for all outbound calls. Using non-Plivo numbers results in an `unknown_caller_id` hangup" — [Plivo SIP technical specs](https://www.plivo.com/docs/sip-trunking/concepts/technical-specifications.md). Verified caller ID is "Not applicable" for India — [Plivo](https://www.plivo.com/docs/voice/concepts/verified-caller-id.md) (prior report).
- [S] Edesy (Indian SIP provider): "The SIP `From` number must be a number you own on Edesy"; others get `403 Caller ID Not Authorized` — [Edesy docs](https://edesy.in/docs/sip-trunking/getting-started.md).
- [S, search extract only] Exotel documents a "Custom Caller ID" requiring prior verification and warning that unauthorised use breaks TRAI rules and its terms — [Exotel](https://developer.exotel.com/docs/advanced-config/custom-caller-id). My fetch of that URL returned generic content, not the feature text, so whether it covers a personal mobile on Indian routes is **unconfirmed**. It is the only lead on "CLI after proof".
- [S, anecdote] AWS forum users describe a proof-of-ownership support request for Amazon Connect in India — [re:Post](https://repost.aws/questions/QU1TZ14_u_QLm_Iu1Uw4H8qw/how-can-use-amazon-connect-to-india-customer). A Feb 2026 Retell thread asking about an Airtel mobile as CLI never got a yes or no — [Retell](https://community.retellai.com/t/enable-verified-caller-id-indian-airtel-number-for-outbound-calls/789.md). A 3CX user reports an Indian provider tying CLI to the assigned number — [3CX](https://www.3cx.com/community/threads/outbound-caller-id-problem.126553/post-599232).
- [I] **Why it is rejected, at a high level:** a carrier's edge checks From/PAI against the numbers allocated to that trunk account. A mobile number belongs to the range of the mobile operator whose core authenticates the SIM, so a different carrier's trunk has no authority to assert it. The call is refused or rewritten. On a real SIM call the network derives the identity from the authenticated subscription, which is why the two cases differ fundamentally. That is as far as this note goes on this route.

### Inferences
- **Legitimately the subscriber's own SIM placing its own call:** b, c, d, e (HFP with the phone's SIM, or a gateway holding the user's own single SIM). ECT starts as such a call. **Not:** a, f (rented number) and g (a trunk asserting a number it was not allocated).
- **Hangup asymmetry under conference, the most important new finding [I]:** FreeSWITCH only controls the D leg.
  - When the agent ends the call (`_send_hangup_request`, `conversation_call_bridge.py:2123`, or `hangup_immediate`, `:2039-2049`), only D drops. C stays connected to a muted handset in silence unless the app also ends the call.
  - When C hangs up, FreeSWITCH sees nothing on D, and the agent keeps talking until the silence watchdog (`calling-agent/backend/app/voice/pipeline.py:725-737`) unless the app reports it.
  - So c and d need a bidirectional control channel on every call. ECT and e do not have this problem.
- **Order B is likely better for C and harder for us [I]:** C never hears hold on answer, but it requires a held D leg with the greeting gated. Order A needs D to connect while C waits. Our dialplan alone contributes at least 1 s (`answer`, `sleep 1000` in `/etc/freeswitch/dialplan/public.xml`), plus ESL and WS setup, then the merge. The gap has not been measured.
- **Variant C is a useful refinement [I]:** server-originated leg means the user pays one airtime leg instead of two, and `lescall_dest` works unchanged (see platform section).

### Gaps
- Hold and merge duration, audio during hold, answer-detection accuracy on IMS (the prior note warns a call can show active before the person answers), and whether Indian networks allow automated merge. All need a handset test.
- How many Indian handsets run API 37: no figures found.
- iPhone behaviour for ATD over HFP on a locked phone is from general knowledge, not verified.

## Each pattern with Plivo versus without Plivo

### Takeaway
Plivo is not the deciding variable for any pattern. a, b, c, d and f need some rented DID, from Plivo or any carrier. e needs no trunk at all. g fails on every carrier evidence was found for. "Without Plivo" changes who bills D or R, not what C sees.

### Cited Findings
- [S] Plivo India: ₹0.38/min domestic outbound (API) and inbound, 30 s pulse, ₹200/month DID. The page does not list Zentrunk rates or split mobile from landline — [Plivo pricing](https://www.plivo.com/voice/pricing/in/). One measured inbound call matched (₹1.90 for 300 s) per the prior report.
- [V, one call] All-in AI cost was ₹2.99 per talk-minute including Plivo on call 972 (project record, not re-derived; caching work since may have lowered it).
- [S] Other Indian SIP sources exist: Edesy sells numbers and minutes — [Edesy](https://edesy.in/docs/sip-trunking/getting-started.md). Exotel bills a pulse "typically 60 seconds in India" — [Exotel docs](https://developer.exotel.com/docs/advanced-config/custom-caller-id).
- [S] Jio's text excludes BSNL numbers as a forward target (prior note), which matters when choosing a non-Plivo D.

### Inferences
Comparison, all [I] except where a rate or a code reference is cited above. "Telephony" excludes the AI cost, which I take as roughly ₹2.6/min (₹2.99 less ₹0.38, one call only). Airtime is the user's plan; whether unlimited plans cover these legs is [U].

| Pattern | Rented number needed | C sees | Telephony per talk-min, via Plivo | Without Plivo: other carrier | Without Plivo: no trunk | Concurrency | iPhone | Unattended | Reliability view |
|---|---|---|---|---|---|---|---|---|---|
| a Return path | R + D (can be one) | R; P only on the return | ₹0.38 out, later ₹0.38 in; D ₹200/mo | Same; check forward-target class | Outbound needs a line; forward target could be a gateway SIM (one call) | Trunk limit; return calls not limited by SIM (forwarded) | Yes (codes; Live Voicemail off [S]) | Yes | Good if forwarding holds; hiRobin's failure claims [S] apply |
| b Missed-call return | D only | P (missed call) | ₹0 for ring; ₹0.38 in on return | Same | Forward to gateway SIM | Ring: one at a time; returns many | Ring by hand only | Android yes; iOS no | Depends entirely on C calling back |
| c Manual conference | D | P | ₹0.38 in; 2 airtime legs | Same | No D means no agent, unless a gateway SIM stands in | 1 per SIM | Yes, by hand | No | Human-assisted; merge untested in India |
| c ECT | D | P, then possibly a connected-number update | ₹0.38 in; airtime unknown | Same | As above | Could free the SIM after transfer [U] | No API found | Android only, if supported | **Unknown on Indian networks** |
| d App + conference | D or R | P | ₹0.38 (variant C out, order A/B in); 1-2 airtime legs; pre-merge time billed | Same | As above | 1 per SIM; phone occupied | **No** | Partly: phone on, in coverage, app alive, not in use | Weak: FCM, OEM kills, dialer role, merge, control channel |
| e HFP box | **None** | P | ₹0 to LesCall; 1 airtime leg | n/a | **This is the no-trunk case** | 1 per SIM (HFP also 1 per adapter) | Yes in principle | Yes, near the phone | Unproven: old reports, no product |
| e Own-SIM gateway | **None** | P | ₹0 to LesCall; plan or SIM minutes | n/a | **This is the no-trunk case** | 1 per SIM | n/a (SIM not in phone) | Yes; P no longer rings on the phone | Hardware exists; Jio VoLTE reports poor [S] |
| f Click-to-call | R | R | ₹0.76 (two legs) | Same | Not possible | Trunk limit; 1 human | Yes | No (human must answer) | Good, but wrong CLI |
| g Trunk CLI | R-class trunk | Rejected or rewritten | n/a | Rejected per Edesy; Exotel lead unconfirmed | n/a | n/a | n/a | n/a | Do not design around it |

Illustrative 3-minute call: pattern e costs about ₹7.8 in AI, ₹0 Plivo; pattern d about ₹7.8 + ₹1.14 + pre-merge time. Rental, hardware and support are extra. Figures rest on one measured call.

### Gaps
- Zentrunk per-minute rate for India, Airtime treatment of a leg to a DID, and hardware price for a single-port VoLTE device or an HFP box were not established. Earlier dealer listings (₹20,000 for a 2-port) are not a per-unit answer.

## Platform mapping: what the current code does and what each pattern needs

### Takeaway
A leg that arrives on D with P as caller is misidentified today in both possible ways: as the owner's own number, or as our DID. The fix is small if identity is injected at one point (`_resolve_call_routing`), or unnecessary with server-originated legs or a gateway, because `lescall_dest` already carries the customer. The real work is elsewhere: there is no per-line limit, no device channel, the greeting gate assumes "first frame = customer present", and machine-line logic can misread a hold or a join tone as a mailbox. No FCM or device registry exists.

### Cited Findings

**Outbound identity and origination [V]**
- `_outbound_identity` returns `caller_id`, `gateway_name`, `agent_slug` from `phone_numbers` with `direction` in both/outbound (`dashboard/server.py:4781-4830`). `originate_cmd` builds the dial string with `lescall_dest` = the dialled digits (`shared/fs_originate.py:87-106`). The destination and `lescall_dest` are the same parameter, so a leg to P that is really for C cannot be expressed yet.
- Dial origins all funnel through that identity: callback `dashboard/server.py:4278,4336`; follow-ups `4504,4606` (`autodial.due_candidates`, `blocked_reason` at `4450,4482`); manual and scheduled `5129-5141`; scheduler `scheduling.claim_due` at `5950`; campaign `5522,5660`.
- `_do_dial` awaits `_fs_esl_exec(originate, timeout=55)` and buckets the result through `_parse_fs_result` (`dashboard/server.py:5138-5145`). A device has no synchronous result.

**Concurrency [V]**
- No per-line limit exists. The only cap is per owner, `max_concurrent_calls` in `entitlements` (`shared/billing/entitlements.py:509-516`), reachable only through billing checks.
- Campaigns: `_campaign_tasks[camp_owner]`, one sequential loop per owner. The loop moves on when originate returns, then sleeps `delay_secs` (`dashboard/server.py:5660-5684`); I saw no wait for hangup in those lines. Follow-ups, callbacks, scheduled and manual dials run independently of it.
- A grep across `dashboard`, `shared` and `calling-agent/backend/app` for firebase, fcm_, device_token, push_token found nothing. **There is no device channel.**

**Identity on a leg arriving at D with caller = P [V from code, not exercised]**
- `_resolve_call_routing` calls `_lookup_recent_outbound_dial(caller_number, dialed_number or did)` (`conversation_call_bridge.py:611-697`). `wanted` = {P, D}, while the `call_links` row holds C (`dashboard/server.py:747-800`), so there is no exact match.
- With any dial in the last 120 s, by any tenant (no owner filter, `conversation_call_bridge.py:467-473`): direction = outbound, `identity_dial` = None, and `_customer_number` returns D's digits (`:581-604`). **The customer is stored as our own DID.**
- With no recent dial: direction = inbound and customer = P. **Every customer's call is stored against the owner's mobile**, with memory, contact auto-creation and WhatsApp (`:512-568`, `:1566-1603`, `shared/whatsapp/stage.py:239-280`) all pointing at the user.
- With `dialed_number` = C the existing path is correct: `_dial_matches_phone` is true, direction is outbound, the slug comes from the dial record, and `_customer_number` returns C (`:399`, `:581-590`, `:625-684`). That is why injecting C there is sufficient.

**Opening, first-audio gate, two beats [V]**
- The gate is `pipeline.py:1170-1183`: it polls `_pushed_frames` for up to 5 s, then plays anyway ("call may not be answered yet"). FreeSWITCH delivers frames from answer, so on D **the greeting would fire as soon as D connects, before C is present.** This is true for all of c and d.
- `_call_start_ts` drives `_in_pickup_phase` (`pipeline.py:3259-3264`, `CONNECT_WINDOW_S=12` at `:433`). Time spent on hold or ringing eats that window, so the merged call loses pickup handling. `_opening_ts` and `EARLY_HELLO_WINDOW_S=25` (`conversation.py:1487`, `2731-2755`) bound the onset intro the same way.
- `generate_opening` branches on `call_direction`, and the two beats depend on it (`conversation.py:3760-3790`). The correct label is therefore not cosmetic. A "hello" spoken over the opening is handled by onset absorption (`conversation.py:4384`).
- `_skip_greeting` and `_greeting_done` exist as pipeline flags, used for reconnect and billing refusal (`pipeline.py:1165`, `conversation_call_bridge.py:1030-1033`). A gated greeting would be a new sibling: wait for an external "merged" event, then reset the timers.

**Machine-line and audio events on a merged or held leg [V for the code; interaction is I]**
- HOLD wording arms a wait with silence; the episode ceiling is 90 s (`machine_line.py:21-28`, `conversation.py:1511`, `3416-3440`). An outbound leg on hold may therefore hear the operator's announcement and go quiet correctly, which is good, but then close at the ceiling if C has not answered by then, which is fine within a 45 s ring.
- `machine_audio_close_allowed` permits a beep close only when outbound and no person has spoken (`conversation.py:3330-3336`), and `pipeline.py:3533-3549` requires 1.5 s of prior audio. A confirmed beep is 3-12 hops (300-1200 ms) at a steady frequency (`audio_events.py:48-57`). **So a hold announcement (which counts as speech) followed by a carrier conference join tone meets the literal conditions of a mailbox beep, and would close the call immediately.** No Indian join tone has been observed; this is a hazard to test.
- `_human_spoke` is set only when a person's line passes the gate (`conversation.py:3478,3510`). Screener and voicemail handling work on the customer's audio as usual, because the mix contains the customer. C's iOS Call Screening still has to be answered by the agent, and in Order A the app would treat the screener's answer as C's answer.
- `DEAD_MEDIA_S=5` closes on a constant-value stream (`pipeline.py:742-755`) and `SILENCE_REPROMPT_S=6` re-asks (`:725`). A held leg with no or flat media could trigger either. Both need suppressing until the "merged" event.

**VAD and echo [V for design; I for effect]**
- Echo handling is time and text domain: `TTS_ECHO_COOLDOWN_MS=400` and playback shadow with `SHADOW_ECHO_TEXT_RATIO` (`pipeline.py:282-310`), not an adaptive filter. Telephony assumes separate paths.
- Thresholds and FSMN are tuned on 8 kHz upsampled telephone audio (`dhron-voice-interface-report.md:380-391,1132-1140`, per `platform_audit.md`).

**Recording and metering [V]**
- The two-party recording is captured on the channel and uploaded at call end (`conversation_call_bridge.py:1240-1268`). On D that is the conference mix plus agent, and it starts at D's answer, so pre-merge audio is included.
- `open_call` keys on `conn_id`, owner, direction, phone; answered_at is first media (`shared/usage_metering.py:96-118`, `conversation_call_bridge.py:1320-1330`). `billsec_final` comes from the CDR (`usage_metering.py:14-24`), so on D **ring and hold time bill as talk time**, and a no-answer attempt under Order B is a billable call of up to about 45 s.

**Rendezvous for FreeSWITCH variables [V]**
- WS path carries caller, did, campaign, slug, dest (`socket_server.py:548-559,764-798`). An inbound leg has no `lescall_dest` or slug. `socket_server.py` runs under system Python, which is a reason to do the DB lookup in the bridge rather than there (per `CLAUDE.md`).

### Inferences
**VAD or echo verdict [I]:** a merged conference should not inherently break them. A carrier mixer normally removes each participant's own signal, so the agent should not hear its own TTS from the mixer, leaving only echo from C's handset, as on any call. The risks are different:
- the user's unmuted mic adds a third voice, so `setMuted` must be enforced and watched;
- mixer gain or gating could shift the absolute levels our VAD relies on;
- double transcoding could harm FSMN, unmeasured;
- join and hold tones could reach audio events.

HFP or a gateway has no mixer. SCO adds its own narrowband and handset processing.

**Changes per pattern [I]:**

| Change | a | b | c manual | d conference | d variant C | ECT | e |
|---|---|---|---|---|---|---|---|
| Forwarding product (prior report Group A/B) | Yes | Yes | No | Optional | Optional | Optional | Optional |
| Direction fix `:467-473,667` | Yes | Yes | Yes | Yes | No | Yes | No |
| Expected-return record | Optional | Yes | No | No | No | No | No |
| `personal_lines` table plus per-line lease | No | No | Yes | Yes | Yes | Yes | Yes |
| Inject C in `_resolve_call_routing` by claim on (P, D) | No | No | Yes | Yes | No | Depends on CLI [U] | No |
| `originate_cmd` split: `dial_to` vs `lescall_dest` | No | No | No | No | Yes | No | No |
| Greeting and timers gated on "merged" | No | No | Yes (user tap) | Yes | Yes | No or brief | No |
| Suppress watchdogs and beep close until merged | No | No | Yes | Yes | Yes | Brief | No |
| Bidirectional hangup via device | No | No | Yes | Yes | Yes | No | No |
| Billable start = merged | No | No | Yes | Yes | Yes | Maybe | No |
| New gateway type and health | No | No | No | No | No | No | Yes |
| Android default-dialer app, FCM | No | Optional | Optional | Yes | Yes | Yes | No |

**Proposed new surface, none built [I]:**
- `personal_lines(owner, number, sig_digits UNIQUE, mode[device|gateway|hfp], did_id, gateway/trunk ref, status, verified_at, last_seen_at)` and a `line_leases(line_id, conn or dial id, expires_at)` row taken with an advisory lock, like the `_billing_claim_guard` pattern. Never a `phone_numbers` row for P (prior report). A busy line must be a **deferral that does not spend an attempt** in `autodial.blocked_reason`, and campaigns must await a lease rather than failing contacts as no-answer.
- For gateway/HFP: a trunk row plus `phone_numbers` row for the device's identity works mechanically, but sentinel would then probe and may ring or kill it (`shared/sentinel.py:449-473`), and `direction='both'` is what selects it. Needs an exclusion.
- Device API: `POST /api/device/register` (FCM token, user auth), `POST /api/device/calls/{id}/events` (`d_connected`, `ringing`, `answered`, `merged`, `merge_failed`, `customer_ended`, `taken_over`, `busy`), and a server-to-device `end_call` and `dial` push. Map events onto `_parse_fs_result` buckets so `_update_contact_after_call` and campaign counters stay valid. A new 9004 request type (`socket_server.py:1073-1103`) for `arm_greeting`.
- Security: a device claim keyed on caller = P is only as good as the `:5080` gate, because `auth-calls=false` with no ACL is forgeable (`/etc/freeswitch/sip_profiles/external.xml:58`, `platform_audit.md`). A one-shot token in DTMF from `playDtmfTone` would be a second factor at the cost of an audible tone in the setup.
- Record: `calls.via_line`, `/v1` field. `direction='outbound'`, `phone=C`.

### Gaps
- Whether FreeSWITCH sees flat, silent or no RTP on a leg held by the handset is unknown, and decides whether the watchdogs fire.
- Whether a merged conference delivers P or C as the caller on an ECT'd leg. Per spec the INVITE comes from the transferee with Referred-By carrying the transferor, so C's number on D is plausible [I, untested]. The platform must accept either.
- Whether Indian conferences play a join tone: no source.

## Which pattern gets closest to what the owner wants: C sees P and can call P back

### Takeaway
Ranked by that outcome: e (HFP, then own-SIM gateway), d, c, b, a, f, g. Only e combines a true P on every outbound call with unattended operation and no conference. It is also the only one with a plausible iPhone story, but it is one line per phone with hardware per user, and no product was found. Add conditional forwarding to any of them and the callback reaches the agent; without it, the callback rings the user.

### Cited Findings
- Supporting evidence is in the sections above. iOS has no third-party dialer control; Truecaller's iOS recording needs the user to merge by hand because of CallKit limits — [TechCrunch](https://techcrunch.com/2024/02/25/truecaller-brings-call-recording-and-transcription-to-india) [S].

### Inferences
| Rank | Pattern | C sees P? | C's callback reaches | Agent-call quality | Attended? | Main obstacle |
|---|---|---|---|---|---|---|
| 1 | e HFP box | Yes, always | Phone; agent with CCF | Clean leg, narrowband | No | No product; box per phone; Bluetooth range; user's phone audio route is taken over; 1 line |
| 2 | e own-SIM gateway | Yes, always | The gateway, not a phone, since the SIM left it. The agent can answer there, and the user's phone no longer has the number | Clean leg | No | The user loses P on the handset, so this fails "personal" unless it is a dedicated business number; modem/VoLTE quirks |
| 3 | d app + conference (ECT if it exists) | Yes | Phone; agent with CCF | Conference artefacts, hold gap | Partly | Android only, default dialer, phone occupied, FCM, carrier support |
| 4 | c manual merge | Yes | Same | Same, plus human in loop | Yes | Not an agent workflow; useful as the first physical test |
| 5 | b missed call | Yes, as a missed call | Agent with CCF | Clean | Android: no; iOS: human | C must act; no outbound conversation |
| 6 | a return path | No, R; P only if told | Agent with CCF | Clean | No | Does not meet the outbound half |
| 7 | f click-to-call | No, R | R | Clean | Human needed | Does not meet either half |
| 8 | g trunk CLI | No | n/a | n/a | n/a | Refused |

Reliability cautions apply to every row, since none has been built. The lowest-cost first experiment, needing no code beyond header logging, is a manual merge (c) on each of Jio, Airtel and Vi to a spare DID, listening for hold wording, a join tone and merge delay. In the same session, check the Android dialer's Transfer button or API capability on an API 37 handset. That single session would settle most of the [U] items above for c, d and ECT. Prior-report rules on the spare DID apply (register the row, no agent bound).

### Gaps
- No user-tolerance data for a hold gap on answer, and no return-rate data for missed calls in India.
- Whether the outbound-commercial posture differs among these patterns is left to the legal track. Its earlier finding (`reports/LesCall on personal phone numbers.md`) is that the rules attach to the sender regardless of how a SIM is connected, so the mechanics here should not be read as clearance.
