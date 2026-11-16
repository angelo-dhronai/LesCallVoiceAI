"""Campaign-call recovery (RUNBOOK §85, 2026-11-13).

The Pramukh campaign calls of 2026-10-22 / 2026-11-04 on the customer
account, each pinned by the caller's own words as STT heard them:

  1131  "4 bhk" ×3 → "sorry, couldn't hear you" (the MODEL had failed);
        "share your cost sheet" → "couldn't hear you" + a price deflection
  1126  "I'm a bit busy" → "I won't take much time"; "call me after a
        week" → "after how many minutes?"
  1127  identity confirmed with more than a bare yes → a model turn
  1130  a canned "say that again?" repeated after "I can hear you"
  1136  "I never showed any interest" → "I won't take much time"
  1137  "करना ही नहीं है कॉल" → "when should I call?"; a goodbye with the
        next qualification question stapled on
  1188  "सही है" heard as "भाई है" → the agent hung up as a wrong number
  1193  "why so slow?" → busy objection; "I'm free any time" → callback →
        "we'll call you in the morning" + hang-up

Offline: no LLM calls, no live agent YAML.
"""
from __future__ import annotations

import asyncio

import pytest

from app.agent_definition.schema import AgentDefinition
from app.voice import call_recovery as cr
from app.voice import conversation as conv
from app.voice.conversation import ConversationRouter


@pytest.fixture(autouse=True)
def _offline(stub_llm):
    """Offline — see the stub_llm fixture in conftest."""


# ── pure detectors ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,want", [
    ("भाई है।", "right"), ("सही है", "right"), ("सई है", "right"),
    ("सही नंबर है", "right"), ("हाँ सही है सही है।", "right"),
    ("करेक्ट, करेक्ट।", "right"), ("बराबर", "right"),
    ("गलत नंबर है", "wrong"), ("जी रॉन्ग नंबर।", "wrong"),
    ("गलत है भाई।", "wrong"), ("wrong number i guess", "wrong"),
    ("गलत नहीं है", "right"), ("सही नहीं है", "wrong"),
    ("हा", ""), ("जी", ""), ("मेरे भाई का नंबर है", ""),
    ("i am busy right now", ""), ("राइट नाउ मैं बाहर हूँ", ""),
    ("ग़लत है या सही — क्या मतलब?", ""),
])
def test_reconfirm_hint(text, want):
    assert cr.reconfirm_hint(text) == want


@pytest.mark.parametrize("text", [
    "लेकिन मैंने कुछ इंटरेस्ट नहीं दिखाया।", "मैंने कोई इंक्वायरी नहीं की थी",
    "I never showed any interest", "interest nahi dikhaya maine",
])
def test_denies_interest(text):
    assert cr.denies_interest(text)


@pytest.mark.parametrize("text", [
    "मुझे इंटरेस्ट नहीं है", "आपने इंटरेस्ट दिखाया था?", "हाँ मैंने interest दिखाया था",
])
def test_denies_interest_is_not_plain_disinterest(text):
    assert not cr.denies_interest(text)


@pytest.mark.parametrize("text,last,want", [
    ("करना ही नहीं है कॉल।", "", True),
    ("कॉल मत करना", "", True),
    ("मेरे को कॉल मत करना दोबारा।", "", True),
    ("please don't call me", "", True),
    ("कभी भी नहीं।", "बताइए कब कॉल करूँ।", True),
    ("कभी भी नहीं।", "आप 3 BHK देख रहे हैं या 4 BHK?", False),
    ("अभी कॉल मत करो", "", False),          # "not now" is a deferral
    ("आज कॉल मत करना, कल करना", "", False),
    ("we directly farmers, we don't call or anything.", "", False),
])
def test_refuses_any_call(text, last, want):
    assert cr.refuses_any_call(text, last) is want


