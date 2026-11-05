"""Prashant (Blue Stone test) — Pramukh Blue Stone on the owner's scripted pitch.

arisha@dhronai.com's TEST twin (`pramukh_blue_stone_test-*`, inactive), built
on the SAME scripted engine as the Aristo test agent (RUNBOOK §75/§78,
`build_pramukh_aristo_test.py`, 2026-10-12). The owner's "Pramukh Aristo —
Professional Sales Calling Script" is the pattern; every Blue Stone fact in
the lines below is the sheet's (`deploy/pramukh_vapi_projects.yaml`, docs/
Pramukh Project Details.md §2) — nothing else was invented:

  * opening beats unchanged — hello → identity → the good-time line;
  * one authored line: spacious 2 & 3 BHK homes and commercial shops in New
    Chala, Vapi + "requirement first" + where are you calling from;
  * the script's questions, word for word, one per turn: self-use or
    investment → 2 or 3 BHK → budget → immediate or 3–6 months → most
    important factor. Each answer is STORED and never re-asked;
  * the pitch by purpose (self-use / investor + horizon), from the
    KNOWLEDGE BASE — Blue Stone is UNDER CONSTRUCTION, possession coming
    soon, so the self-use pitch ends on seeing the location, layout and
    construction progress, never on a ready flat (call 1063 on the main
    Blue Stone agent said "कुछ रेडी पोजीशन के नज़दीक हैं" — never again);
  * "what else would you like to know?" after EVERY answer, until the
    caller says no (engine: `script.after_answer` + `no_more_questions`);
  * the site visit ONLY then — once — Saturday evening or Sunday morning,
    the hour from the caller, one read-back, pre-close question, goodbye.

Owner rules carried over from Aristo (2026-10-12): no visit push before the
end (the "later" / "price high" / WhatsApp answers lose their visit
sentence); price: the script's line first, the approximate figure (sheet:
₹50–55 lakh 2 BHK, ₹60 lakh onwards 3 BHK) only on a second ask (engine
━━ PRICE ━━ band); out of budget → "our other properties in this budget?"
first (engine). Blue Stone's own: a shop enquiry is answered (it is a
commercial-cum-residential project) but shop sizes and prices are NOT
published — the team shares them.

    docker exec -i lescall-api python - --apply < deploy/build_pramukh_blue_stone_test.py
    calling-agent/venv/bin/python deploy/build_pramukh_blue_stone_test.py --dry-run
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import shutil
import sys
from pathlib import Path

REPO = Path("/home/angelo/lescall")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "calling-agent"))

import yaml  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "bpp", REPO / "deploy" / "build_pramukh_projects.py")
bpp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bpp)

from backend.app.agent_definition import store  # noqa: E402
from backend.app.agent_definition.schema import AgentDefinition  # noqa: E402

SLUG = "pramukh_blue_stone_test-{direction}"
DISPLAY = "Blue Stone test"
OWNER = "arisha@dhronai.com"
ACTOR = "angelo@dhronai.com:blue-stone-scripted-pitch-2026-10-13"
REASON = "Blue Stone test agent on the owner's scripted sales pitch (RUNBOOK §75)"
P = "Pramukh Blue Stone"
KEY = "blue_stone"
ITEM_ID = "pramukh-blue-stone"

# ---------------------------------------------------------------------------
# The lines, in native script with English words in Latin (Sarvam degrades
# on romanised Hindi — CLAUDE.md). "Sir" is held to contacts.gender by the
# engine (Sir / Ma'am / nothing — RUNBOOK §42). The structure and every
# shared sentence are the owner's Aristo script; only the Blue Stone facts
# differ.
# ---------------------------------------------------------------------------
L_OPEN = ("Pramukh Blue Stone, New Chala Vapi में हम spacious 2 और 3 BHK homes "
          "और commercial shops offer करते हैं। Project details देने से पहले मैं "
          "आपकी requirement समझना चाहूँगा — आप अभी कहाँ से बात कर रहे हैं, Vapi "
          "में ही या किसी और शहर से?")
Q_PURPOSE = "May I know, आप Vapi में property self-use के लिए consider कर रहे हैं या investment purpose से?"
Q_BHK = "Sure Sir. आप specifically 2 BHK consider कर रहे हैं या 3 BHK?"
Q_BUDGET = "Understood. आपका approximate budget range क्या comfortable रहेगा?"
Q_TIMELINE = ("Purchase का plan आपका immediate है, या next 3 से 6 months में "
              "consider कर रहे हैं?")
Q_FACTOR = ("आपके लिए property select करते वक़्त सबसे important factor क्या है "
            "— location, size, amenities, construction quality या investment "
            "value?")
Q_HORIZON = "आपका investment horizon approximately कितना है — 3 से 5 years या long-term?"

PITCH_SELF = (
    "Perfect Sir. आपकी requirement समझने के बाद मुझे लगता है कि Pramukh "
    "Blue Stone आपके consideration में होना चाहिए। New Chala, Vapi में ये एक "
    "commercial-cum-residential project है, और 2 और 3 BHK homes families के "
    "लिए spacious living देते हैं। <one or two of its pitch points from the "
    "KNOWLEDGE BASE that match what matters most to them — e.g. location → "
    "Muktanand Marg, one of the best locations in Vapi, the station about "
    "2 km; size → the 2 BHK's 1,360 sq ft super built-up, or two car "
    "parkings with every 3 BHK; amenities → gymnasium, banquet hall, open "
    "garden, jogging track; construction → Pramukh Group's record>। और ये "
    "project अभी under construction है, possession जल्द आने वाली है — तो site "
    "पर आप location, layout और construction progress ख़ुद evaluate कर सकते "
    "हैं।")
PITCH_INVEST = (
    "Understood Sir. Investment perspective से मैं आपको कोई unrealistic "
    "return promise नहीं करूँगा। Property evaluate करते वक़्त हम location, "
    "current market pricing, rental potential, demand और future resale "
    "perspective को consider करते हैं। Pramukh Blue Stone का 2 और 3 BHK "
    "segment, एक commercial-cum-residential project में, उन investors के लिए "
    "relevant हो सकता है जो Vapi में quality residential asset consider कर रहे "
    "हैं। आपका investment horizon approximately कितना है — 3 से 5 years या "
    "long-term?")
Q_KNOW_MORE = ("Sir, let me briefly explain — Pramukh Blue Stone का main "
               "proposition है: prime location, spacious 2 और 3 BHK homes, "
               "commercial-cum-residential project और established developer। "
               "आप इसके बारे में और क्या जानना चाहेंगे?")
Q_DOUBTS = "आप Pramukh Blue Stone के बारे में और क्या जानना चाहेंगे?"
L_INVITE = (
    "Sir, I don't want to take too much of your time over the phone. आपकी "
    "requirement के basis पर property relevant लग रही है, इसलिए मेरा "
    "suggestion है कि आप एक 20 से 30 minute site visit कर लीजिए — मैं "
    "personally आपको suitable 2 या 3 BHK options, floor plans, amenities और "
    "complete commercial details explain कर दूँगा, उसके बाद decision completely "
    "yours। Saturday evening या Sunday morning में से क्या convenient रहेगा?")
L_READBACK = ("बस confirm करने के लिए एक बार और बोल देता हूँ: <the day, the "
              "real date and the time — e.g. Saturday, 17 October, शाम 5 बजे>")
L_PRECLOSE = {
    "hi": "ठीक है जी, धन्यवाद। जाने से पहले क्या मैं आपकी और किसी चीज़ में मदद "
          "कर सकता हूँ?",
    "en": "Alright, thank you. Before I go, is there anything else I can "
          "help you with?"}
L_BYE = {"hi": "Thank you, आपका दिन शुभ हो।",
         "en": "Thank you, have a good day."}
# Call 1092 (2026-10-13): "अभी तो मैं नहीं आ सकती" → the close stage, and
# the model said only the pre-close line — the owner's "okay, I'll share
# the details on WhatsApp" was a secondary clause of the goal and was
# dropped. The decline now has its own stage whose first line is this one,
# spoken by the engine (`script.entry_line`, RUNBOOK §81); it ends on the
# pre-close question, so the caller's "no" closes the call.
L_DECLINED = {
    "hi": "ठीक है जी, कोई बात नहीं — मैं details WhatsApp पर share कर देता "
          "हूँ। जाने से पहले क्या मैं आपकी और किसी चीज़ में मदद कर सकता हूँ?",
    "en": "Alright, no problem — I'll share the details on WhatsApp. Before "
          "I go, is there anything else I can help you with?"}
# The second ask of "what matters most" admits it did not hear (call 1092:
# "Amenities" came back "हमें देखिए", and the model answered for her).
Q_FACTOR_REASK = ("माफ़ कीजिए, ठीक से सुनाई नहीं दिया — location, size, "
                  "amenities, construction quality या investment value?")
Q_FROM_REASK = ("माफ़ कीजिए, ठीक से सुनाई नहीं दिया — आप Vapi से ही बात कर "
                "रहे हैं या किसी और शहर से?")

# The size is always known by the time a price question comes (it is the
# second requirement question), so the authored line leaves that sentence
# out and the guidance adds it back only while the size is unknown.
A_PRICE = (
    "Certainly Sir. Exact pricing unit के configuration, floor, area और "
    "availability पर depend करेगी। मैं आपको approximate figure देकर confuse "
    "नहीं करना चाहता। मैं आपको available option का exact unit price, carpet "
    "area, floor और complete cost sheet share कर देता हूँ।")
A_PRICE_ASK_SIZE = "आप 2 BHK consider कर रहे हैं या 3 BHK?"
A_WHATSAPP = (
    "Absolutely Sir, मैं WhatsApp पर details share कर देता हूँ। मैं सिर्फ़ "
    "brochure forward नहीं करूँगा — relevant 2 या 3 BHK option, floor plan, "
    "carpet area और pricing भी share करूँगा। आप एक बार details review कर "
    "लीजिए। आपको details भेजने के बाद मैं आज evening में follow-up करूँ?")
A_PRICE_HIGH = (
    "I understand, Sir. Property में price को isolated number की तरह देखना "
    "appropriate नहीं होता। आपको carpet area, location, specifications, "
    "amenities, parking, construction quality और overall value के basis पर "
    "comparison करना चाहिए — मैं आपको complete cost sheet share कर देता हूँ "
    "ताकि आपका decision informed हो।")
A_LATER = (
    "Of course Sir, no problem. आपको अभी purchase decision लेने की ज़रूरत "
    "नहीं है। मैं details WhatsApp पर share कर देता हूँ, ताकि जब आप decision "
    "लें तब आपके पास proper comparison already हो।")


def _blue_stone(sheet_item: dict) -> dict:
    """The sheet's Blue Stone item. The approximate figure is NOT in the item
    text — it lives in pricing_policy.approx_line and reaches the model
    only through the engine's ━━ PRICE ━━ band on a second price ask.
    `budget_lakhs` stays the sheet's, so the budget check works."""
    v = copy.deepcopy(sheet_item)
    v["price_range"] = (
        "exact pricing depends on the configuration, floor, area and "
        "availability — exact unit price and complete cost sheet from the "
        "team; an approximate figure only when a ━━ PRICE ━━ note gives it")
    v["pitch_points"] = [
        "prime location — Muktanand Marg, New Chala, Vapi, one of the best "
        "locations in Vapi; the railway station about 2 km",
        "spacious residences — 2 BHK (1,360 sq ft super built-up, carpet "
        "about 816 sq ft) and 3 BHK (1,535 sq ft super built-up, carpet about "
        "921 sq ft); two car parkings with every 3 BHK",
        "a commercial-cum-residential project — commercial shops in the same "
        "project (shop sizes and prices from the team)",
        "an established developer — Pramukh Group, 30 years, 60-plus "
        "projects, not one stalled",
    ] + list(v.get("pitch_points") or [])
    return v


