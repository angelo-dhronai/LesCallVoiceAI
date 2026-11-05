# Device-side ways to attach a cloud AI voice agent to a person's own phone and SIM (Android, iOS, hardware bridges), as of 2026-11-03

Scope note: desk research only. Nothing here was tested on a handset, a carrier or a Bluetooth stack. Android reference pages were read at their current state (they already include API 37, Android 17). Apple's feature-availability page was read at its iOS 27 state. Items marked "Inference" are my reasoning, not sourced fact.

## Can a third-party Android app get and inject cellular call audio?

### Takeaway
No, not through any public API. Capturing call audio is limited to pre-installed privileged apps holding `CAPTURE_AUDIO_OUTPUT`, and the intercept-and-play path (`enterBackgroundAudioProcessing`) is `@SystemApi`. A third-party app can fully control the call as the default dialer (answer, place, hold, conference, mute, and on API 37 transfer), but it never touches the audio samples.

### Cited Findings
**Capture**
- `VOICE_CALL`, `VOICE_DOWNLINK` and `VOICE_UPLINK` all require `CAPTURE_AUDIO_OUTPUT`, which the docs say "is reserved for use by system components and is not available to third-party applications." `REMOTE_SUBMIX` has the same restriction. — [MediaRecorder.AudioSource](https://developer.android.com/reference/android/media/MediaRecorder.AudioSource)
- Android's audio-sharing rules for "Voice call + ordinary app": "The call always receives audio"; an app can capture audio if it is an accessibility service; an app can capture the voice call only "if it is a privileged (pre-installed) app with permission CAPTURE_AUDIO_OUTPUT", using `VOICE_UPLINK`/`VOICE_DOWNLINK` and/or `AudioDeviceInfo.TYPE_TELEPHONY`. — [Sharing audio input](https://developer.android.com/media/platform/sharing-audio-input)
- Android 9 change: apps not in the foreground or running a foreground service receive silence when capturing. — [Sharing audio input](https://developer.android.com/media/platform/sharing-audio-input)

**The private intercept path (the mechanism a system screener would use)**
- `Call.enterBackgroundAudioProcessing()` is `@SystemApi @hide`: after Telecom sets `STATE_AUDIO_PROCESSING`, "your app may setup the audio paths with the audio stack in order to capture and play audio on the call stream." It "can only be called by the default dialer app." — [AOSP Call.java](https://raw.githubusercontent.com/aosp-mirror/platform_frameworks_base/main/telecomm/java/android/telecom/Call.java)
- `STATE_AUDIO_PROCESSING` means the call is "active with the network, but the audio from the call is being intercepted by an app on the local device", invisible to the user except for a persistent notification. `exitBackgroundAudioProcessing(true)` moves the call to `STATE_SIMULATED_RINGING`, so the phone rings after screening. — [AOSP Call.java](https://raw.githubusercontent.com/aosp-mirror/platform_frameworks_base/main/telecomm/java/android/telecom/Call.java)
- `CallResponse.Builder.setShouldScreenCallViaAudioProcessing` is `@SystemApi @hide`, requires `CAPTURE_AUDIO_OUTPUT`, and "will only be honored if the CallScreeningService shares the same uid as the system dialer app." — [AOSP CallScreeningService.java](https://raw.githubusercontent.com/aosp-mirror/platform_frameworks_base/main/telecomm/java/android/telecom/CallScreeningService.java)
- The public Builder has no audio method. It offers only disallow, reject, rejected-as-missed (37.2), silence, skip call log, skip notification and composer attachments. — [CallResponse.Builder](https://developer.android.com/reference/android/telecom/CallScreeningService.CallResponse.Builder)

**Android 17 (API 37) additions**
- Android 17 raised the API level to 37. — [Wikipedia: Android 17](https://en.wikipedia.org/wiki/Android_17)
- API 37 made the use case readable publicly: `Call.getAudioProcessingUseCase()` and `AUDIO_PROCESSING_USE_CASE_ASK_TO_HOLD / CALL_SCREENING / VOICEMAIL / UNKNOWN`. The public reference exposes `STATE_AUDIO_PROCESSING` (since API 30) but does not list `enterBackgroundAudioProcessing` (checked by text search of the fetched page). A third-party dialer can see that a call is being processed, and cannot start processing. — [android.telecom.Call](https://developer.android.com/reference/android/telecom/Call)
- `Connection.setAudioProcessing` is marked ApiSince=37. Its remarks say it applies only to `PROPERTY_IS_EXTERNAL_CALL`. This comes from an auto-generated .NET binding, not Google's page. — [Microsoft binding](https://learn.microsoft.com/en-us/dotnet/api/android.telecom.connection.setaudioprocessing?view=net-android-37.0)
- **New in API 37: `Call.transfer(Uri targetNumber, boolean isConfirmationRequired)` (blind or assured) and `Call.transfer(Call toCall)` (consultative), referencing 3GPP TS 24.629.** — [android.telecom.Call](https://developer.android.com/reference/android/telecom/Call)
- Android 17 reportedly routes calls started via Telecom Jetpack APIs to a premium network slice, and the first beta integrated VoIP call history. Both are VoIP-side and neither adds cellular audio access. These are secondary press sources. — [Digital Trends ES](https://es.digitaltrends.com/celular/android-17-arreglar-llamadas-videollamadas-lentas/); [ITmedia](https://www.itmedia.co.jp/mobile/spv/2602/16/news057.html)

**What the roles do give**
- `ROLE_DIALER` requires handling `ACTION_DIAL` with a dial pad UI and fully implementing `InCallService` with incoming and ongoing call UI. Emergency calls always use the preloaded dialer. Calls are placed with `TelecomManager.placeCall(Uri, Bundle)`. The audio surface on `InCallService` is routing (`requestCallEndpointChange`) and `setMuted`, with no PCM stream. — [InCallService](https://developer.android.com/reference/android/telecom/InCallService)
- `Call` exposes public `answer(int)`, `hold()`, `conference(Call)`, `mergeConference()`, `swapConference()` and `playDtmfTone(char)`, all since API 23 except where noted. — [android.telecom.Call](https://developer.android.com/reference/android/telecom/Call)
- `TelecomManager.acceptRingingCall()` is deprecated since API 29: "Companion apps for wearable devices should use the InCallService API instead." It needs `ANSWER_PHONE_CALLS` or `MODIFY_PHONE_STATE`. `endCall()` was deprecated the same way. — [TelecomManager](https://developer.android.com/reference/android/telecom/TelecomManager)
- `CallScreeningService` needs `ROLE_CALL_SCREENING` (a single user-chosen app) and must respond within 5 s. It can block, silence or identify calls and has no audio or answer action. — [CallScreeningService](https://developer.android.com/reference/android/telecom/CallScreeningService)
- `CallRedirectionService` (`ROLE_CALL_REDIRECTION`) lets an app modify or cancel outgoing calls, for example rerouting them over VoIP. — [Telecom overview](https://developer.android.com/develop/connectivity/telecom)
- Core-Telecom Jetpack is for "stand-alone calling apps" (self-managed VoIP) and manages their audio routing and focus. It is not a way into SIM calls. A replacement phone app "must not have any calling capability, and must consist solely of the user interface for calling." — [Telecom overview](https://developer.android.com/develop/connectivity/telecom)

**The AccessibilityService workaround and Play policy**
- Current Play policy: "The Accessibility API is not designed and cannot be requested for: remote call audio recording." — [Play: Permissions and APIs that Access Sensitive Information](https://support.google.com/googleplay/android-developer/answer/9888170?hl=en)
- The ban took effect 11 May 2022, and Truecaller dropped recording as a result ("we are unable to offer call recording any longer"). — [TNW, Apr 2022](https://thenextweb.com/news/truecaller-call-recording-google-play-policy); [Business Today, Apr 2022](https://www.businesstoday.in/technology/story/google-to-stop-android-apps-from-recording-calls-soon-330700-2022-04-21)
- Cube ACR still works off-Play through an accessibility "App Connector". Its FAQ tells users to enable it in Accessibility settings, set the audio source to "voice recognition (software)" or "voice communication (software)" on Android 10-14, and warns that the workaround "will not work for calls using a headset" (Android 9-14). — [Cube ACR FAQ](https://cubeacr.app/faq.html)
- Cube's Helper is distributed via Galaxy Store, AppGallery, Amazon, Aptoide and APK, because the Play version can no longer ask for the connector. — [Cube ACR FAQ](https://cubeacr.app/faq.html); [Cube Helper page](https://cubeacr.app/helper.html)
- Snippet only (the page returned 403 when fetched): Google "labeled call recording as a type of spyware in its developer policy." — [Yahoo Tech](https://tech.yahoo.com/articles/google-makes-difficult-record-calls-155834200.html)

**Play permissions and foreground services**
- Call Log permissions require being "actively registered as the default Phone or Assistant handler", and the app must stop using them when it no longer is. — [Play policy](https://support.google.com/googleplay/android-developer/answer/9888170?hl=en)
- July 2026 policy preview: account verification via phone call is no longer a permitted `READ_CALL_LOG` use. Exceptions are still subject to review and to the existing limits on call recording. — [Play policy preview](https://support.google.com/googleplay/android-developer/answer/16558241?hl=en)
- Apps targeting Android 14+ must declare a foreground service type (`phoneCall`, `microphone`, etc.) with matching permissions. — [Android 14 FGS types](https://developer.android.com/about/versions/14/changes/fgs-types-required?hl=pt)
- Play requires a declaration with a description and demo video for each type. — [Play FGS requirements](https://support.google.com/googleplay/android-developer/answer/13392821)

**OEM battery killers**
- dontkillmyapp ranks vendors by background-kill severity: Xiaomi #2 (5/5), OnePlus #3 (5), Samsung #4 (5), Oppo #8 (4), Vivo #11 (3), realme #12 (3), stock AOSP/Pixel 0. The page is undated, so treat it as possibly stale. — [dontkillmyapp](https://dontkillmyapp.com/)

### Inferences
- Accessibility-based capture only lets an app keep the **mic** open during a call (`VOICE_RECOGNITION`/`VOICE_COMMUNICATION` sources). The far party is then picked up only by leakage or speakerphone. This is supported by Cube's own failure mode ("can't hear the interlocutor on Bluetooth/headset") and by no downlink source being permitted. It is unusable for an agent: STT would get echo-degraded audio.
- **Injection:** I found no public API that writes to the uplink. Speakerphone plus `AudioTrack` plus the phone's own mic is the only unprivileged route, and the mic must stay unmuted, so it depends on the device's AEC not cancelling the injected signal as echo. Expect poor quality and no reliable barge-in. Treat it as a hack for demos only. I found no shipped product using it for a live two-way agent.
- API 37 `Call.transfer` plus `conference()` open a different class of design: a default-dialer app can move the call to a server leg without touching audio. See the section on carrier conference and transfer below.

### Gaps
- I found no reliable per-OEM data on whether `VOICE_CALL` capture leaks to third-party apps on Samsung, Xiaomi, OnePlus, Vivo or Oppo in current builds. Only vendor-neutral blog claims exist, so run a device test matrix if this matters.
- Android 16 specifics: no telecom or audio change surfaced in search. This is absence of evidence, not confirmation.
- `MODE_IN_CALL` usage rules: the fetched `AudioManager` excerpt showed only one-line summaries, such as `MODE_CALL_SCREENING` "Call screening in progress". The claim that `setMode(MODE_IN_CALL)` is telephony-only is from memory and unverified.
- `placeCall` permission (`CALL_PHONE`) and its API level were not in the fetched excerpt.

## What do privileged and shipped Android products actually do (Pixel, Samsung, Xiaomi, Truecaller), and is it in India?

### Takeaway
Every on-call Android AI I could document is either pre-installed system software (Pixel, Samsung, Xiaomi) or reaches the audio on a server through call forwarding (Truecaller Assistant, on thin evidence). No third-party app bridges live cellular audio to a server from the handset.

### Cited Findings
- **Pixel Call Screen availability:** automatic screening in Australia, Canada, Ireland, UK and US. Manual screening adds France, Germany, India, Italy, Japan and Spain. India is manual-only and limited to the Pixel 10 and Pixel 11 series, and manual screening needs a SIM from a listed country. — [Pixel Phone Help](https://support.google.com/pixelphone/answer/9118387?hl=en)
- "Call Screen works on your device and doesn't use Wi-Fi or mobile data"; "AI replies are generated on device". — [Pixel Phone Help](https://support.google.com/pixelphone/answer/9118387?hl=en)
- India timing conflict: 2025 coverage described an India beta in English and Hindi; a later source dates manual Call Screen in India to the June 2026 Pixel Drop. — [9to5Google, Sep 2025](https://9to5google.com/2025/09/29/pixel-phone-app-updates/); [Gadget Hacks](https://android.gadgethacks.com/news/best-google-pixel-features-call-ais-strong-track-record/)
- Hold for Me: the only source lists US, UK, Canada, Australia and Japan, with no India. It is an older article. — [Computerworld](https://www.computerworld.com/article/1613551/google-pixel-phone-calling-features.html)
- API 37 lists audio-processing use cases that match these features (call screening, ask-to-hold, voicemail). — [android.telecom.Call](https://developer.android.com/reference/android/telecom/Call)
- **Samsung Bixby Text Call** answers with an automated voice and shows the caller's words as text. It launched in India in Nov 2023, English only, and later added English (India). — [Samsung India newsroom](https://news.samsung.com/in/samsung-launches-bixby-text-call-in-india-now-have-a-text-chat-like-communication-with-your-callers-using-bixby); [GSMArena](https://www.gsmarena.com/bixby_text_call_now_available_in_india_too_only_in_english_though-news-60489.php); [SamMobile](https://www.sammobile.com/news/bixby-text-call-supports-indian-english-accent/)
- Caution: a Samsung Community post titled "Now in Hinglish and Hindi with Auto-Answer" is a 2023 **user's feature request**, not an announcement. — [Samsung Community](https://community.samsung.com/in/conversations/Tech-Talk/Bixby-Text-to-Call-Now-in-Hinglish-and-Hindi-with-Auto-Answer/r2_14404625)
- **Xiaomi AI Call Assistant** (`com.xiaomi.aiasst.service`) is described as a system tool on new Xiaomi devices with Hindi listed. Only APK-listing sources were found. — [Uptodown](https://ai-call-assistant.en.uptodown.com/android)
- **Truecaller Assistant** answers and asks why the caller is calling, with live transcription. It launched in India on Android in 2023 at ₹149/month with English, Hindi and Hinglish; iOS users in India were told to wait. — [GSMArena](https://www.gsmarena.com/truecaller_assistant_india_launch_price_details-news-59245.php); [Business Wire 2022](https://www.businesswire.com/news/home/20220928005210/en/Truecaller-Launches-AI-powered-Assistant-to-Filter-Out-Fraud-and-Scam-Calls)
- Truecaller Assistant mechanism: a third-party guide says it needs conditional call forwarding and that some prepaid plans have limits. An App Store reviewer says "Any call that is not in your contacts is forwarded out to their platform." Neither is a Truecaller primary source, and the guide is internally inconsistent. — [HowtoGadget](https://www.howtogadget.blog/truecaller-assistant-ai-call-screener-guide); [App Store listing](https://apps.apple.com/app/truecaller-phone-call-blocker/id448142450)
- **Truecaller recording in India (Feb 2024):** Android shows a button in Truecaller's dialer and a floating button over other dialers. On iOS, because of CallKit restrictions, the app "calls a second recording line, and you manually merge the calls." — [TechCrunch](https://techcrunch.com/2024/02/25/truecaller-brings-call-recording-and-transcription-to-india)

### Inferences
- The private Telecom path above fits Pixel-style on-device screening, because Google's dialer is a system app with `CAPTURE_AUDIO_OUTPUT`. I found no source stating that Pixel, Samsung or Xiaomi use this exact path. OEMs could also use vendor audio HAL routes.
- A vendor partnership or preload (a system app on a specific OEM) is the only legitimate way to obtain this audio path. That is a business-development route, not an app feature.

### Gaps
- No primary source on Bixby Text Call's current Hindi status, One UI 8 behaviour, or whether it auto-answers.
- No official Xiaomi documentation on how its assistant accesses audio. Vivo and Oppo equivalents were not covered.
- No source on Gemini's role in Pixel call features.
- No Truecaller help-centre page was retrievable, so conditional forwarding is **partly confirmed only**.

## iOS: can an app answer a cellular call, read its audio, or use any Apple AI feature?

### Takeaway
I found no public API for this. Every Apple on-call AI feature is first-party, on-device and not extensible, per the sources I could read. Third parties use a merged line (Truecaller recording) or carrier forwarding (Truecaller Assistant, per a user report). Call Screening does cover English (India), so LesCall's machine-line detector will meet it on Indian calls.

### Cited Findings
- **iOS 27 feature availability:** English (India) is listed for Live Voicemail, Call Screening, Hold Assist and Call Transcription. **Hindi appears only for Call Recording.** Live Translation in Phone lists neither English (India) nor Hindi. Call Transcription Summaries and Voicemail Summaries list English (India). — [Apple feature availability](https://www.apple.com/ios/feature-availability/)
- Call Screening "automatically answers calls from unknown numbers"; once the caller gives a name and reason, the iPhone rings. Settings are Never, Ask Reason for Calling, or Silence. It doesn't apply while roaming, and it turns off for 24 h after an emergency call. — [Screen and block calls](https://support.apple.com/guide/iphone/screen-and-block-calls-iphe4b3f7823/ios); [Manage unknown callers (IN)](https://support.apple.com/en-in/111106)
- Hold Assist: iPhone 12 / SE 3 and later, not in all regions. — [Apple Support](https://support.apple.com/en-us/127521)
- Native call recording since iOS 18.1 saves the recording and transcript in Notes; summaries need Apple Intelligence. — [Apple Support 121583](https://support.apple.com/121583)
- Truecaller's iOS recording works around CallKit by merging a second line. — [TechCrunch](https://techcrunch.com/2024/02/25/truecaller-brings-call-recording-and-transcription-to-india)
- Truecaller's Live Caller ID on iPhone needs iOS 18.2+ (Jan 2025). — [Business Today](https://www.businesstoday.in/technology/news/story/your-iphone-will-never-be-the-same-truecaller-updates-ios-app-with-big-features-461655-2025-01-22)
- Truecaller Assistant iOS markets: one App Store snippet lists US, CA, AU, CL, ZA, SE and IL (no India), but the fetched listing text does not contain that list. I found no report of an iOS launch in India. — [App Store listing](https://apps.apple.com/app/truecaller-phone-call-blocker/id448142450); [GSMArena 2023](https://www.gsmarena.com/truecaller_assistant_india_launch_price_details-news-59245.php)
- On iPhone, conditional forwarding is set through carrier codes (`*61*`, `*62*`, `*67*`) rather than Settings, which offers only unconditional forwarding. These are low-quality sources, and codes vary by carrier. — [iGeeksBlog](https://www.igeeksblog.com/how-to-use-conditional-call-forwarding-on-iphone/); [Voice.ai](https://voice.ai/hub/phone/unconditional-call-forwarding-explained/)

### Inferences
- iOS cannot auto-answer or auto-merge for a third-party app. Truecaller's manual merge exists because no API does it for them. On iOS, attaching an agent to the personal number therefore means network forwarding (inbound) or a human tapping merge. **Unattended outbound from the personal number on iOS is not feasible, except via an HFP bridge.**
- `CXCallObserver` gives call state only. This is from memory (`uuid`, `isOutgoing`, `hasConnected`, `hasEnded`, `isOnHold`): Apple's page returned only its title to the fetcher, so verify it.
- LesCall's existing iOS Call Screening handling (`machine_line.py`, RUNBOOK §63) matters more now that English (India) is supported, for both outbound AI calls and this project.

### Gaps
- No Apple developer page was readable (JS-rendered). "No public API" is a negative I could not cite directly. The evidence is Truecaller's workaround and the absence of any such feature in the sources.
- App Intents and Apple Intelligence for phone: not evidenced in my searches, so I cannot state either way what iOS 27 adds.
- The recording announcement to the far party was not confirmed by a retrieved source (I recall that it exists).

## Hardware bridge: Bluetooth HFP "car kit" attached to the phone

### Takeaway
This is the only unprivileged device-side route that gives true two-way call audio on both Android and iOS, with no app needed. The building blocks exist, but it needs one radio per phone within Bluetooth range, audio is narrowband to 16 kHz at best, and I found no commercial product doing this for AI agents.

### Cited Findings
- **ESP32:** the ESP-IDF HFP client provides `esp_hf_client_answer_call()` and `esp_hf_client_dial()`, plus audio callbacks and `esp_hf_client_audio_data_send()` over (e)SCO when Voice Over HCI is enabled. It reports a `CONNECTED_MSBC` state. — [ESP-IDF HFP client](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/bluetooth/esp_hf_client.html)
- **Linux:** oFono's `hfp_hf_bluez5` plugin implements the HF role over BlueZ 5 with voice call, handsfree audio and call volume components. The code dates from 2013. — [oFono source](https://kernel.googlesource.com/pub/scm/network/ofono/ofono/+/752d286a5ce3efc28a6b8a4c4dffb44d23d8d15b/plugins/hfp_hf_bluez5.c)
- BlueALSA can pair with oFono for full HFP. It supports both CVSD (narrowband) and mSBC (wideband, 16 kHz) from release 4.1.0, and does not implement telephony functions itself. — [BlueALSA wiki](https://github.com/arkq/bluez-alsa/wiki/Using-BlueALSA-with-HFP-and-HSP-Devices)
- **Asterisk `chan_mobile`:** "currently only allows one device (phone or headset) to be connected to an adapter at a time"; "one adapter for each device you wish to use simultaneously"; a device leaving range is disconnected. — [Asterisk Mobile Channel Concepts](https://docs.asterisk.org/Configuration/Channel-Drivers/Mobile-Channel/Mobile-Channel-Concepts/)
- Field reports are mixed: one user had it "working well with iPhone 4s", another had one-way voice with a Galaxy S5, and others call a stable connection anything but predictable. These are old. — [FreePBX forum](https://community.freepbx.org/t/chan-mobile-working-well-with-iphone-4s/13494); [Asterisk community](https://community.asterisk.org/t/chan-mobile-one-way-voice-with-galaxy-s5/70017)
- **Android as HF:** the AOSP stack contains an HF client (`bta_hf_client`, `BTA_HfClientEnable`). — [AOSP source](https://android.googlesource.com/platform/system/bt/+/5f222ea/bta/hf_client/bta_hf_client_api.c)
- **SIM-in-a-box gateways:** Yeastar TG (1 to 16 channels) and FreeSWITCH `mod_gsmopen` (beta; "one interface (a GSM modem)" per channel; Huawei E1550 recommended) bridge GSM to SIP. These need the SIM inside the box and do not attach to the phone. — [mod_gsmopen docs](https://developer.signalwire.com/freeswitch/FreeSWITCH-Explained/Modules/mod_gsmopen_7143567); [Yeastar TG datasheet](https://ipphone-warehouse.com/content/pdf/yeastar/Yeastar-TG-Series-Datasheet.pdf); [Yeastar interconnect guide](https://help.yeastar.com/en/s-series/topic/interconnect_yeastar_s-series_voip_pbx_and_tg_gateway.html)

### Inferences
- **Answers inbound:** yes, via ATA from the HF, on a timer or on a command. **Places outbound with the personal number:** yes, because the phone's SIM dials. Both work on iPhone too, which makes this the only unattended iOS option.
- **Design shape:** a small always-powered box (Pi or ESP32 with Wi-Fi/LTE backhaul) near where the phone lives, connecting to LesCall by WebSocket or SIP. It fits an office desk or a phone left on charge. It does not fit a phone in a pocket without a wearable-class box.
- **User takeover:** the user can tap the audio route back to the handset (a normal iOS and Android control). When the phone leaves range the agent is gone, and calls ring normally.
- **Audio:** telephone narrowband in most cases, with mSBC only when both ends negotiate it, plus Bluetooth SCO, Wi-Fi/LTE backhaul and cellular delay. No latency numbers were found, so measure them. LesCall's pipeline already handles 8 kHz L16 from FreeSWITCH, so the narrowband case is not new.
- **Scale:** a hardware SKU per customer, plus shipping and support. Realistic for a pilot or for high-value owners, not for mass consumer use. No store review applies.

### Gaps
- PipeWire's HF role and call control were not verified. Search returned no current source, and whether `chan_mobile` supports mSBC is unconfirmed.
- I found nothing on Dial2Do, CellSocket, xLite or any current commercial Bluetooth-to-PBX gateway, including whether Yeastar offers one. Treat all of these as unverified, possibly defunct.
- The `profile_supported_hfpclient` overlay and how non-automotive Android builds enable HF could not be fetched (404). The claim that consumer builds disable it remains unverified, though it is consistent with Android using the HF role mainly for car builds.
- No evidence on Indian OEM handset interoperability, or on whether iOS or Android will drop an HF after inactivity.

## Carrier conference or transfer driven from the phone (a route surfaced by this research, not in the brief)

### Takeaway
A default-dialer Android app can get both call directions to the server with no audio API, by merging a second cellular leg to a LesCall DID. On API 37 it could also transfer. This looks like the most promising unprivileged Android-only software path and needs a prototype, since no shipped AI product was found doing it unattended.

### Cited Findings
- Merging a second line is a shipped pattern: Truecaller on iOS calls a recording line and the user merges the calls. — [TechCrunch](https://techcrunch.com/2024/02/25/truecaller-brings-call-recording-and-transcription-to-india)
- The public APIs exist: `placeCall`, `Call.answer`, `Call.hold`, `Call.conference(Call)`, `Call.mergeConference()`, `InCallService.setMuted` — [android.telecom.Call](https://developer.android.com/reference/android/telecom/Call); [InCallService](https://developer.android.com/reference/android/telecom/InCallService)
- API 37: `Call.transfer(Uri, boolean)` for blind or assured transfer, `Call.transfer(Call)` for consultative, under 3GPP TS 24.629. — [android.telecom.Call](https://developer.android.com/reference/android/telecom/Call)
- Merge availability is gated per call by `CAPABILITY_MERGE_CONFERENCE`. — [android.telecom.Call](https://developer.android.com/reference/android/telecom/Call)

### Inferences
- **Outbound on the personal number:** the app places leg 1 to the LesCall DID, then leg 2 to the customer, merges them when the customer answers, and mutes the mic. The customer sees the personal number, because their leg is a genuine SIM call. The server hears the customer through the carrier conference and speaks back through it.
- **Inbound:** the app answers, holds, dials the DID, then merges. The caller gets a gap of some seconds on hold (unmeasured) unless the app says nothing and accepts that. API 37 transfer could replace this when the carrier supports it, and frees the handset, but ends the user's ability to take over from the phone.
- **Takeover:** `setMuted(false)` puts the user in the call, and the server leg can then drop. The phone earpiece carries the conversation throughout, so "listen in" is free.
- **Costs and risks:** two airtime legs per call; the phone must be on, in coverage and not on another call; carrier conference and ECT support on Jio/Airtel/Vi is unknown; users must replace their dialer app (`ROLE_DIALER`), which is high friction; and Indian OEM battery killers threaten the dialer's reliability, though Telecom binds the `InCallService` during calls. Play's ban targets the Accessibility API, not conference, but a reviewer could still see an AI-participant dialer as sensitive. Consent and disclosure are for the legal workstream.

### Gaps
- Not tested. Key unknowns: can merge be automated on Indian VoLTE networks, how many seconds of hold the caller hears, the audio quality of a carrier conference, whether ringing or answer detection on leg 2 is reliable (a call can show active before the person answers on some networks), and how many Indian handsets ship API 37 (very few so far, I would expect, though I have no figures).

## Spare phone, dual-SIM, rooted and audio-loop options

### Takeaway
A second phone changes nothing for app permissions, since the rule is on the app and not the device. It only helps as a rooted appliance or a loop rig holding the SIM, which is effectively a one-channel GSM gateway with worse reliability.

### Cited Findings
- `CAPTURE_AUDIO_OUTPUT` needs a privileged (pre-installed) app, regardless of which SIM slot or device. — [Sharing audio input](https://developer.android.com/media/platform/sharing-audio-input)
- Non-root Shizuku/ADB tools are reported to enable recording but are hard to set up. Consumer blog quality only. — [Owll](https://owll.ai/blog/how-to-record-phone-call-android); [Plaud](https://www.plaud.ai/blogs/news/android-call-recording-methods-all-devices-version)

### Inferences
- **Rooted or custom-ROM phone (privileged app):** could get `CAPTURE_AUDIO_OUTPUT`. On some SoC audio HALs playback into the call also works, but it varies by chipset, cannot go through Play, and is unsupportable at customer scale. Viable for an in-house lab only.
- **Dual-SIM or eSIM:** no help. A second SIM is a second number on the same restricted stack.
- **Headset-jack or USB-audio loop:** the phone treats a wired headset or USB audio device as a normal call route, so an external box can get downlink from the output and inject uplink through the mic input. This is the analogue form of the HFP bridge. It is a hardware hack with ground-loop and level problems, is decreasingly possible (fewer phones have jacks), and needs either a human or a default-dialer app to answer. No source was found on current Indian handsets.

### Gaps
- No sourced compatibility data for rooted call-audio routes or USB audio during a cellular call on current devices.

## Network-side "SIP client" on the SIM: can the personal number register externally?

### Takeaway
Not as a consumer feature, and I found no sign of an Indian operator offering it. The SIM holds the keys and will only do challenge-response for apps with carrier privileges or a special permission. Open IMS clients exist as code, but registration still needs the operator's cooperation.

### Cited Findings
- `TelephonyManager.getIccAuthentication` (EAP-AKA, EAP-SIM, GBA) requires carrier privileges or `USE_ICC_AUTH_WITH_DEVICE_IDENTIFIER`; `READ_PRIVILEGED_PHONE_STATE` is deprecated for it. — [Microsoft binding of the Android API](https://learn.microsoft.com/en-us/dotnet/api/android.telephony.telephonymanager.geticcauthentication?view=net-android-36.0)
- Carrier privileges are granted to apps signed by certificates stored on the UICC. — [AOSP UICC docs](https://source.android.com/docs/core/connect/uicc)
- Android 12+ supports GSMA TS.43 entitlement via EAP-AKA as a platform flow, and exposes an `EapAkaHelper` library for other EAP-AKA uses. — [AOSP IMS service entitlement](https://source.android.com/docs/core/connect/ims-service-entitlement?hl=id)
- IMSDroid is an open-source Android IMS client with IMS-AKA, but is old (2010-2015) and is a reference only. — [IMSDroid](https://github.com/Lubovvv/imsdroid)
- `SipManager` and related classes were deprecated in API 31: "no longer supported and should not be used as the basis of future VOIP apps." It never used the SIM number anyway. — [SipManager](https://developer.android.com/reference/android/net/sip/SipManager)

### Inferences
- Moving the personal number to an external SIP client requires (a) SIM-held AKA, (b) carrier-signed app access, and (c) operator P-CSCF or ePDG reachability from outside its policy. Without an operator deal, (b) alone stops it.
- The operator-partnership and number-porting routes belong to the network workstream.

### Gaps
- I found no primary source on Jio, Airtel or Vi policy on external IMS registration, or on maintained open-source VoWiFi clients. The conclusion rests on the API gates rather than on an operator statement.
- How `USE_ICC_AUTH_WITH_DEVICE_IDENTIFIER` is granted was not checked. I assume it is privileged.

## LesCall app with FCM wake and WebRTC (no cellular audio)

### Takeaway
Technically easy, but it reaches only people calling inside the app, not the personal number. It does not solve the problem, though it can serve as the transport for a hand-off or an outbound-call control channel.

### Cited Findings
- Self-managed calling apps use Core-Telecom, which manages audio routing and focus and interoperates with system calls. — [Telecom overview](https://developer.android.com/develop/connectivity/telecom)

### Inferences
- `CallRedirectionService` could reroute the user's own outgoing dials to VoIP, but the customer would see the VoIP number, not the personal SIM number, unless a verified CLI is arranged at the carrier.

### Gaps
- FCM high-priority delivery on Indian OEM skins was not researched. Given dontkillmyapp's rankings, expect delivery problems unless the user whitelists the app.

## Comparison across approaches

### Takeaway
No software-only route works for iOS unattended. For Android, conference merge via a default dialer is the only unprivileged software route with two-way audio, and it is unproven. HFP hardware is the only cross-platform route with direct audio, at one box per phone. Forwarding (network workstream) remains the only route with no device constraints at all for inbound.

### Cited Findings
- The table below applies the sourced findings from the sections above. Its ratings are my judgement.

### Inferences

| Approach | Answer inbound on personal number | Place outbound showing personal number | Phone must be on and in coverage | Audio quality / latency | Store risk | Realistic scale |
|---|---|---|---|---|---|---|
| Android 3rd-party audio APIs (`VOICE_*`) | No: blocked | No | n/a | None | Not permitted | Zero |
| Accessibility + speaker/mic loop | Poorly | Poorly | Yes | Bad: echo, one side acoustic | Play forbids recording use; sideload only | Demo only |
| Android default dialer + carrier conference | Yes, with hold gap (untested) | **Yes** (untested) | Yes, and phone is occupied | Carrier conference narrowband; latency unmeasured | Medium: dialer role + review | Medium if carriers cooperate |
| Android default dialer + API 37 `transfer` | Possibly, carrier-dependent | Unclear | For the moment of transfer only | Clean server leg | Medium | API 37 devices only, few today |
| Android OEM preload / system app | Yes | Yes | Yes | Good, on-device path | None (partner deal) | One OEM at a time; biz-dev |
| Rooted phone or custom ROM | Yes (HAL dependent) | Yes | Yes (spare phone) | Variable | Off-store | Lab only |
| iOS: any app | **No** | **No** | n/a | n/a | Not available | Zero |
| iOS: manual merge (Truecaller-style) | User taps | User taps | Yes | Conference narrowband | Low | Assisted use only, not an agent |
| Bluetooth HFP box | Yes (Android and iOS) | Yes (Android and iOS) | Yes, within BT range | 8 kHz, 16 kHz if mSBC; latency unmeasured | None (no app) | One box per phone; pilot-scale |
| SIM in GSM/LTE gateway | Yes | Yes | No phone at all; SIM leaves handset | Narrowband | None | Works, but user loses phone use; telecom-rule review needed |
| External IMS/SIP on SIM | No without operator | No | No, if it worked | Would be good | n/a | Operator deal only |
| LesCall VoIP app only | No: in-app only | No | App reachable | Good (WebRTC) | Low | Not this problem |

### Gaps
- All audio, latency and reliability cells need field measurement. The legal and policy column for AI on a personal number in India is left to the legal workstream, including the obvious issue that a SIM-box-style gateway may raise telecom-licensing questions.