@pytest.mark.parametrize("text,want", [
    ("कोई ज़रूरत नहीं है।", True), ("कुछ नहीं करना है।", True),
    ("not interested.", True), ("मुझे दिलचस्पी नहीं है जी", True),
    ("नहीं चाहिए", True),
    # a product preference is not a refusal of the call
    ("बंगलो नहीं चाहिए, मुझे फ्लैट ही चाहिए।", False),
    ("नहीं, बंगलो में तो इंटरेस्ट नहीं है।", False),
    ("Swarit में नहीं चाहिए, दूसरा कोई हो तो बताइए।", False),
    ("इतना इंटरेस्ट नहीं था अभी तक।", False),
    ("i don't need qualification, my friend does it for me.", False),
])
def test_flat_refusal(text, want):
    assert cr.flat_refusal(text) is want


@pytest.mark.parametrize("text,last,want", [
    ("नहीं, अभी मैं थोड़ा बिज़ी हूँ।", "", True),
    ("बिल्कुल ठीक नहीं रहेगा।", "क्या अभी बात करना ठीक रहेगा?", True),
    ("नहीं", "अभी दो मिनट बात करना ठीक रहेगा?", True),
    ("हाँ ठीक है", "अभी दो मिनट बात करना ठीक रहेगा?", False),
    ("नहीं", "आप 3 BHK देख रहे हैं या 4 BHK?", False),
    ("मैं बिज़ी नहीं हूँ", "", False),
    ("all our representatives are busy on other calls.", "", False),
])
def test_not_a_good_time(text, last, want):
    assert cr.not_a_good_time(text, last) is want


@pytest.mark.parametrize("text,days", [
    ("आप मुझे एक वीक के बाद फोन कीजिए।", 7), ("2 din baad call karna", 2),
    ("दो हफ्ते बाद", 14), ("after 3 days", 3), ("एक महीने बाद", 30),
    ("दो मिनट", 0), ("कल शाम को", 0),
])
def test_long_offset_days(text, days):
    assert cr.long_offset_days(text, explicit_only=True) == days


def test_next_week_is_left_to_the_day_question():
    assert cr.long_offset_days("call me next week") == 7
    assert cr.long_offset_days("call me next week", explicit_only=True) == 0


def test_days_phrase():
    assert cr.days_phrase(7, "hi") == "एक हफ़्ते बाद"
    assert cr.days_phrase(2, "hi") == "2 दिन बाद"
    assert cr.days_phrase(7, "en") == "in a week"


@pytest.mark.parametrize("text,want", [
    ("आप शेयर तो करिए अपना कोर्स शीट।", True),
    ("details भेज दीजिए WhatsApp पर", True),
    ("could you send me a brochure or something?", True),
    ("आपने brochure भेजा था", False), ("location कहाँ है?", False),
    ("आपने details भेज दी थी", False),
])
def test_share_request(text, want):
    assert cr.share_request(text) is want


@pytest.mark.parametrize("text", [
    "लेकिन इतना स्लो क्यों बोल रहे हैं?",
    "अरे भाई इतना टाइम नहीं रहता किसी के पास। अरे आप इतना ब्रेक क्यों ले रहे हैं बोलने में?",
    "why are you so slow",
])
def test_speed_complaint(text):
    assert cr.speed_complaint(text)


def test_agent_goodbye_detection():
    assert cr.agent_line_is_goodbye(
        "माफ़ कीजिएगा, Sagar जी। मैं आपको कभी कॉल नहीं करूँगा। आपका दिन शुभ हो।")
    assert cr.agent_line_is_goodbye(
        "बिल्कुल Sir, मैं आपको बाद में कॉल करता हूँ। आपका दिन शुभ हो!")
    assert not cr.agent_line_is_goodbye("Pramukh Aristo में 3 और 4 BHK हैं।")
    assert not cr.agent_line_is_goodbye("आपका दिन कैसा रहा?")


def test_strip_leading_ack():
    assert cr.strip_leading_ack(
        "Understood. आपका approximate budget range क्या comfortable रहेगा?"
    ) == "आपका approximate budget range क्या comfortable रहेगा?"
    assert cr.strip_leading_ack(
        "Sure Sir. आप specifically 3 BHK consider कर रहे हैं या 4 BHK?"
    ).startswith("आप specifically")
    q = "May I know, आप self-use के लिए देख रहे हैं?"
    assert cr.strip_leading_ack(q) == q