def _stages(old: list[dict], direction: str) -> list[dict]:
    by = {s["id"]: s for s in old}

    def st(sid, order, role, goal, collect, advance, turns, branches=None,
           src=None, script=None):
        base = copy.deepcopy(by.get(src or sid) or {})
        base.update({"id": sid, "order": order, "role": role, "goal": goal,
                     "collect": collect, "advance_when": advance,
                     "turns_until_advance": turns, "label": "",
                     "branches": branches or [], "script": script or {}})
        return base

    # RUNBOOK §75: the engine's floor under the model — the directive's
    # question is spoken verbatim when the reply left it out, and the
    # stage's follow-up line after every answer.
    ask = {"ask_verbatim": True}

    # Opening beats exactly as before (owner: "keep this part the same").
    stages: list[dict] = [copy.deepcopy(by["WARM_WELCOME"])]
    if direction == "outbound":
        stages.append(copy.deepcopy(by["INTRO_AND_TIME_CHECK"]))
    n = len(stages)
    declined = [
        {"when_slot": {"name": "site_visit_interest", "equals": v},
         "goto": "DECLINED_CLOSE", "max_takes": 1, "actions": []}
        for v in ("Maybe later", "No")]
    stages += [
        st("REQUIREMENT_FIRST", n, "discovery", (
            "They have agreed to talk. Say ONLY, word for word: "
            f"\"{L_OPEN}\" — nothing before it, nothing after it. If they "
            "ask something first, answer it plainly in one sentence, then "
            "say the line. Next turn, after a one-word acknowledgement of "
            f"where they are, ask word for word: \"{Q_PURPOSE}\" — their "
            "answer is the purpose; it is stored and decides the pitch. If "
            "the answer to 'where from' is not a place you can make out, "
            "ask it once more — never assume a city: say, word for word, "
            f"\"{Q_FROM_REASK}\" — and ask the purpose only after that "
            "(call 1092: 'ते मैं हूं' was taken as an answer and never "
            "re-asked)."),
            ["calling_from", "purpose"], {"all_collect_filled": True, "max_turns": 3}, 3,
            branches=[
                {"when_slot": {"name": "purpose", "equals": "Self-use"},
                 "max_takes": 1, "actions": ["retire_slots:investment_horizon"]},
                {"when_slot": {"name": "purpose", "equals": "Investment"},
                 "max_takes": 1, "actions": ["retire_slots:family_size"]},
            ], src="LET_THEM_TALK", script=ask),
        st("UNDERSTAND_REQUIREMENT", n + 1, "discovery", (
            "The requirement — one question per turn, in this order, "
            "skipping any whose answer is already ✓ in the CONVERSATION "
            "STATE, each one verbatim (the 'Suggested phrasing' there is the "
            "same line — say it word for word, not reworded): size → "
            f"verbatim: \"{Q_BHK}\" · budget → verbatim: \"{Q_BUDGET}\" · "
            f"timeline → verbatim: \"{Q_TIMELINE}\" · what matters most → "
            f"verbatim: \"{Q_FACTOR}\". The 'Sure Sir' of the size line is its "
            "reaction; before the other three, react to what they just said "
            "in a few plain words, then the line. If they want a commercial "
            "shop rather than a home, take 'Commercial' as the size, say the "
            "team shares shop sizes and pricing, and carry on with the next "
            "question. Take only a clear answer: a garbled word, a fragment "
            "or something unrelated is NOT an answer — say you didn't catch "
            "it and ask the same question once more in simpler words; never "
            "guess, never put a guess into the record. If they only say "
            "'hmm' or 'okay', ask the same question again — NEVER answer it "
            "on their behalf. What matters most to them is ONLY what the "
            "CONVERSATION STATE has ✓ — never say it for them ('आपके लिए "
            "location सबसे ज़्यादा मायने रखती है') when it is not there; if "
            "you did not catch it, say word for word: "
            f"\"{Q_FACTOR_REASK}\" To the timeline question, 'बस देख रहे "
            "हैं' / 'just looking' IS the answer (Just exploring) — take it "
            "and ask the next question. No WhatsApp, brochure or cost-sheet "
            "offer in this step — that comes after the pitch, when they ask. "
            "If they answer with a "
            "question, answer it first, then ask. Never ask a question whose "
            "answer is already known, even after other questions in between. "
            "If their budget does not fit Pramukh Blue Stone, a ━━ BUDGET FIT "
            "━━ note says what to suggest — one sentence, and switch only on a "
            "clear yes. Never quote a price here. No pitch, no visit yet."),
            ["configuration_required", "budget_range", "purchase_timeline",
             "decision_factor"],
            {"all_collect_filled": True, "max_turns": 8}, 8,
            src="SOFT_QUALIFICATION", script=ask),
        st("FIT_AND_PITCH", n + 2, "recommend", (
            "The requirement is complete — now the pitch, chosen by the ✓ "
            "Purpose in the CONVERSATION STATE. The project is Pramukh "
            "Blue Stone unless a ━━ PITCH SWITCHED ━━ note names another; "
            "every fact inside the line comes from THAT project's entry in "
            "the KNOWLEDGE BASE. If the purpose is Self-use, say ONLY: "
            f"\"{PITCH_SELF}\" If the purpose is Investment, say ONLY: "
            f"\"{PITCH_INVEST}\" Fill the <angle brackets> from the KNOWLEDGE "
            "BASE and keep every other word. The investor's answer to the "
            "horizon question is stored and acknowledged in a few words — it "
            "needs no reply of its own; the next step follows. If they "
            "answer with a question, answer it, then continue. Never a "
            "price figure unless a ━━ PRICE ━━ note gives it."),
            ["investment_horizon"],
            {"min_turns": 1, "all_collect_filled": True, "max_turns": 3}, 3,
            src="PRESENT_PROJECT", script=ask),
        st("KNOW_MORE", n + 3, "recommend", (
            f"Ask, word for word: \"{Q_KNOW_MORE}\" — then let them talk: "
            "this is where you understand what they actually need. "
            "Acknowledge what they say in a few plain words. Answer every "
            "question from the KNOWLEDGE BASE and the ━━ OBJECTION HANDLING "
            "━━ lines — price, WhatsApp, price too high, 'I'll see later', "
            "the shops, possession each have their wording there; use it, "
            "never an improvised version. After EVERY answer, ask word for "
            f"word: \"{Q_DOUBTS}\" — and keep doing that — answer, then that "
            "question — until they say they have no more questions. Do not "
            "invite them to visit here; the invitation is the next step. "
            "If they name another Pramukh project, answer about it from the "
            "KNOWLEDGE BASE and ask the same doubts question. Never a rough "
            "price for Pramukh Blue Stone before a ━━ PRICE ━━ note."),
            [], {"max_turns": 16}, 16,
            branches=[{"when_intent": "no_more_questions",
                       "goto": "INVITE_SITE_VISIT", "max_takes": 1,
                       "actions": []}],
            script={"after_answer": {"hi": Q_DOUBTS,
                                     "en": "Do you have any other question "
                                           "or doubt about this property "
                                           "that I can help you with?"},
                    # Not after the stage's own opening question, the
                    # WhatsApp line (its number check follows) or the
                    # "later" line (it promises the WhatsApp details).
                    "unless": [Q_KNOW_MORE, A_WHATSAPP, A_LATER]}),
        st("INVITE_SITE_VISIT", n + 4, "scheduling", (
            f"They have no more questions. Say ONLY, word for word: "
            f"\"{L_INVITE}\" When they give a day and time, reply ONLY "
            f"with, word for word: \"{L_READBACK}\" — and wait for their "
            "yes; nothing else in that reply (no 'fix कर देते हैं', no "
            "WhatsApp, no new question). NEVER say that read-back line "
            "without a time: if they give only a day, ask the time first "
            "(between 10 AM and 7 PM); only a time, ask the day. If a "
            "day and time was already fixed earlier in the call, say that "
            "read-back line now instead of the suggestion. If they would rather "
            "see details first, or can't or won't come now, accept it at "
            "once — the ━━ VISIT DECLINED ━━ note says what to say — and "
            "never invite them again on this call."),
            [], {"appointment_captured": True, "max_turns": 4}, 4,
            branches=declined),
        st("CONFIRM_VISIT", n + 5, "confirmation", (
            "A day and time for the visit is noted — it is in the call "
            "state. If your previous line did not read it back yet, say "
            f"ONLY, word for word: \"{L_READBACK}\" — then stop "
            "and wait for their answer. A yes or an okay confirms it and the "
            "next step wraps up. If they correct the day or the time, take "
            "the correction and say the corrected day, date and time once "
            "in that same sentence, then wait again. Nothing else in this "
            "turn — no WhatsApp, no who's coming along, no re-pitching."),
            [], {"intent": "affirmation", "max_turns": 3}, 3),
        st("CONFIRM_AND_CLOSE", n + 6, "confirmation", (
            f"Wrap up. Say, word for word: \"{L_PRECLOSE['hi']}\" — and "
            "never read the visit back again. If no visit was fixed, first "
            "confirm in one sentence what you'll send on WhatsApp and when "
            "you'll follow up, then say that line. If they raise a doubt or "
            "ask something, answer it exactly as the ━━ OBJECTION HANDLING "
            "━━ lines or the KNOWLEDGE BASE have it, and then say the same "
            "line again. When they say there is nothing else, say ONLY: "
            f"\"{L_BYE['hi']}\" and end the call — nothing after it, no new "
            "question."),
            [], {}, 99,
            script={"after_answer": dict(L_PRECLOSE),
                    "unless": [L_BYE["hi"], L_BYE["en"]]}),
        # Reached ONLY by the INVITE stage's decline branches (it is last,
        # so no stage advances into it).
        st("DECLINED_CLOSE", n + 7, "confirmation", (
            "They will not come for a visit now — accepted; never invite "
            "them again. Say ONLY, word for word: "
            f"\"{L_DECLINED['hi']}\" If they raise a doubt or ask "
            "something, answer it exactly as the ━━ OBJECTION HANDLING ━━ "
            "lines or the KNOWLEDGE BASE have it, then say, word for word: "
            f"\"{L_PRECLOSE['hi']}\" When they say there is nothing else, "
            f"say ONLY: \"{L_BYE['hi']}\" and end the call — nothing after "
            "it, no new question."),
            [], {}, 99,
            script={"entry_line": dict(L_DECLINED),
                    "after_answer": dict(L_PRECLOSE),
                    "unless": [L_BYE["hi"], L_BYE["en"], L_DECLINED["hi"],
                               L_DECLINED["en"]]}),
    ]
    return stages


