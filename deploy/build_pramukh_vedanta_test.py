"""Ravi (test) — Pramukh Vedanta on the owner's scripted sales pitch.

A TEST twin of the Vedanta agent (pramukh_vedanta-*) on angelo@dhronai.com:
the same persona, shared knowledge, Hindi-only voice handling and budget
switch as the fleet (build_pramukh_projects.py), with the call flow the
owner scripted line by line (2026-10-07 ideal script; rewritten to the
owner's exact wording 2026-10-12 — RUNBOOK §75):

  * opening beats UNCHANGED — hello → identity → the good-time line;
  * one authored line opens the requirement (3 and 4 BHK homes in New
    Chala → self-use or investment), then four questions in the owner's
    words, one per turn: 3 or 4 BHK → budget range → immediate or 3–6
    months → what matters most. Each answer is STORED, never re-asked, and
    only taken when it is a real answer (ask-gated slots, closed
    vocabularies, the money check);
  * the pitch by purpose, as a template the model fills from the KNOWLEDGE
    BASE (project name, configurations, amenities, status) — self-use ends
    on the site-visit suggestion, investment ends on the horizon question;
  * "want to know more about <project>, or another project?" → open Q&A
    with the owner's exact objection answers (price, WhatsApp, price high,
    later) → after EVERY answer "any other question or doubt?" until the
    caller says no (engine: `when_intent: no_more_questions` branch);
  * the owner's invitation (20–30 min visit, "any date and time in mind?")
    → the day/date/time read back ONCE as "बस confirm करने के लिए…" → their
    yes → "जाने से पहले… और किसी चीज़ में मदद?" → "Thank you, आपका दिन शुभ हो"
    and the call ends (no "shall I cut the call?" in between:
    confirm_before_end off, pre_close_question on).

FACTS ARE THE SHEET'S (deploy/pramukh_vapi_projects.yaml) — RUNBOOK §72.
The owner's pitch template named clubhouse / pool / toddler zone / servant
room; the sheet lists other amenities for Vedanta, so the template takes its
amenities from the KNOWLEDGE BASE. Only the script's PRICING POLICY survives
from the old Vedanta fact-set: no approximate Vedanta figure, exact unit
price + cost sheet instead (budget_lakhs keeps the budget check).

    docker exec -i lescall-api python - --apply < deploy/build_pramukh_vedanta_test.py
    calling-agent/venv/bin/python deploy/build_pramukh_vedanta_test.py --dry-run
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

SLUG = "pramukh_vedanta_test-{direction}"
DISPLAY = "Ravi test"
ACTOR = "angelo@dhronai.com:vedanta-scripted-pitch-2026-10-12"
REASON = "Vedanta test agent on the owner's scripted sales pitch (RUNBOOK §75)"
P = "Pramukh Vedanta"

# ---------------------------------------------------------------------------
# The owner's lines (2026-10-12), in native script with English words in
# Latin (Sarvam degrades on romanised Hindi — CLAUDE.md). "Sir" is held to
# contacts.gender by the engine (Sir / Ma'am / nothing — RUNBOOK §42).
# ---------------------------------------------------------------------------
L_OPEN = ("Pramukh Vedanta में हम 3 और 4 BHK homes लेकर आए हैं, New Chala, "
          "Vapi में — तो आप ये property ख़ुद रहने के लिए देख रहे हैं, या "
          "investment के लिए?")
Q_PURPOSE = "तो आप ये property ख़ुद रहने के लिए देख रहे हैं, या investment के लिए?"
Q_BHK = "Sure Sir. आप specifically 3 BHK consider कर रहे हैं या 4 BHK?"
Q_BUDGET = "आपका approximate budget range क्या comfortable रहेगा?"
Q_TIMELINE = ("Purchase का plan आपका immediate है, या next 3 से 6 months में "
              "consider कर रहे हैं?")
Q_FACTOR = ("आपके लिए property select करते वक़्त सबसे important factor क्या है "
            "— location, size, amenities, construction quality या investment "
            "value?")
Q_HORIZON = "आपका investment horizon approximately कितना है — 3 से 5 years या long-term?"

PITCH_SELF = (
    "Perfect Sir. आपकी requirement समझने के बाद मुझे लगता है कि <project "
    "name> आपके consideration में होना चाहिए। Property में आपको <its "
    "configurations, e.g. 3 BHK, 4 BHK और Ultra 4 BHK> residences मिल "
    "जाएँगे। इस project की amenities में आपको <four or five of its amenities "
    "from the KNOWLEDGE BASE, the ones closest to what matters most to them> "
    "मिल जाएगा। ये project अभी <its status exactly as the KNOWLEDGE BASE "
    "states it — ready, under construction, or both> है, तो आपको सारी चीज़ें "
    "evaluate करने के लिए एक site visit काफ़ी useful रहेगी। मेरा suggestion "
    "रहेगा कि आप एक बार property personally visit करें। उसके बाद आपको "
    "comparison करना भी easy रहेगा।")
PITCH_INVEST = (
    "Understood Sir. Investment perspective से मैं आपको कोई unrealistic "
    "return promise नहीं करूँगा। Property evaluate करते वक़्त हम location, "
    "current market pricing, rental potential, demand और future resale "
    "perspective को consider करते हैं। <project name> उन investors के लिए "
    "perfect है जो <its locality, e.g. New Chala> में एक larger residential "
    "asset की खोज में हैं। आपका investment horizon approximately कितना है — "
    "3 से 5 years या long-term?")
Q_KNOW_MORE = ("क्या आप <project name> के बारे में थोड़ा और जानना चाहेंगे, या "
               "आपका interest किसी और project में है?")
Q_DOUBTS = ("क्या आपके पास इस property के बारे में और कोई सवाल या doubt है, "
            "जिसमें मैं आपकी मदद कर सकता हूँ?")
L_INVITE = (
    "Sir, मैं आपका ज़्यादा time नहीं लेना चाहता। आपकी requirement के basis पर "
    "property relevant लग रही है। इसलिए मेरा suggestion है कि आप एक 20 से "
    "30 minute की site visit कर लीजिए। मैं personally आपको suitable options, "
    "floor plans, amenities और complete commercial details explain कर दूँगा। "
    "उसके बाद decision आपका है। क्या कोई date और time है आपके दिमाग़ में site "
    "visit के लिए?")
L_READBACK = ("बस confirm करने के लिए एक बार और बोल देता हूँ: <the day, the "
              "real date and the time — e.g. Saturday, 17 October, शाम 5 बजे>")
L_PRECLOSE = {
    "hi": "ठीक है जी, धन्यवाद। जाने से पहले क्या मैं आपकी और किसी चीज़ में मदद "
          "कर सकता हूँ?",
    "en": "Alright, thank you. Before I go, is there anything else I can "
          "help you with?"}
L_BYE = {"hi": "Thank you, आपका दिन शुभ हो।",
         "en": "Thank you, have a good day."}

# The owner's line asks "3 BHK या 4 BHK?" in the middle; by the time a
# price question comes the size is always known (it is the second
# requirement question), so the authored line leaves that sentence out and
# the guidance adds it back only while the size is unknown.
A_PRICE = (
    "Certainly Sir. Exact pricing unit के configuration, floor, area और "
    "availability पर depend करेगी। मैं आपको approximate figure देकर confuse "
    "नहीं करना चाहता। मैं आपको available option का exact unit price, area, "
    "floor और complete cost sheet share कर देता हूँ।")
A_PRICE_ASK_SIZE = "आप 3 BHK consider कर रहे हैं या 4 BHK?"
A_WHATSAPP = (
    "Absolutely Sir, मैं WhatsApp पर details share कर देता हूँ। मैं आपको "
    "सिर्फ़ brochure forward नहीं करूँगा। मैं relevant 3 या 4 BHK option, "
    "floor plan, area और pricing भी share करूँगा। आप एक बार details review "
    "कर लीजिए। उसके बाद अगर property आपके criteria में fit होती है, तो हम "
    "site visit schedule कर लेंगे। आपको details भेजने के बाद मैं आज evening "
    "में follow-up करूँ?")
A_PRICE_HIGH = (
    "I understand, Sir. Premium property में price को isolated number की "
    "तरह देखना appropriate नहीं होता। आपको area, location, specifications, "
    "amenities, parking, construction quality और overall value के basis पर "
    "comparison करना चाहिए। मेरा suggestion है कि आप एक बार property "
    "personally evaluate कीजिए। अगर visit के बाद आपको लगे कि value आपके "
    "budget के according justify नहीं होती, at least आपका decision informed "
    "होगा।")
A_LATER = (
    "Of course Sir, no problem. आपको अभी purchase decision लेने की ज़रूरत "
    "नहीं है। बस एक चीज़ suggest करूँगा — अगर आप genuinely Vapi में 3 या 4 "
    "BHK consider कर रहे हैं, तो एक short site visit कर लेना useful रहेगा। "
    "आप property को physically evaluate कर लेंगे और future में जब decision "
    "लेंगे, तब आपके पास proper comparison already होगा। आपके लिए weekend "
    "better रहेगा या weekday?")


def _vedanta(sheet_item: dict) -> dict:
    """The sheet's Vedanta item with ONLY the ideal script's pricing policy
    on top. Sizes, status, acreage, amenities and distances are the owner's
    sheet, never a second copy."""
    v = copy.deepcopy(sheet_item)
    v["price_range"] = (
        "exact pricing depends on the configuration, floor, area and "
        "availability — shared as the exact unit price and a complete cost "
        "sheet, never as a rough figure")
    v["pitch_points"] = [
        "a prime location in New Chala, Vapi",
        "spacious residences — 3 BHK, 4 BHK and Ultra 4 BHK of about 1,965 "
        "to 3,890 sq ft super built-up",
        "lifestyle amenities — theatre, café, games zone, spa, banquet hall "
        "and four kinds of sports courts",
        "an established developer — Pramukh Group, 30 years and not one "
        "stalled project",
    ] + list(v.get("pitch_points") or [])
    v["not_published"] = [
        "any approximate or 'starting from' price — exact unit pricing comes "
        "with the cost sheet",
    ] + list(v.get("not_published") or [])
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
         "goto": "CONFIRM_AND_CLOSE", "max_takes": 1, "actions": []}
        for v in ("Maybe later", "No")]
    stages += [
        st("REQUIREMENT_FIRST", n, "discovery", (
            "They have agreed to talk. Say ONLY, word for word: "
            f"\"{L_OPEN}\" — nothing before it, nothing after it. If they "
            "ask something first, answer it plainly in one sentence, then "
            "say the line. Their answer — self-use or investment — is the "
            "purpose: it is stored and it decides the pitch later."),
            ["purpose"], {"all_collect_filled": True, "max_turns": 3}, 3,
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
            "in a few plain words, then the line. Take only a clear answer: "
            "a garbled word, a fragment "
            "or something unrelated is NOT an answer — say you didn't catch "
            "it and ask the same question once more in simpler words; never "
            "guess, never put a guess into the record. If they only say "
            "'hmm' or 'okay', ask the same question again — NEVER answer it "
            "on their behalf. If they answer with a "
            "question, answer it first, then ask. Never ask a question whose "
            "answer is already known, even after other questions in between. "
            "If their budget does not fit Pramukh Vedanta, a ━━ BUDGET FIT ━━ "
            "note says what to suggest — one sentence, and switch only on a "
            "clear yes. Never quote a Vedanta price. No pitch, no visit yet."),
            ["configuration_required", "budget_range", "purchase_timeline",
             "decision_factor"],
            {"all_collect_filled": True, "max_turns": 8}, 8,
            src="SOFT_QUALIFICATION", script=ask),
        st("FIT_AND_PITCH", n + 2, "recommend", (
            "The requirement is complete — now the pitch, chosen by the ✓ "
            "Purpose in the CONVERSATION STATE. The project is Pramukh "
            "Vedanta unless a ━━ PITCH SWITCHED ━━ note names another; every "
            "fact inside the line comes from THAT project's entry in the "
            "KNOWLEDGE BASE. If a ━━ BUDGET FIT ━━ note says their range does "
            "not fit Pramukh Vedanta and names other projects, do NOT pitch "
            "Vedanta: ask in one short sentence which of those they would "
            "like to hear about, and pitch THAT one with its own facts once "
            "they choose. If the purpose is Self-use, say ONLY: "
            f"\"{PITCH_SELF}\" If the purpose is Investment, say ONLY: "
            f"\"{PITCH_INVEST}\" Fill the <angle brackets> from the KNOWLEDGE "
            "BASE and keep every other word. The investor's answer to the "
            "horizon question is stored and acknowledged in a few words — it "
            "needs no reply of its own; the next step follows. If they "
            "answer with a question, answer it, then continue. Never a rough "
            "price: exact unit price and cost sheet only."),
            ["investment_horizon"],
            {"min_turns": 1, "all_collect_filled": True, "max_turns": 3}, 3,
            src="PRESENT_PROJECT", script=ask),
        st("KNOW_MORE", n + 3, "recommend", (
            f"Ask, word for word: \"{Q_KNOW_MORE}\" — then let them talk: "
            "this is where you understand what they actually need. "
            "Acknowledge what they say in a few plain words. Answer every "
            "question from the KNOWLEDGE BASE and the ━━ OBJECTION HANDLING "
            "━━ lines — price, WhatsApp, price too high, 'I'll see later' "
            "each have their exact wording there; use it, never an "
            "improvised version. After EVERY answer, ask word for word: "
            f"\"{Q_DOUBTS}\" — and keep doing that — answer, then that "
            "question — until they say they have no more questions. Do not "
            "invite them to visit here; the invitation is the next step. "
            "If they name another Pramukh project, answer about it from the "
            "KNOWLEDGE BASE and ask the same doubts question. Never a rough "
            "price for Pramukh Vedanta."),
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
                    # "later" line (it ends on weekend-or-weekday).
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
            "never invite them again on this call. If they reject the day or "
            "time you proposed ('कल मत करो', 'कोई और date'), that is a "
            "negotiation, not a refusal: offer two other days and ask which "
            "suits — never a callback, never a goodbye."),
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
    for n in ("calling_from", "family_size"):
        by[n]["required"] = False
        by[n]["max_asks"] = 0
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
        "en": "Sure Sir. Are you specifically considering a 3 BHK or a 4 BHK?"}
    by["configuration_required"]["max_asks"] = 2
    by["budget_range"]["question"] = {
        "hi": Q_BUDGET,
        "en": "What approximate budget range would be comfortable for you?"}
    by["budget_range"]["max_asks"] = 2
    # Once stored, overwritten only on an explicit correction ("change
    # करके", "नहीं, 4 BHK"). Call b5452b23: the caller QUOTING the agent's
    # price ("3 bhk का ₹70 लाख टू ₹85 लाख, यह किसका है?") rewrote both the
    # size and the budget, and the agent then cited "आपका 85 लाख का budget".
    by["configuration_required"]["merge_policy"] = "correction_gated"
    by["budget_range"]["merge_policy"] = "correction_gated"
    by["purchase_timeline"]["question"] = {
        "hi": Q_TIMELINE,
        "en": "Is your purchase plan immediate, or are you considering it in "
              "the next 3 to 6 months?"}
    by["purchase_timeline"]["max_asks"] = 2
    by["purchase_timeline"]["ask_gated"] = True
    syn = by["purchase_timeline"]["enum_synonyms"]
    # Never a bare "अभी"/"abhi": "अभी बात कर सकते हैं", "आपने अभी recommend
    # किया" wrote Immediate on call 64629614 (RUNBOOK §79).
    syn["Immediate"] = [x for x in (syn.get("Immediate") or [])
                        if x.strip().lower() not in ("abhi", "अभी")] + [
        "immediate", "immediately", "इमीडिएट", "इमिडिएट", "turant", "तुरंत",
        "abhi hi", "अभी ही", "अभी लेना", "jald se jald", "जल्द से जल्द"]
    syn["Within 6 months"] = list(syn.get("Within 6 months") or []) + [
        "3 se 6", "3-6", "3 से 6", "3 to 6", "three to six", "teen se chhe",
        "तीन से छह", "next 3", "next few months", "agle kuch mahine",
        "अगले कुछ महीने", "kuch mahine", "कुछ महीनों", "कुछ महीने"]
    by["finalized_property"]["question"] = {
        "hi": "क्या आप एक बार site पर आकर ख़ुद देखना चाहेंगे?",
        "en": "Would you like to come and see the site yourself once?"}
    by["finalized_property"]["required"] = False   # written by the switch,
    by["finalized_property"]["max_asks"] = 0       # never asked
    order = ["caller_name", "purpose", "configuration_required",
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
                                      "इन्वेस्टमेंट वैल्यू", "investment",
                                      "इन्वेस्टमेंट", "इन्वेस्टमेंट वाली",
                                      "निवेश", "invest"]}),
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
                "'Tell me about Vedanta' / 'how big' / 'how many floors' → "
                "the two or three facts that fit what they told you, in round "
                "spoken numbers, then one question back. Never a rough price — "
                "exact unit price and cost sheet only.")

    def add(pid, g):
        out.insert(0, {"id": pid, "patterns": [], "answer": {},
                       "prompt_guidance": g})
    # The owner's exact answers (2026-10-12). Script fidelity: a 'verbatim:'
    # line is spoken word for word; only the <bracketed> omission is allowed.
    add("decide_later", (
        "'Main baad mein dekhunga' / 'बाद में देखूँगा' / 'I'll think about "
        f"it' → verbatim: \"{A_LATER}\" (if a ━━ VISIT DECLINED ━━ note is "
        "present, stop before the weekend-or-weekday question and follow "
        "the note instead)."))
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
        "Never a question of your own in its place. NEVER say a number for "
        "Pramukh Vedanta."))
    add("investment_returns", (
        "'Will the price double?' / 'what returns?' → you won't promise "
        "returns or appreciation. A sound investment decision rests on "
        "location, current pricing, demand, rental potential and resale "
        "prospects — offer to help them evaluate those, and ask their "
        "investment horizon if you don't know it."))
    return out


def build(direction: str, ravi: dict, all_items: list[dict]) -> dict:
    proj = next(p for p in yaml.safe_load(bpp.SHEET.read_text(encoding="utf-8"))
                ["projects"] if p["key"] == "vedanta")
    ved = _vedanta(proj["item"])
    items = [ved if i["id"] == "pramukh-vedanta" else i for i in all_items]
    d = bpp.build(direction, ravi, {**proj, "item": ved}, items)
    d["agent_id"] = "pramukh_vedanta_test-v1"
    d["identity"]["role_description"] = (
        "Pramukh Vedanta sales consultant (scripted-pitch test) — "
        + ("follow-up calls" if direction == "outbound" else "inbound"))

    cf = d["call_flow"]
    cf["stages"] = _stages(cf["stages"], direction)
    cf["call_goal"] = (
        "Help this caller evaluate whether Pramukh Vedanta is the right "
        "property for their requirement — the owner's script, line by line: "
        "understand the requirement first (purpose, 3 or 4 BHK, budget, "
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
    # request, on this test twin only — every pause held before answering,
    # a back-channel "हाँ" never a turn, real speech stops our audio.
    cf["turn_taking"] = {
        "pause_hold_ms": 1500,
        "max_merged_fragments": 6,
        "backchannel_guard": True,
        "sustained_barge_ms": 700,
        "tail_barge_in": True,
    }
    # The owner's lines run to 647 characters (PITCH_SELF); the platform's
    # 400-character cap sliced the pitch at "…एक site visit..." on that call.
    d["llm_config"]["max_response_length"] = 760
    d["llm_config"]["max_tokens"] = 320
    d["slots"] = _slots(d["slots"])

    kb = d["knowledge_base"]
    kb["pricing_policy"] = {"mode": "on_request", "defer_line": {}}
    # Spellings STT gave "Aristo" on call b5452b23 (the caller was told
    # "रिश्ते की बात नहीं है" for one of them).
    kb.setdefault("stt_corrections", {}).setdefault(
        "project_name_corrections", {}).update({
            "परिष्टों": "Aristo", "परिष्टो": "Aristo", "परिस्टो": "Aristo",
            "अरिष्टो": "Aristo", "अरीस्टो": "Aristo",
            "प्रमुख रिस्टो": "Pramukh Aristo"})
    sig = kb["qa_field_signals"]
    sig["purpose"] = ["ख़ुद रहने के लिए", "khud rehne", "investment के लिए",
                      "live in yourself", "as an investment"]
    sig["configuration_required"] = ["3 bhk consider", "या 4 bhk", "3 BHK or",
                                     "specifically 3", "कितने bhk", "bhk"]
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
        "You are Ravi, Pramukh Vedanta's consultant — professional, premium "
        "and never pushy. This call follows the owner's script: the lines "
        "quoted in the CURRENT GOAL and in OBJECTION HANDLING are said word "
        "for word, in Hindi with the English words left in English, and "
        "everything in between — the reaction to what the person just said, "
        "an answer to a question of theirs — is short, plain and human. You "
        "are not trying to sell a flat over a phone call; you are helping "
        "this person evaluate whether Pramukh Vedanta is the right property "
        "for their requirement. So you understand the requirement first — "
        "purpose, 3 or 4 BHK, budget, timeline, what matters most — one "
        "question at a time, and only then the pitch, built from the "
        "KNOWLEDGE BASE and tied to what they told you. What they tell you "
        "stays told: it is in the CONVERSATION STATE, you never ask for it "
        "again even after other questions, and you use it when you suggest "
        "or compare properties. You only record what you clearly heard and "
        "that makes sense as an answer — a garbled or unrelated fragment is "
        "not an answer; you say you didn't catch it and ask once more. You "
        "never quote a rough price for Vedanta: exact unit price and complete "
        "cost sheet. You know every Pramukh project in Vapi in the KNOWLEDGE "
        "BASE and answer questions about them plainly; you suggest another "
        "one only when a ━━ BUDGET FIT ━━ note says their budget fits it "
        "better, and pitch it only after a clear yes. After every answer you "
        "ask whether they have any other question, until they say no; then "
        "the visit; then one read-back; then whether there is anything else; "
        "then the closing line. You use their name at most twice in the "
        "call, never greet twice, never read a date back twice. You are a "
        "person on the phone, not a form.")

    gr = d["guardrails"]
    drop = ("invent a price", "say 'when can you come?'",
            "pitch Vedanta before")
    gr["never_rules"] = [r for r in gr["never_rules"]
                         if not r.startswith(drop)] + [
        "give an approximate, rough or 'starting from' price for Pramukh "
        "Vedanta — offer the exact unit price and complete cost sheet instead",
        "invent a size, floor count, possession date, availability, amenity "
        "or RERA number beyond what the KNOWLEDGE BASE states — the pitch "
        "template is filled from the KNOWLEDGE BASE, never from memory",
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
    ]

    ob = d["outbound"]
    if direction == "outbound":
        # Unchanged (owner: "keep this part the same").
        ob["good_time_check_line"] = {
            "hi": "शुक्रिया! मैं रवि बोल रहा हूँ Pramukh Vedanta, New Chala, "
                  "Vapi से — आपने कुछ समय पहले हमारे project में interest "
                  "दिखाया था। अभी दो मिनट बात करना ठीक रहेगा?",
            "en": "Thanks! It's Ravi from Pramukh Vedanta, New Chala, Vapi — "
                  "you'd shown interest in our project a while back. Is now "
                  "an okay time for two minutes?"}
    else:
        cf["connect"]["intro_line"] = {
            "hi": "नमस्ते, मैं रवि बोल रहा हूँ Pramukh Vedanta, New Chala, "
                  "Vapi से — बताइए, मैं आपकी क्या मदद कर सकता हूँ?",
            "en": "Hello, this is Ravi from Pramukh Vedanta, New Chala, Vapi "
                  "— how can I help you?"}
    ob["direction_brief"] = (
        ("You're following up with someone who enquired about Pramukh "
         "Vedanta. " if direction == "outbound" else
         "Someone has called about Pramukh Vedanta. ")
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
        "Pramukh Vedanta site location pin",
        "floor plan of the 3, 4 or Ultra 4 BHK option discussed",
        "exact unit price, area, floor and complete cost sheet",
        "Ravi's direct number"]
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not (args.dry_run or args.apply):
        ap.error("--dry-run or --apply")
    sheet = yaml.safe_load(bpp.SHEET.read_text(encoding="utf-8"))
    all_items = [p["item"] for p in sheet["projects"]]
    agents_dir = (Path(__file__).resolve().parent / "agents_dry_test"
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
                                    owner=bpp.OWNER if args.apply else "",
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
            bpp._register(slug, DISPLAY)
            try:
                from backend.app.agent_definition import registry
                registry.invalidate(slug)
            except Exception:
                pass
        print(f"{slug}: revision {rev}")


if __name__ == "__main__":
    main()