# ── the engine ──────────────────────────────────────────────────────────────

IDENTITY = {"hi": "नमस्ते! क्या मेरी बात {contact_name} जी से हो रही है?"}
GOOD_TIME = {"hi": "शुक्रिया! मैं आशा बोल रही हूँ TestCo से — अभी दो मिनट बात "
                   "करना ठीक रहेगा?"}
BUSY = {"hi": "कोई बात नहीं। अगर अभी ठीक नहीं है तो बता दीजिएगा आप कब "
              "available रहेंगे, ताकि हम बात कर सकें।"}
SIZE_Q = "Sure Sir. आप specifically 3 BHK consider कर रहे हैं या 4 BHK?"
BUDGET_Q = "Understood. आपका approximate budget range क्या comfortable रहेगा?"


def make_router(recovery=True, ack=True, stage="DISCOVERY", good_time=True):
    ob = {"identity_confirm_line": IDENTITY,
          "good_time_check_line": GOOD_TIME,
          "busy_response": BUSY}
    if ack:
        ob["identity_ack_line"] = {"hi": "OK."}
    d = {
        "agent_id": "t_recovery", "version": "1.0.0",
        "identity": {"agent_name": "Asha", "company_name": "TestCo"},
        "languages": {"supported": ["hi", "en"], "default": "hi",
                      "tts_voices": {}},
        "call_flow": {
            "stages": [
                {"id": "WARM_WELCOME", "order": 0, "goal": "confirm identity",
                 "role": "greeting", "turns_until_advance": 2,
                 "advance_when": {"max_turns": 2}},
                {"id": "INTRO", "order": 1, "goal": "introduce",
                 "role": "pitch", "turns_until_advance": 99},
                {"id": "DISCOVERY", "order": 2, "goal": "discover",
                 "role": "discovery", "turns_until_advance": 99,
                 "collect": ["configuration_required", "budget_range"],
                 "script": {"ask_verbatim": True}},
            ],
            "recovery": {"enabled": True} if recovery else {},
            "callback_policy": {"enabled": True},
        },
        "slots": [
            {"name": "configuration_required", "type": "enum", "priority": 1,
             "required": True, "enum_values": ["3 BHK", "4 BHK"],
             "enum_synonyms": {"3 BHK": ["3bhk", "teen bhk"],
                               "4 BHK": ["4bhk", "char bhk"]},
             "question": {"hi": SIZE_Q}, "max_asks": 2},
            {"name": "budget_range", "type": "money", "priority": 2,
             "required": True, "question": {"hi": BUDGET_Q}, "max_asks": 2},
        ],
        "response_bank": {
            "opening_greeting": "hi",
            "objection_responses": {"hi": [BUSY["hi"], "कोई बात नहीं।"]},
            "decline_farewell": {"hi": "कोई बात नहीं, आपके समय के लिए शुक्रिया।"},
            "lang_fallbacks": {"hi": "माफ़ कीजिए, ठीक से सुनाई नहीं दिया — एक "
                                     "बार फिर बोलेंगे?"},
        },
        "follow_up": {"clarify_commitments": {"enabled": True}},
        "call_direction": "outbound",
        "outbound": ob,
    }
    r = ConversationRouter(agent_def=AgentDefinition.from_dict(d),
                           default_language="hi")
    r.call_direction = "outbound"
    r.outbound_contact_name = "Vishal"
    r.memory.call_state.customer_name = "Vishal"
    r.state.language = "hi"
    r._intro_delivered = True
    if stage == "GREETING":
        r.last_agent_response = IDENTITY["hi"].replace("{contact_name}", "Vishal")
    else:
        r.state.stage = stage
        r._good_time_spoken = good_time
        r.last_agent_response = GOOD_TIME["hi"]
    return r