def _slot(name, prio, label, q, typ="text", enum=None, syn=None,
          required=True, max_asks=1, depends=None, ask_gated=False):
    return {"name": name, "priority": prio, "required": required,
            "label": label, "question": q, "type": typ,
            "enum_values": enum or [], "enum_synonyms": syn or {},
            "extract_patterns": [], "depends_on": depends or [],
            "merge_policy": "free", "confirm_readback": "",
            "max_asks": max_asks, "reask_question": {},
            "summary_known": "", "summary_unknown": "",
            "ask_gated": bool(ask_gated)}


def _slots(old: list[dict]) -> list[dict]:
    by = {s["name"]: copy.deepcopy(s) for s in old}
    # The shared builder carries these two; this script defines its own
    # versions below, so take the inherited ones out.
    by.pop("decision_factor", None)
    by.pop("investment_horizon", None)
    by["family_size"]["required"] = False
    by["family_size"]["max_asks"] = 0
    by["calling_from"]["required"] = True     # owner: keep it, it is personal
    by["calling_from"]["max_asks"] = 2
    by["calling_from"]["question"] = {
        "hi": "आप अभी कहाँ से बात कर रहे हैं, Vapi में ही या किसी और शहर से?",
        "en": "Where are you calling from — Vapi itself, or another city?"}
    by["calling_from"]["reask_question"] = {
        "hi": Q_FROM_REASK,
        "en": "Sorry, I didn't catch that — are you calling from Vapi "
              "itself, or another city?"}
    by.pop("amenity_priorities", None)        # shares "सबसे ज़रूरी" (call 1074)
    # The owner's questions, word for word. max_asks 2: asked once, and once
    # more ONLY while still unanswered (a fragment is not an answer); a
    # stored answer is never asked again (CONVERSATION STATE ✓).
    by["purpose"]["question"] = {
        "hi": Q_PURPOSE,
        "en": "So are you looking at this property to live in yourself, or "
              "as an investment?"}
    by["purpose"]["priority"] = 2
    by["purpose"]["ask_gated"] = True          # + correction_gated (inherited)
    by["configuration_required"]["question"] = {
        "hi": Q_BHK,
        "en": "Sure Sir. Are you specifically considering a 2 BHK or a 3 BHK?"}
    by["configuration_required"]["max_asks"] = 2
    # Speech-to-text hears "तीन BHK" as "तीन भी है" (calls 1068, 1080); the
    # same shape for "दो BHK".
    csyn = by["configuration_required"].setdefault("enum_synonyms", {})
    csyn["2 BHK"] = list(csyn.get("2 BHK") or []) + [
        "दो भी", "2 भी", "टू बीएचके", "टू bhk", "do bhk"]
    csyn["3 BHK"] = list(csyn.get("3 BHK") or []) + [
        "तीन भी", "3 भी", "थ्री बीएचके", "थ्री bhk", "teen bhk"]
    csyn["Commercial"] = list(csyn.get("Commercial") or []) + [
        "shop", "shops", "दुकान", "दूकान", "commercial shop", "showroom",
        "office space", "दुकानें"]
    by["budget_range"]["question"] = {
        "hi": Q_BUDGET,
        "en": "What approximate budget range would be comfortable for you?"}
    by["budget_range"]["max_asks"] = 2
    # Once stored, overwritten only on an explicit correction ("change
    # करके", "नहीं, 3 BHK"). Call b5452b23: the caller QUOTING the agent's
    # price rewrote both the size and the budget, and the agent then cited
    # "आपका 85 लाख का budget".
    by["configuration_required"]["merge_policy"] = "correction_gated"
    by["budget_range"]["merge_policy"] = "correction_gated"
    by["purchase_timeline"]["question"] = {
        "hi": Q_TIMELINE,
        "en": "Is your purchase plan immediate, or are you considering it in "
              "the next 3 to 6 months?"}
    by["purchase_timeline"]["max_asks"] = 2
    by["purchase_timeline"]["ask_gated"] = True
    syn = by["purchase_timeline"]["enum_synonyms"]
    # Never the bare "अभी"/"abhi": "अभी तो नहीं आ सकती" (to the visit) was
    # stored as Immediate on call 1077.
    syn["Immediate"] = list(syn.get("Immediate") or []) + [
        "immediate", "turant hi", "jald se jald", "अभी तुरंत", "abhi turant"]
    syn["Within 6 months"] = list(syn.get("Within 6 months") or []) + [
        "3 se 6", "3-6", "3 से 6", "3 to 6", "three to six", "teen se chhe",
        "तीन से छह", "next 3", "next few months", "agle kuch mahine",
        "अगले कुछ महीने", "kuch mahine", "कुछ महीनों", "कुछ महीने"]
    # Call 1092: "अभी तो बस देख ही रही हूँ" to the timeline question was
    # not stored, and the agent offered WhatsApp mid-discovery instead.
    syn["Just exploring"] = list(syn.get("Just exploring") or []) + [
        "just looking", "बस देख", "देख ही रही", "देख ही रहे", "देख रही हूँ",
        "देख रहे हैं", "abhi dekh rahe", "bas dekh"]
    by["finalized_property"]["question"] = {
        "hi": "क्या आप एक बार site पर आकर ख़ुद देखना चाहेंगे?",
        "en": "Would you like to come and see the site yourself once?"}
    by["finalized_property"]["required"] = False   # written by the switch,
    by["finalized_property"]["max_asks"] = 0       # never asked
    order = ["caller_name", "calling_from", "purpose", "configuration_required",
             "budget_range", "purchase_timeline"]
    new = {
        "decision_factor": _slot(
            "decision_factor", 6, "What matters most",
            {"hi": Q_FACTOR,
             "en": "What is the most important factor for you when selecting "
                   "a property — location, size, amenities, construction "
                   "quality or investment value?"},
            typ="enum", max_asks=2, ask_gated=True,
            enum=["Location", "Size", "Amenities", "Construction quality",
                  "Investment value"],
            syn={"Location": ["location", "lokesan", "jagah", "area",
                              "लोकेशन", "जगह", "इलाका"],
                 "Size": ["size", "space", "bada ghar", "sizes", "साइज़",
                          "साइज", "बड़ा घर", "जगह ज़्यादा"],
                 "Amenities": ["amenities", "amenity", "facilities",
                               "suvidha", "एमेनिटीज़", "सुविधा", "सुविधाएं"],
                 "Construction quality": ["construction", "quality",
                                          "build quality", "कंस्ट्रक्शन",
                                          "क्वालिटी", "मज़बूती"],
                 "Investment value": ["investment value", "returns",
                                      "appreciation", "resale", "रिटर्न",
                                      "इन्वेस्टमेंट वैल्यू"]}),
        "investment_horizon": _slot(
            "investment_horizon", 7, "Investment horizon",
            {"hi": Q_HORIZON,
             "en": "What is your approximate investment horizon — 3 to 5 "
                   "years, or long-term?"},
            typ="enum", max_asks=2, ask_gated=True,
            enum=["3-5 years", "Long-term"],
            syn={"3-5 years": ["3 se 5", "3-5", "teen se paanch", "3 साल",
                               "5 साल", "तीन से पांच", "पांच साल", "3 to 5",
                               "3 से 5", "5 years", "3 years", "5 साल तक"],
                 "Long-term": ["long term", "long-term", "lambe samay",
                               "लंबे समय", "लॉन्ग टर्म", "10 साल", "hamesha",
                               "long", "लंबा"]}),
    }
    df = new["decision_factor"]
    df["max_asks"] = 3
    df["reask_question"] = {
        "hi": Q_FACTOR_REASK,
        "en": "Sorry, I didn't catch that — location, size, amenities, "
              "construction quality or investment value?"}
    # STT under hi-IN spells the English option words in Devanagari.
    dsyn = df["enum_synonyms"]
    dsyn["Amenities"] = list(dsyn.get("Amenities") or []) + [
        "अमेनिटी", "अमेनिटीज़", "अमेनिटीज", "अमेनिटिज", "एमेनिटी",
        "एमेनिटीज", "मैनिटीज़", "मैनिटीज", "एमिनिटीज"]
    dsyn["Location"] = list(dsyn.get("Location") or []) + ["लोकेसन", "लोकेशन्स"]
    dsyn["Construction quality"] = list(
        dsyn.get("Construction quality") or []) + ["कंस्ट्रक्शन क्वालिटी",
                                                  "क्वॉलिटी"]
    out: list[dict] = []
    for i, n in enumerate(order):
        s = by.pop(n)
        s["priority"] = i + 1
        out.append(s)
    out += [new["decision_factor"], new["investment_horizon"]]
    for i, s in enumerate(out):
        s["priority"] = i + 1
    rest = list(by.values())
    for j, s in enumerate(rest):
        s["priority"] = len(out) + j + 1
    return out + rest