def drive(r, text):
    async def _one():
        return [res async for res in r.process_stream(text)]
    return asyncio.run(_one())


def said(results) -> str:
    return " ".join(getattr(x, "text", "") or "" for x in results).strip()


# 1136 ─ "I never showed any interest"
def test_never_showed_interest_gets_the_removal_line_and_the_call_ends():
    r = make_router(stage="INTRO")
    out = said(drive(r, "लेकिन मैंने कुछ इंटरेस्ट नहीं दिखाया।"))
    assert "नंबर list से हटा" in out and "ज़्यादा टाइम" not in out
    assert r.state.is_closing and r.hangup_pending
    assert r.state_mgr.extra_flags.get("dnd_requested") is True
    assert r.call_outcome == "dnd"


# 1137 ─ a refusal of any call
@pytest.mark.parametrize("text", ["करना ही नहीं है कॉल।", "मुझे कॉल मत करना।"])
def test_refusing_any_call_closes_at_once(text):
    r = make_router(stage="INTRO")
    out = said(drive(r, text))
    assert "नंबर list से हटा" in out
    assert r.state.is_closing and r.hangup_pending


def test_never_as_the_answer_to_when_closes():
    r = make_router(stage="INTRO")
    drive(r, "नहीं, अभी मैं थोड़ा बिज़ी हूँ।")
    out = said(drive(r, "कभी भी नहीं।"))
    assert "नंबर list से हटा" in out and r.hangup_pending


def test_flat_no_need_closes_politely_without_a_pitch():
    r = make_router(stage="INTRO")
    out = said(drive(r, "कोई ज़रूरत नहीं है।"))
    assert "आपके समय के लिए शुक्रिया" in out
    assert r.state.is_closing and r.call_outcome == "not_interested"
    assert not r.state_mgr.extra_flags.get("dnd_requested")


def test_recovery_off_keeps_the_objection_path():
    r = make_router(recovery=False, stage="INTRO")
    drive(r, "लेकिन मैंने कुछ इंटरेस्ट नहीं दिखाया।")
    assert not r.state.is_closing and not r.hangup_pending


# 1126 ─ busy, then "after a week"
def test_busy_gets_the_owner_line_then_a_week_is_confirmed():
    r = make_router(stage="INTRO")
    out = said(drive(r, "नहीं, अभी मैं थोड़ा बिज़ी हूँ।"))
    assert "कब available रहेंगे" in out and "ज़्यादा टाइम" not in out
    assert not r.state.is_closing
    out = said(drive(r, "आप मुझे एक वीक के बाद फोन कीजिए।"))
    assert "एक हफ़्ते बाद" in out and "मिनट" not in out
    assert r.state.is_closing and r.hangup_pending
    assert r.state_mgr.extra_flags.get("callback_offset_min") == 7 * 1440


def test_not_a_good_time_then_still_no_time_is_one_soft_close():
    r = make_router(stage="INTRO")
    out = said(drive(r, "बिल्कुल ठीक नहीं रहेगा।"))
    assert "कब available रहेंगे" in out
    out = said(drive(r, "अभी बात करना ही ठीक नहीं है।"))
    assert "कब" not in out and "?" not in out
    assert r.state.is_closing and r.hangup_pending


# 1193 ─ pace complaint / free any time
def test_a_pace_complaint_is_an_apology_and_the_open_question():
    r = make_router()
    r.last_agent_response = SIZE_Q
    r.state_mgr.note_asked("configuration_required")
    out = said(drive(r, "अरे भाई इतना टाइम नहीं रहता किसी के पास। अरे आप "
                        "इतना ब्रेक क्यों ले रहे हैं बोलने में?"))
    assert "delay" in out and "3 BHK consider" in out
    assert "available रहेंगे" not in out
    assert not r.state.is_closing and r.state.decline_count == 0


def test_free_any_time_is_not_a_callback_request():
    r = make_router()
    assert r._detect_callback_request(
        "मैं तो ऑलवेज फ्री ही रहता हूँ, सुबह से शाम तक आप कभी भी कॉल कीजिए।"
    ) is False
    assert r._detect_callback_request("कल सुबह कॉल कीजिए") is True