def _playbook(old: list[dict]) -> list[dict]:
    keep = {"discuss_with_family", "comparing_builders", "builder_reliability",
            "other_project_question", "what_options", "how_got_number",
            "project_details", "needs_different_size", "illegal_request"}
    out = [copy.deepcopy(e) for e in old if e["id"] in keep]
    for e in out:
        if e["id"] == "project_details":
            e["prompt_guidance"] = (
                "'Tell me about Blue Stone' / 'how big' / 'how many "
                "buildings' → the two or three facts that fit what they told "
                "you, in round spoken numbers. Never a price figure unless a "
                "━━ PRICE ━━ note gives it; the status is 'under construction, "
                "possession coming soon' exactly — never which units are "
                "ready or open.")

    def add(pid, g):
        out.insert(0, {"id": pid, "patterns": [], "answer": {},
                       "prompt_guidance": g})
    # The owner's exact answers (2026-10-12, Aristo script). Script fidelity:
    # a 'verbatim:' line is spoken word for word; only the <bracketed>
    # omission is allowed.
    add("decide_later", (
        "'Main baad mein dekhunga' / 'बाद में देखूँगा' / 'I'll think about "
        f"it' → verbatim: \"{A_LATER}\" No visit suggestion here."))
    # The doubts question after each answer is the KNOW_MORE stage's
    # `script.after_answer` — the engine speaks it when the model leaves it
    # out, so the answers stay the owner's plain lines.
    add("price_high", (
        "'Price high hai' / 'महँगा है' / 'too expensive' → verbatim: "
        f"\"{A_PRICE_HIGH}\" If their budget fits another Pramukh project "
        "better, the ━━ BUDGET FIT ━━ note will say so."))
    # The platform must know the WhatsApp number before it can send (§72):
    # the owner's line is kept whole and the number check rides on its end
    # the first time, as the ━━ WHATSAPP ━━ note asks; the doubts question
    # follows on the next reply, after they answer.
    add("whatsapp_details", (
        "'WhatsApp kar do' / 'details भेज दो' / 'send me the brochure' → "
        f"verbatim: \"{A_WHATSAPP}\" — and, in the same breath, ONLY if the "
        "━━ WHATSAPP ━━ note says the number is not confirmed yet, add: "
        "\"और क्या यही नंबर WhatsApp पर है?\" Nothing else in that reply; "
        "the doubts question comes on your next reply, once they have "
        "answered."))
    add("price_question", (
        "'Sir, price kya hai?' / 'कितने का है?' / 'rate बताओ' → verbatim: "
        f"\"{A_PRICE}\" — only if their size is NOT yet in the CONVERSATION "
        f"STATE, insert \"{A_PRICE_ASK_SIZE}\" before the last sentence. "
        "Never a question of your own in its place, and no number in this "
        "first answer. If they ask AGAIN for an approximate figure, a "
        "━━ PRICE ━━ note gives it — say that figure once, calling it "
        "approximate, and that the exact price comes with the cost sheet."))
    add("investment_returns", (
        "'Will the price double?' / 'what returns?' → you won't promise "
        "returns or appreciation. A sound investment decision rests on "
        "location, current pricing, demand, rental potential and resale "
        "prospects — offer to help them evaluate those, and ask their "
        "investment horizon if you don't know it."))
    # Blue Stone's own two: it is commercial-cum-residential, and it is
    # under construction. Both answers are the sheet's words and nothing
    # more — shop sizes / prices and the possession date are NOT published.
    add("commercial_shops", (
        "'Shop भी है?' / 'दुकान' / 'commercial space' / 'office' → yes — "
        "Pramukh Blue Stone is a commercial-cum-residential project with "
        "commercial shops in it. Shop sizes and pricing are not published: "
        "say the team shares them, in one sentence. Never a shop size, a "
        "shop price or a count of shops."))
    add("possession_when", (
        "'Possession कब?' / 'कब तक ready होगा?' / 'ready है?' → exactly as "
        "the KNOWLEDGE BASE has it: under construction, possession coming "
        "soon; the exact date the team confirms. Never a month, a year, "
        "'a few months', 'some units are ready' or 'near possession'."))
    return out


def build(direction: str, ravi: dict, all_items: list[dict]) -> dict:
    proj = next(p for p in yaml.safe_load(bpp.SHEET.read_text(encoding="utf-8"))
                ["projects"] if p["key"] == KEY)
    approx = str(proj["item"].get("price_range") or "").strip()
    bst = _blue_stone(proj["item"])
    items = [bst if i["id"] == ITEM_ID else i for i in all_items]
    d = bpp.build(direction, ravi, {**proj, "item": bst}, items)
    d["agent_id"] = f"pramukh_blue_stone_test-{direction}-v1"
    d["identity"]["role_description"] = (
        "Pramukh Blue Stone sales consultant (scripted-pitch test) — "
        + ("follow-up calls" if direction == "outbound" else "inbound"))

    cf = d["call_flow"]
    cf["stages"] = _stages(cf["stages"], direction)
    cf["call_goal"] = (
        "Help this caller evaluate whether Pramukh Blue Stone is the right "
        "property for their requirement — the owner's script, line by line: "
        "understand the requirement first (purpose, 2 or 3 BHK, budget, "
        "timeline, what matters most), pitch by purpose from the knowledge "
        "base, answer every question until they have none, invite them to a "
        "20–30 minute site visit, confirm the day and time once, ask if "
        "anything else is needed, and close. Never sell a flat over the "
        "phone.")
    # RUNBOOK §75: the two-step close the owner scripted — the pre-close
    # question is asked before every goodbye, and the caller's "no, nothing
    # else" ends the call on the closing line with no "shall I cut the
    # call?" in between.
    cf["termination"]["confirm_before_end"] = False
    cf["termination"]["pre_close_question"] = dict(L_PRECLOSE)
    cf["termination"]["end_conditions"] = [
        "once the site visit is read back and confirmed, the pre-close "
        "question is asked and the caller has nothing else, say the closing "
        "line and end",
        "if the caller has clearly declined a visit and the WhatsApp "
        "follow-up is confirmed, ask the pre-close question, then close",
    ]
    # RUNBOOK §78 (call b5452b23, 2026-10-12): the owner's turn-taking
    # request, on the test twins only — every pause held before answering,
    # a back-channel "हाँ" never a turn, real speech stops our audio.
    cf["turn_taking"] = {
        "pause_hold_ms": 1500,
        "max_merged_fragments": 6,
        "backchannel_guard": True,
        "sustained_barge_ms": 700,
        "tail_barge_in": True,
    }
    # The scripted lines run past 600 characters (PITCH_SELF); the
    # platform's 400-character cap sliced the Vedanta pitch mid-sentence.
    d["llm_config"]["max_response_length"] = 760
    d["llm_config"]["max_tokens"] = 320
    d["slots"] = _slots(d["slots"])
    # RUNBOOK §81: a short answer to an option question that matches no
    # option under the hi-IN lock is re-read once without it ("Amenities").
    d["languages"]["stt_enum_recheck"] = True

    kb = d["knowledge_base"]
    # The script's line first; the sheet's approximate figure on a SECOND
    # price ask, through the engine's ━━ PRICE ━━ band (owner, 2026-10-12).
    kb["pricing_policy"] = {"mode": "on_request", "defer_line": {},
                            "second_ask_approx": True, "approx_line": approx}
    kb["qa_field_signals"].pop("amenity_priorities", None)
    # Devanagari spellings STT is likely to give "Blue Stone" (the shared
    # builder has ब्लू स्टोन / ब्लूस्टोन; these are the plural and short-vowel
    # shapes — no Blue Stone call has had the caller say the name yet, so
    # they are unverified; harmless, they only rewrite a project name).
    kb.setdefault("stt_corrections", {}).setdefault(
        "project_name_corrections", {}).update({
            "ब्लू स्टोन्स": "Blue Stone", "ब्लू स्टोंस": "Blue Stone",
            "ब्लु स्टोन": "Blue Stone", "ब्लू इस्टोन": "Blue Stone",
            "blu stone": "Blue Stone",
            "प्रमुख ब्लू स्टोन": "Pramukh Blue Stone"})
    sig = kb["qa_field_signals"]
    sig["purpose"] = ["ख़ुद रहने के लिए", "khud rehne", "investment के लिए",
                      "live in yourself", "as an investment"]
    sig["configuration_required"] = ["2 bhk consider", "या 3 bhk", "2 BHK or",
                                     "specifically 2", "कितने bhk", "bhk"]
    sig["budget_range"] = ["budget range", "approximate budget", "comfortable"]
    sig["purchase_timeline"] = ["immediate", "3 से 6 months", "3 to 6 months",
                                "purchase का plan", "purchase plan"]
    sig["decision_factor"] = ["important factor", "सबसे important",
                              "most important", "location, size"]
    sig["investment_horizon"] = ["investment horizon", "3 से 5 years",
                                 "3 to 5 years", "long-term", "कितने समय"]

    rb = d["response_bank"]
    rb["objection_playbook"] = _playbook(rb["objection_playbook"])
    rb["closing_responses"] = dict(L_BYE)

    d["personality"]["custom_instructions"] = (
        "You are Prashant, Pramukh Blue Stone's consultant — professional, "
        "warm and never pushy. This call follows the owner's script: the "
        "lines quoted in the CURRENT GOAL and in OBJECTION HANDLING are said "
        "word for word, in Hindi with the English words left in English, and "
        "everything in between — the reaction to what the person just said, "
        "an answer to a question of theirs — is short, plain and human. You "
        "are not trying to sell a flat over a phone call; you are helping "
        "this person evaluate whether Pramukh Blue Stone is the right "
        "property for their requirement. So you understand the requirement "
        "first — purpose, 2 or 3 BHK, budget, timeline, what matters most — "
        "one question at a time, and only then the pitch, built from the "
        "KNOWLEDGE BASE and tied to what they told you. What they tell you "
        "stays told: it is in the CONVERSATION STATE, you never ask for it "
        "again even after other questions, and you use it when you suggest "
        "or compare properties. You only record what you clearly heard and "
        "that makes sense as an answer — a garbled or unrelated fragment is "
        "not an answer; you say you didn't catch it and ask once more. You "
        "give no price figure on the first ask — exact unit price and "
        "complete cost sheet — and the approximate figure only when a "
        "━━ PRICE ━━ note gives it. Pramukh Blue Stone is under construction "
        "with possession coming soon — you say exactly that, never which "
        "units are ready, and never a date the KNOWLEDGE BASE does not have. "
        "It is a commercial-cum-residential project: if they ask about the "
        "shops, you say they are part of the project and that shop sizes "
        "and pricing come from the team. You know every Pramukh project in "
        "Vapi in the KNOWLEDGE BASE and answer questions about them plainly; "
        "you suggest another one only when a ━━ BUDGET FIT ━━ note says "
        "their budget fits it better, and pitch it only after a clear yes. "
        "After every answer you ask whether they have any other question, "
        "until they say no; then the visit; then one read-back; then whether "
        "there is anything else; then the closing line. You use their name "
        "at most twice in the call, never greet twice, never read a date "
        "back twice. You are a person on the phone, not a form.")

    gr = d["guardrails"]
    drop = ("invent a price", "say 'when can you come?'",
            "pitch before the requirement")
    gr["never_rules"] = [r for r in gr["never_rules"]
                         if not r.startswith(drop)] + [
        "give a price figure for Pramukh Blue Stone unless a ━━ PRICE ━━ note "
        "gives it — the first answer is the exact unit price and complete "
        "cost sheet",
        "suggest or mention a site visit before the invitation step — the "
        "visit is the LAST step, after they say they have no more questions",
        "say a caller's answer back to them, or praise it — a word of "
        "acknowledgement, then the next line",
        "invent a size, floor count, possession date, availability, amenity "
        "or RERA number beyond what the KNOWLEDGE BASE states — the pitch "
        "template is filled from the KNOWLEDGE BASE, never from memory",
        "say that any unit of Pramukh Blue Stone is ready, near possession, "
        "available or sold out — it is under construction with possession "
        "coming soon, exactly as the KNOWLEDGE BASE states; the date and "
        "which units are open come from the team",
        "give a shop size, shop price or shop count — the shops exist, their "
        "details come from the team",
        "pressure the caller — no 'flat le lo', no 'best project', no 'your "
        "investment will double', no 'book today or the price will go up'",
        "promise returns, appreciation or rental income",
        "pitch before the requirement is complete — purpose, size, budget, "
        "timeline and what matters most",
        "move to the site-visit invitation while a question or doubt of "
        "theirs is still open — after every answer, ask whether they have "
        "any other question, until they say no",
        "end the call without asking the pre-close question first, or say "
        "anything after the closing line",
        "say what matters most to the caller, or any answer of theirs, that "
        "the CONVERSATION STATE does not have ✓ — a garbled reply is asked "
        "again, never answered for them",
        "give the super built-up area when the CARPET area is asked — the "
        "carpet figure is in the KNOWLEDGE BASE specifications; name which "
        "one you are saying",
    ]

    ob = d["outbound"]
    if direction == "outbound":
        # Unchanged shape (owner: "keep this part the same").
        ob["good_time_check_line"] = {
            "hi": "शुक्रिया! मैं प्रशांत बोल रहा हूँ Pramukh Blue Stone, New "
                  "Chala, Vapi से — आपने कुछ समय पहले हमारे project में "
                  "interest दिखाया था। अभी दो मिनट बात करना ठीक रहेगा?",
            "en": "Thanks! It's Prashant from Pramukh Blue Stone, New Chala, "
                  "Vapi — you'd shown interest in our project a while back. "
                  "Is now an okay time for two minutes?"}
    else:
        cf["connect"]["intro_line"] = {
            "hi": "नमस्ते, मैं प्रशांत बोल रहा हूँ Pramukh Blue Stone, New "
                  "Chala, Vapi से — बताइए, मैं आपकी क्या मदद कर सकता हूँ?",
            "en": "Hello, this is Prashant from Pramukh Blue Stone, New "
                  "Chala, Vapi — how can I help you?"}
    ob["direction_brief"] = (
        ("You're following up with someone who enquired about Pramukh "
         "Blue Stone. " if direction == "outbound" else
         "Someone has called about Pramukh Blue Stone. ")
        + "Follow the owner's script: understand their requirement first, "
        "pitch by purpose, answer every question, invite them to evaluate it "
        "in person, confirm once, ask if anything else is needed, close. ON "
        "A FOLLOW-UP CALL (you already spoke and shared details): say you're "
        "following up on the details shared, ask whether they had a chance "
        "to review them and which aspect needs clarity — location, "
        "configuration or pricing — and offer the exact availability and "
        "costing for the option they liked. If they sound unsure, say you "
        "don't want to disturb them repeatedly and simply ask their current "
        "status — actively looking, planning later, or not looking right "
        "now — and respect the answer.")

    d["follow_up"]["whatsapp_promise"]["assets"] = [
        "Pramukh Blue Stone site location pin",
        "floor plan of the 2 or 3 BHK option discussed",
        "exact unit price, area, floor and complete cost sheet",
        "Prashant's direct number"]
    return d