def test_a_clarification_answer_with_no_time_never_inherits_one():
    r = make_router()
    r._pending_clarify = {"kind": "callback", "day": "", "time": "morning",
                          "vague": True, "clarified": False}
    r._clarify_asked = True
    resp = r._resolve_clarification(
        "यह एआई कॉल है, ओके ओके, लेकिन यह ब्रेक नहीं पड़ना चाहिए।")
    assert resp is None and not r.state.is_closing


# 1131 ─ cost sheet / model failure
def test_share_request_is_acknowledged_and_the_open_question_follows():
    r = make_router()
    r.last_agent_response = BUDGET_Q
    r.state_mgr.set_externally("configuration_required", "4 BHK")
    r.state_mgr.note_asked("budget_range")
    out = said(drive(r, "आप शेयर तो करिए अपना कोर्स शीट।"))
    assert "WhatsApp" in out and "सुनाई नहीं" not in out
    assert "budget range" in out and "Understood" not in out
    assert r.state_mgr.extra_flags.get("whatsapp_details_requested") is True


def test_model_failure_records_the_answer_and_asks_the_next_question():
    r = make_router()
    r.last_agent_response = SIZE_Q
    r.state_mgr.note_asked("configuration_required")
    line = r._llm_failure_reply("4 bhk")
    assert r.state_mgr.state["configuration_required"] == "4 BHK"
    assert "budget range" in line and "सुनाई नहीं" not in line


def test_model_failure_after_a_go_ahead_puts_the_open_question_again():
    r = make_router()
    r.last_agent_response = SIZE_Q
    r.state_mgr.note_asked("configuration_required")
    line = r._llm_failure_reply("हम्म")
    assert "3 BHK consider" in line and "सुनाई नहीं" not in line


def test_model_failure_on_something_else_never_blames_the_callers_audio():
    r = make_router()
    r.last_agent_response = SIZE_Q
    r.state_mgr.note_asked("configuration_required")
    line = r._llm_failure_reply("आपका office कहाँ है")
    assert "सुनाई नहीं" not in line and "फिर बताएँगे" in line


def test_model_failure_with_recovery_off_is_the_legacy_line():
    r = make_router(recovery=False)
    assert "सुनाई नहीं दिया" in r._llm_failure_reply("4 bhk")


def test_a_size_is_never_stored_as_the_budget():
    r = make_router()
    sm = r.state_mgr
    sm.merge_state({"budget_range": "4 BHK"}, source="llm", utterance="4 bhk")
    assert sm.state["budget_range"] is None
    sm.merge_state({"budget_range": "90 Lakhs"}, source="llm",
                   utterance="90 लाख तक")
    assert sm.state["budget_range"] == "90 Lakhs"


# 1127 ─ "OK." + the good-time line with no model turn
def test_identity_yes_with_the_name_takes_the_fast_path_with_ok_first():
    r = make_router(stage="GREETING")
    res = drive(r, "हाँ हाँ विशाल जी से हो रही है बोलिए")
    texts = [x.text for x in res]
    assert texts[0] == "OK."
    assert "अभी दो मिनट" in texts[1]
    assert all(x.is_shortcut for x in res)
    assert r._good_time_spoken is True


def test_no_ack_line_means_no_ok():
    r = make_router(stage="GREETING", ack=False)
    texts = [x.text for x in drive(r, "हाँ जी")]
    assert len(texts) == 1 and "अभी दो मिनट" in texts[0]


@pytest.mark.parametrize("text", [
    "हाँ बोलिए कौन?", "हाँ भाई का नंबर है", "नहीं", "हाँ बाद में कॉल करना",
])
def test_wide_identity_yes_is_narrow(text):
    assert not conv._is_identity_yes_wide(text)


# 1137 ─ nothing after a goodbye
def test_the_script_floor_never_follows_a_goodbye():
    r = make_router()
    r.last_agent_response = SIZE_Q
    r.state_mgr.note_asked("configuration_required")
    r.state_mgr._asked_turn["configuration_required"] = r.state_mgr.turn_index
    assert r._script_followup(
        "माफ़ कीजिएगा, मैं आपको कभी कॉल नहीं करूँगा। आपका दिन शुभ हो।") == ""
    assert "3 BHK" in r._script_followup("जी, समझ गया।")


# 1130 ─ presence check after a canned fallback
def test_presence_check_repeats_the_real_question_not_the_fallback():
    r = make_router()
    r.memory.add_assistant(SIZE_Q, language="hi")
    r.state_mgr.note_asked("configuration_required")
    fb = "माफ़ कीजिए, ठीक से सुनाई नहीं दिया — एक बार फिर बोलेंगे?"
    r.memory.add_assistant(fb, language="hi")
    r.last_agent_response = fb
    out = said(drive(r, "हेलो।"))
    assert "आवाज़ आ रही है" in out and "3 BHK consider" in out
    assert "सुनाई नहीं" not in out


# answer-aware hold
@pytest.mark.parametrize("text,want", [
    ("4 bhk", True), ("हाँ", True), ("नहीं", True), ("जी हाँ", True),
    ("मुझे थोड़ा सोचना पड़ेगा क्योंकि", False), ("कितने का है?", False),
    ("और bhk", False),
])
def test_turn_is_complete_answer(text, want):
    r = make_router()
    r.last_agent_response = SIZE_Q
    r.state_mgr.note_asked("configuration_required")
    assert r.turn_is_complete_answer(text) is want


def test_nothing_is_a_complete_answer_to_a_statement():
    r = make_router()
    r.last_agent_response = "Pramukh Aristo में 3 और 4 BHK homes हैं।"
    assert r.turn_is_complete_answer("हाँ") is False


# ── pipeline: the short answer hold ─────────────────────────────────────────

def _hold(text, *, answer_ms=400, dur_ms=900, splits=0, complete=True):
    import types
    from app.voice import pipeline as pl
    conv_ = types.SimpleNamespace(turn_is_complete_answer=lambda t: complete)
    me = types.SimpleNamespace(
        conversation=conv_, _caller_split_turns=splits,
        _tt_num=lambda key, default=0.0: float(
            {"answer_hold_ms": answer_ms}.get(key, default)))
    tr = types.SimpleNamespace(text=text, duration_ms=dur_ms)
    return pl.VoicePipeline._answer_hold_s(me, tr)


def test_answer_hold_applies_to_a_complete_short_answer():
    assert _hold("4 bhk") == pytest.approx(0.4)


def test_answer_hold_is_opt_in_and_never_for_narrative_or_splitters():
    assert _hold("4 bhk", answer_ms=0) is None
    assert _hold("4 bhk", dur_ms=3000) is None        # narrative-long
    assert _hold("4 bhk", splits=2) is None            # speaks through pauses
    assert _hold("मुझे सोचना है", complete=False) is None


# ── Owner flow 2026-11-14 (call 1221): offer → confirm → close / keep ──────
def _removal_router(stage="INTRO"):
    r = make_router(stage=stage)
    r._def.call_flow.recovery["removal_confirm"] = True
    return r


@pytest.mark.parametrize("text", [
    "मैंने तो लेकिन कभी इंटरेस्ट दिखाया ही नहीं प्रमुख प्रॉपर्टी में।",
    "नहीं नहीं, मैंने दिखाया ही नहीं है इंटरेस्ट कभी प्रमुख प्रॉपर्टी में।",
    "नहीं, लेकिन भाई मैंने तो इंटरेस्ट कभी दिखाया ही नहीं है।",
    "maine interest dikhaya hi nahi",
])
def test_free_word_order_denials_of_interest(text):
    assert cr.denies_interest(text)


@pytest.mark.parametrize("text", [
    "मैंने इंटरेस्ट दिखाया था", "हमने इंक्वायरी की थी, कॉल नहीं आया",
    "इन्वेस्टमेंट के तौर पर देख रहा हूं"])