def _register(slug: str) -> None:
    from shared.db.database import get_db
    with get_db() as db:
        db.execute("INSERT INTO agents (slug, owner, display_name, is_active) "
                   "VALUES (?,?,?,0) ON CONFLICT (slug) DO NOTHING",
                   (slug, OWNER, DISPLAY))
        db.execute("UPDATE agents SET owner=?, is_active=0 WHERE slug=?",
                   (OWNER, slug))
        row = db.execute("SELECT owner FROM agents WHERE slug=?",
                         (slug,)).fetchone()
        assert row and row["owner"] == OWNER, (slug, dict(row) if row else None)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not (args.dry_run or args.apply):
        ap.error("--dry-run or --apply")
    sheet = yaml.safe_load(bpp.SHEET.read_text(encoding="utf-8"))
    all_items = [p["item"] for p in sheet["projects"]]
    agents_dir = (Path(__file__).resolve().parent / "agents_dry_blue_stone_test"
                  if args.dry_run else bpp.LIVE_AGENTS)
    if args.dry_run:
        shutil.rmtree(agents_dir, ignore_errors=True)
        agents_dir.mkdir()
    for direction, ravi_slug in bpp.RAVI.items():
        ravi = yaml.safe_load((bpp.LIVE_AGENTS / ravi_slug / "agent.yaml")
                              .read_text(encoding="utf-8"))
        new = build(direction, ravi, all_items)
        slug = SLUG.format(direction=direction)
        path = agents_dir / slug / "agent.yaml"
        if path.exists():
            rev = store.write_agent(slug, new, actor=ACTOR, reason=REASON,
                                    owner=OWNER if args.apply else "",
                                    is_admin=True,
                                    agents_dir=agents_dir)["revision"]
        else:
            store.assert_direction_matches_slug(slug, new)
            new.update(revision=1, updated_at=store._now_iso(),
                       updated_by=ACTOR)
            agent = AgentDefinition.from_dict(new)
            path.parent.mkdir(parents=True, exist_ok=True)
            agent.to_yaml(path)
            rev = 1
        if args.apply:
            _register(slug)
            try:
                from backend.app.agent_definition import registry
                registry.invalidate(slug)
            except Exception:
                pass
        print(f"{slug}: revision {rev}")


if __name__ == "__main__":
    main()