def test_free_word_order_is_still_narrow(text):
    assert not cr.denies_interest(text)


@pytest.mark.parametrize("text,want", [
    ("हाँ", "yes"), ("हाँ हटा दो", "yes"), ("ठीक है", "yes"), ("okay", "yes"),
    ("yes please remove", "yes"), ("नहीं, हटा दो", "yes"),
    ("मैंने इंटरेस्ट दिखाया ही नहीं", "yes"), ("कॉल मत करना", "yes"),
    ("नहीं", "no"), ("नहीं मत हटाओ", "no"), ("रहने दो", "no"),
    ("no don't remove", "no"), ("नहीं आप बताइए", "no"),
    ("कौन बोल रहा है?", ""), ("", "")])
def test_removal_answer(text, want):
    assert cr.removal_answer(text) == want


def test_never_showed_interest_is_offered_removal_then_confirmed_then_closed():
    r = _removal_router()
    out = said(drive(r, "मैंने तो लेकिन कभी इंटरेस्ट दिखाया ही नहीं प्रमुख प्रॉपर्टी में।"))
    assert "list से आपका नाम हटा दूँ?" in out
    assert not r.state.is_closing and not r.hangup_pending
    out = said(drive(r, "नहीं नहीं, मैंने दिखाया ही नहीं है इंटरेस्ट कभी।"))
    assert "आख़िरी बार" in out and not r.hangup_pending
    out = said(drive(r, "हाँ हटा दो"))
    assert "तकलीफ़ के लिए माफ़ी" in out
    assert r.state.is_closing and r.hangup_pending
    assert r.state_mgr.extra_flags.get("dnd_requested") is True
    assert r.call_outcome == "dnd"


def test_a_no_to_the_removal_offer_keeps_the_call_going():
    r = _removal_router()
    drive(r, "लेकिन मैंने कुछ इंटरेस्ट नहीं दिखाया।")
    out = said(drive(r, "नहीं नहीं, रहने दो"))
    assert "continue करते हैं" in out
    assert not r.state.is_closing and not r.hangup_pending
    assert not r.state_mgr.extra_flags.get("dnd_requested")
    assert r._removal_step == 0


def test_a_no_at_the_final_confirmation_keeps_the_call_going():
    r = _removal_router()
    drive(r, "लेकिन मैंने कुछ इंटरेस्ट नहीं दिखाया।")
    drive(r, "हाँ")
    out = said(drive(r, "नहीं"))
    assert "continue करते हैं" in out and not r.hangup_pending
    # Having chosen to stay, the caller is not offered removal again.
    assert r._refusal_kind("मैंने इंटरेस्ट नहीं दिखाया") == ""


def test_an_unclear_reply_repeats_the_offer_once():
    r = _removal_router()
    drive(r, "लेकिन मैंने कुछ इंटरेस्ट नहीं दिखाया।")
    out = said(drive(r, "कौन बोल रहा है?"))
    assert "list से आपका नाम हटा दूँ?" in out and r._removal_step == 1


# ── Call 1224: the share shortcut is for a bare request only ───────────────
@pytest.mark.parametrize("text,want", [
    ("आप शेयर तो करिए अपना कोर्स शीट", True),
    ("ब्रोशर भेज दो", True),
    ("मेरे को शाम को समय ठीक रहेगा और मुझे ना ब्रोशर भी भेज देना व्हाट्सएप पे।", False),
    ("लेकिन डिटेल्स किस चीज़ की भेजोगे मेरे को व्हाट्सएप पे?", False)])
def test_bare_share_request(text, want):
    assert cr.bare_share_request(text) is want


def test_the_question_just_asked_outranks_an_older_slot_ask():
    r = make_router()
    r.state_mgr.note_asked("configuration_required")
    r.memory.add_assistant("Wednesday को कौन सा time आपके लिए ठीक रहेगा?",
                           language="hi")
    assert "कौन सा time" in r._pending_question()
