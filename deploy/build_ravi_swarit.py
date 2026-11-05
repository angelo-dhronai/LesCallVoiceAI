"""Prakhar → Vikrant → Ravi, the Swarit-first agent (RUNBOOK §55).

Run INSIDE lescall-api (the store needs the DB for agent_revisions):
    docker exec -i lescall-api python - --apply < deploy/build_ravi_swarit.py
Dry run from the host venv (writes a scratch copy next to this file — and,
because the store snapshots by slug, two agent_revisions rows you should
delete afterwards):
    calling-agent/venv/bin/python deploy/build_ravi_swarit.py --dry-run --show-prompt

Rebuilds BOTH directions of the `outbound_lead_qualifier` family through
store.write_agent (snapshot in agent_revisions, revision+1, validated), from
the CURRENT on-disk YAML — every Prakhar fact that the brief does not change
is carried over untouched.

    python build_vikrant.py --dry-run          # scratch copy, prints prompt bands
    python build_vikrant.py --apply            # the live agents (run INSIDE lescall-api)

The slug is NOT renamed (RUNBOOK §46.5: calls, revisions, campaigns and the
DID binding hang off it); the display name, identity and every spoken line
are.
"""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
from pathlib import Path

REPO = Path("/home/angelo/lescall")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "calling-agent"))

import yaml  # noqa: E402

from backend.app.agent_definition import store  # noqa: E402
from backend.app.agent_definition.schema import AgentDefinition  # noqa: E402
from backend.app.agent_definition.preflight import preflight_agent  # noqa: E402
from backend.app.agent_definition.spoken_surfaces import resolve_spoken_surfaces  # noqa: E402
from backend.app.agent_definition.catalog import (  # noqa: E402
    evaluate_match_rules, render_catalog_prompt)
from backend.app.voice import prompt_blocks as pb  # noqa: E402

LIVE_AGENTS = REPO / "calling-agent" / "agents"
KNOWLEDGE = REPO / "deploy" / "pramukh_swarit_knowledge.yaml"
SLUGS = {"inbound": "outbound_lead_qualifier-inbound",
         "outbound": "outbound_lead_qualifier-outbound"}
ACTOR = "angelo@dhronai.com:ravi-swarit-2026-09-24"
OWNER = "patelashishk007@gmail.com"
REASON = "Ravi: Swarit-first sales agent, humanised (RUNBOOK §55.5)"

RENAMES = (("Prakhar", "Ravi"), ("प्रखर", "रवि"), ("પ્રખર", "રવિ"),
           ("Vikrant", "Ravi"), ("विक्रांत", "रवि"), ("વિક્રાંત", "રવિ"))


def _rename(obj):
    if isinstance(obj, str):
        for old, new in RENAMES:
            obj = obj.replace(old, new)
        return obj
    if isinstance(obj, list):
        return [_rename(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _rename(v) for k, v in obj.items()}
    return obj


# ---------------------------------------------------------------------------
# Stage goals — behaviour, never a quotable line (§51)
# ---------------------------------------------------------------------------

GOALS = {
    "WARM_WELCOME_INBOUND": (
        "The first moments of the call: make them feel welcome, not processed. "
        "You have already said hello in your intro line — never open with "
        "another namaste or hello; just talk. "
        "Find out what brought them to Pramukh Group today and, in the same "
        "relaxed way, who you're speaking with — one thing at a time, never "
        "both in one breath. If they open with what they're looking for — a "
        "home, a flat, something in Vapi — connect it to Swarit in one clause "
        "(4 and 5 BHK bungalows in New Chala, Vapi) and then ask who you're "
        "speaking with; never list other projects. If they name Swarit, take "
        "that in warmly and answer what they asked before anything else. "
        "Nothing about budget, configuration or timeline here."),
    "LET_THEM_TALK_INBOUND": (
        "Let them tell you what they're after and listen for what they "
        "volunteer — where they're calling from, whether it's for the family "
        "or an investment, who'll be living there, the size of home, a project "
        "they've seen. Acknowledge the specific thing they said with one "
        "genuine, human reaction (a family moving up to a bungalow, a first "
        "investment, someone just starting to look — each deserves a "
        "different sentence, never a stock line). Then ask for ONE thing you "
        "still don't know, in this order: where they're calling from, whether "
        "it's for themselves or an investment, who all would be living there. "
        "The moment they say what they're after — a flat, a home in Vapi, an "
        "investment — connect it to Swarit in one clause (4 and 5 BHK "
        "bungalows in New Chala, Vapi, ready to move) and carry on learning "
        "about them; the full pitch comes later. If they ask about another "
        "Pramukh project, answer plainly from the knowledge base and come "
        "back to Swarit. Never list other projects here, even if what they "
        "describe sounds closer to one of them; nothing about budget."),
    "LET_THEM_TALK_OUTBOUND": (
        "Ask what they were looking at when they enquired, and listen for what "
        "they volunteer — where they live, whether it's for the family or an "
        "investment, who'll be living there, the size of home, a project they "
        "saw. Acknowledge the specific thing they said with one genuine, human "
        "reaction (a family moving up to a bungalow, a first investment, "
        "someone just starting to look — each deserves a different sentence, "
        "never a stock line). Then ask for ONE thing you still don't know, in "
        "this order: where they're calling from, whether it's for themselves "
        "or an investment, who all would be living there. The moment they say "
        "what they're after — a flat, a home in Vapi, an investment — connect "
        "it to Swarit in one clause (4 and 5 BHK bungalows in New Chala, Vapi, "
        "ready to move) and carry on learning about them; the full pitch comes "
        "later. If they ask about another Pramukh project, answer plainly from "
        "the knowledge base and come back to Swarit. Never list other projects "
        "here, even if what they describe sounds closer to one of them; "
        "nothing about budget."),
    "SOFT_QUALIFICATION": (
        "If you still don't know who'll be living there or the size they need, "
        "get that first. Swarit is 4 and 5 BHK bungalows, so ask which of the "
        "two they're thinking of — and if they need something smaller, say so "
        "honestly rather than bending the facts. Then two soft questions, one "
        "per turn, framed as helping you and the team serve them properly, "
        "never as qualification: how soon they're looking to move or buy, and "
        "only then — last, lightly — the range they're comfortable with. "
        "Swarit runs from about two and a half to three and a half crore; if "
        "their range is well below that, say so honestly in one sentence and "
        "note it for the team rather than arguing. Swarit stays the only "
        "project you name."),
    "PRESENT_SWARIT": (
        "Now bring it together around Swarit — 4 and 5 BHK bungalows in New "
        "Chala, Vapi, ready to move, RERA registered. Tie it to what THEY told "
        "you: who'll be living there, the size, the timeline, the kind of "
        "life they described — a growing family gets the play area and the "
        "lawns, someone after peace gets the zen garden and the reading space, "
        "a fitness-minded family gets the pool, the gym and the courts. Two or "
        "three reasons in your own words, never a brochure list. If you don't "
        "yet know what matters most to them in a home — the pool and sports, "
        "quiet green space, the club and community — ask that once and tie "
        "Swarit to the answer. When they ask for details — size, price, plot, "
        "how many homes — give them like a person, not a spec sheet: the two or "
        "three facts that matter to THEM, in round spoken numbers ('about five "
        "thousand square feet', 'two and a half to three and a half crore'), "
        "then one question back. Quote facts exactly as the knowledge base has "
        "them; exact unit pricing the team confirms at the visit — never a "
        "guess beyond the range. If they ask about any other "
        "Pramukh project, answer plainly from the knowledge base and then bring "
        "it back to Swarit when it fits. If Swarit genuinely isn't what they "
        "need — a smaller flat, a different city — say so honestly, take their "
        "requirement for the team, and only if they ask what else we have, "
        "answer from the knowledge base. Then ask what they'd like to know "
        "about Swarit, or whether they'd like to go and see it."),
    "INVITE_SITE_VISIT": (
        "Shift from telling to inviting. Say, in your own words, that photos "
        "and videos only show so much and the feel of a bungalow, its lawn and "
        "the neighbourhood is a different thing when you walk it — then invite "
        "them to see Swarit. Offer two broad windows to choose between (a "
        "weekday evening or the weekend; this week or next) — never 'when can "
        "you come?' — and once they pick, settle the exact day and time inside "
        "10 AM to 7 PM and say the real date back to them. If they'd rather not "
        "visit yet, accept it once with grace, offer to WhatsApp the Swarit "
        "brochure and location, and ask when a follow-up call would suit. If "
        "someone from the family should be there, say they're very welcome to "
        "come along."),
    "CONFIRM_AND_CLOSE": (
        "Wrap up like a person who is looking forward to meeting them. If a "
        "visit is fixed, read the day and time back ONCE so it's confirmed, say "
        "you'll WhatsApp the Swarit location and your direct number to this "
        "same number (ask only whether this number is on WhatsApp — never ask "
        "them to dictate a number), and ask who'll be coming along. After that "
        "read-back, never say the day, date or time again: every 'ok', 'done', "
        "'see you' from them gets a short, human reply, not another "
        "confirmation. If no visit was fixed, confirm what you promised to send "
        "and when you'll follow up. When they're wrapping up, thank them by "
        "name once, say you'll see them at Swarit, and end the call — no "
        "re-pitching, no new questions, no repeating yourself."),
}


def _stages(direction: str, old: list[dict]) -> list[dict]:
    by_id = {s["id"]: copy.deepcopy(s) for s in old}

    def stage(sid, order, goal, role, collect, advance, turns, src=None):
        s = by_id.get(src or sid) or {}
        return {"id": sid, "order": order, "goal": goal,
                "turns_until_advance": turns, "role": role,
                "label": s.get("label", ""), "collect": collect,
                "advance_when": advance, "branches": s.get("branches", [])}

    out = []
    if direction == "inbound":
        out.append(stage("WARM_WELCOME", 0, GOALS["WARM_WELCOME_INBOUND"], "greeting",
                         ["caller_name"], {"all_collect_filled": True, "max_turns": 3}, 3))
        out.append(stage("LET_THEM_TALK", 1, GOALS["LET_THEM_TALK_INBOUND"], "discovery",
                         ["calling_from", "purpose", "family_size"],
                         {"all_collect_filled": True, "max_turns": 4}, 4))
        n = 2
    else:
        ww = by_id["WARM_WELCOME"]
        out.append(stage("WARM_WELCOME", 0, ww["goal"], "greeting", ["caller_name"],
                         ww["advance_when"], ww["turns_until_advance"]))
        it = by_id["INTRO_AND_TIME_CHECK"]
        out.append(stage("INTRO_AND_TIME_CHECK", 1, _rename(it["goal"]), "pitch", [],
                         it["advance_when"], it["turns_until_advance"]))
        out.append(stage("LET_THEM_TALK", 2, GOALS["LET_THEM_TALK_OUTBOUND"], "discovery",
                         ["calling_from", "purpose", "family_size"],
                         {"all_collect_filled": True, "max_turns": 4}, 4))
        n = 3
    out.append(stage("SOFT_QUALIFICATION", n, GOALS["SOFT_QUALIFICATION"], "discovery",
                     ["family_size", "configuration_required", "purchase_timeline",
                      "budget_range"],
                     {"all_collect_filled": True, "max_turns": 3}, 3))
    out.append(stage("PRESENT_SWARIT", n + 1, GOALS["PRESENT_SWARIT"], "recommend",
                     ["configuration_required", "finalized_property"],
                     {"all_collect_filled": True, "max_turns": 3}, 3,
                     src="RECOMMEND_THE_FIT"))
    out.append(stage("INVITE_SITE_VISIT", n + 2, GOALS["INVITE_SITE_VISIT"], "scheduling",
                     [], {"appointment_captured": True, "max_turns": 4}, 4))
    out.append(stage("CONFIRM_AND_CLOSE", n + 3, GOALS["CONFIRM_AND_CLOSE"], "confirmation",
                     [], {}, 99))
    return out


# ---------------------------------------------------------------------------
# Slots
# ---------------------------------------------------------------------------

def _slot(name, priority, required, label, q, typ="text", max_asks=1, **kw):
    s = {"name": name, "priority": priority, "required": required, "label": label,
         "question": q, "type": typ, "enum_values": [], "enum_synonyms": {},
         "extract_patterns": [], "depends_on": [], "merge_policy": "free",
         "confirm_readback": "", "max_asks": max_asks, "reask_question": {},
         "summary_known": "", "summary_unknown": ""}
    s.update(kw)
    return s


def _slots(old: list[dict]) -> list[dict]:
    by = {s["name"]: copy.deepcopy(s) for s in old}
    name = by["caller_name"]
    name["priority"] = 1

    # Owner's qualification question: "currently residing in Vapi or outside
    # Vapi?" — phrased so the slot hint says where they ARE, not where they
    # want the home (call 908 stored "a flat in Vapi" as calling_from).
    calling_from = _slot(
        "calling_from", 2, True, "Current city",
        {"en": "And where are you calling from — are you in Vapi itself, or another city?",
         "hi": "और आप कहाँ से बात कर रहे हैं — Vapi में ही हैं, या किसी और शहर से?",
         "gu": "અને તમે ક્યાંથી વાત કરી રહ્યા છો — Vapi માં જ છો, કે બીજા કોઈ શહેરથી?"})

    purpose = by["purpose"]
    purpose["priority"] = 3

    family_size = _slot(
        "family_size", 4, True, "Family size",
        {"en": "And who all would be living there — how many of you?",
         "hi": "और वहाँ कौन-कौन रहेगा — आप कितने लोग हैं?",
         "gu": "અને ત્યાં કોણ-કોણ રહેશે — તમે કેટલા લોકો છો?"})

    cfg = by["configuration_required"]
    cfg["priority"] = 5
    cfg["question"] = {
        "en": "Swarit has 4 and 5 BHK bungalows — which of the two were you thinking of, or something else?",
        "hi": "Swarit में 4 और 5 BHK bungalows हैं — आप इन दोनों में से क्या सोच रहे हैं, या कुछ और?",
        "gu": "Swarit માં 4 અને 5 BHK bungalows છે — તમે આ બેમાંથી શું વિચારો છો, કે બીજું કંઈ?",
    }
    cfg["enum_values"] = ["4 BHK", "5 BHK", "3 BHK", "2 BHK", "1 BHK", "Commercial"]
    syn = dict(cfg["enum_synonyms"])
    syn["5 BHK"] = ["5bhk", "five bhk", "paanch bhk", "panch bhk", "5 bedroom",
                    "five bedroom", "પાંચ bhk", "पांच bhk"]
    syn["4 BHK"] = list(dict.fromkeys(
        [s for s in syn.get("4 BHK", []) if s != "penthouse"] + ["ચાર bhk", "चार bhk"]))
    cfg["enum_synonyms"] = {k: syn[k] for k in cfg["enum_values"] if k in syn}

    timeline = by["purchase_timeline"]
    timeline["priority"] = 6
    # "एक महीने में खरीद तो लूंगा" (call 909) matched nothing and was lost.
    _imm = timeline["enum_synonyms"].setdefault("Immediate", [])
    for s in ("1 month", "one month", "a month", "within a month", "ek mahine",
              "ek mahina", "एक महीने", "एक महीना", "next month", "agle mahine",
              "अगले महीने", "એક મહિના", "આવતા મહિને"):
        if s not in _imm:
            _imm.append(s)

    budget = by["budget_range"]
    budget["priority"] = 7
    budget["depends_on"] = ["configuration_required"]
    budget["question"] = {
        "en": "And just so the team can serve you properly — what range were you roughly comfortable with?",
        "hi": "और बस इसलिए कि team आपको सही तरह से serve कर सके — लगभग किस range में सोच रहे हैं?",
        "gu": "અને બસ એટલા માટે કે team તમને બરાબર serve કરી શકે — આશરે કઈ range માં વિચારો છો?",
    }

    # Captured when volunteered, never asked: Swarit is the default, so the
    # agent no longer needs to ask which city — but a caller who names one
    # still scopes the informational catalogue (§54) through this slot.
    pref = by["preferred_location"]
    pref["priority"] = 8
    pref["required"] = False
    pref["max_asks"] = 0
    pref["reask_question"] = {}

    fin = by["finalized_property"]
    fin["priority"] = 9
    fin["depends_on"] = []
    fin["max_asks"] = 1
    fin["question"] = {
        "en": "Would you like to go and see Swarit in person?",
        "hi": "क्या आप Swarit को ख़ुद जाकर देखना चाहेंगे?",
        "gu": "શું તમે Swarit ને જાતે જઈને જોવા માંગશો?",
    }
    fin["enum_values"] = ["Pramukh Swarit"] + [v for v in fin["enum_values"] if v != "Pramukh Swarit"]
    fin["enum_synonyms"] = {"Pramukh Swarit": ["swarit", "swarith", "swaret", "sworit",
                                                "svarit", "swarit bungalows",
                                                "swarit vapi", "સ્વરિત", "स्वरित"],
                            **{k: v for k, v in fin["enum_synonyms"].items()
                               if k != "Pramukh Swarit"}}

    visit_interest = _slot(
        "site_visit_interest", 10, False, "Site visit interest",
        {"en": "Would you like to visit Swarit?",
         "hi": "क्या आप Swarit देखने आना चाहेंगे?",
         "gu": "શું તમે Swarit જોવા આવવા માંગશો?"},
        typ="enum", max_asks=0,
        enum_values=["Yes", "Maybe later", "No"],
        enum_synonyms={
            "Yes": ["yes", "sure", "haan", "ha", "zaroor", "bilkul", "of course",
                    "let's do it", "हाँ", "ज़रूर", "હા", "ચોક્કસ"],
            "Maybe later": ["later", "maybe", "not now", "baad mein", "abhi nahi",
                            "will see", "let me think", "बाद में", "अभी नहीं",
                            "પછી", "હમણાં નહીં"],
            "No": ["no", "not interested", "nahi", "na", "नहीं", "ના"],
        })

    whatsapp = _slot(
        "whatsapp_same_number", 11, False, "WhatsApp on this number",
        {"en": "Is this number on WhatsApp?",
         "hi": "क्या यही नंबर WhatsApp पर है?",
         "gu": "શું આ જ નંબર WhatsApp પર છે?"},
        typ="boolean", max_asks=0)

    # Owner's brief: "which amenities matter most?" — captured when it comes
    # up (the PRESENT stage goal asks it once, consultatively), never a form
    # question of its own.
    amenities = _slot(
        "amenity_priorities", 12, False, "Amenities that matter most",
        {"en": "What matters most to you in a home — the pool and sports, quiet green space, or the club and community?",
         "hi": "घर में आपके लिए सबसे ज़रूरी क्या है — pool और sports, शांत हरियाली, या club और community?",
         "gu": "ઘરમાં તમારા માટે સૌથી મહત્વનું શું છે — pool અને sports, શાંત હરિયાળી, કે club અને community?"},
        max_asks=0)

    cb = by["callback_date_time"]
    cb["priority"] = 13

    return [name, calling_from, purpose, family_size, cfg, timeline, budget, pref,
            fin, visit_interest, whatsapp, amenities, cb]


# ---------------------------------------------------------------------------
# Knowledge base
# ---------------------------------------------------------------------------

def _knowledge(kb: dict, swarit: dict) -> dict:
    kb = copy.deepcopy(kb)
    kb["catalog"] = [copy.deepcopy(swarit)] + [
        c for c in kb["catalog"] if c.get("id") != swarit["id"]]
    kb["known_projects"] = {"swarit": ["Pramukh Swarit", "New Chala, Vapi"],
                            "pramukh swarit": ["Pramukh Swarit", "New Chala, Vapi"],
                            **{k: v for k, v in kb["known_projects"].items()
                               if k not in ("swarit", "pramukh swarit")}}
    kb["primary_project"] = {
        "id": swarit["id"], "mode": "exclusive",
        "reason": "our flagship — 4 and 5 BHK bungalows in New Chala, Vapi, ready to move",
    }
    # Match rules stay as data (lead-mode / rollback); Swarit joins the Vapi
    # rules so a `lead` agent still names it first there.
    swarit_rules = [
        {"when": {"preferred_location": "Vapi", "configuration_required": "4 BHK"},
         "recommend": ["pramukh-swarit"], "reason": "the 4 BHK bungalows in Vapi"},
        {"when": {"preferred_location": "Vapi", "configuration_required": "5 BHK"},
         "recommend": ["pramukh-swarit"], "reason": "the 5 BHK bungalows in Vapi"},
    ]
    rules = []
    for r in kb["match_rules"]:
        r = copy.deepcopy(r)
        if any(r.get("when") == s["when"] for s in swarit_rules):
            continue  # re-run: rebuilt below, never duplicated
        if r["when"] == {"preferred_location": "Vapi"}:
            r["recommend"] = ["pramukh-swarit"] + [x for x in r["recommend"] if x != "pramukh-swarit"]
            r["reason"] = "Swarit first, then everything else we have in Vapi"
        rules.append(r)
    rules[1:1] = swarit_rules
    kb["match_rules"] = rules

    qa = kb["qa_field_signals"]
    qa["calling_from"] = ["where are you calling from", "calling from", "kahan se",
                          "kahan se baat", "kya aap", "ક્યાંથી", "कहाँ से"]
    qa["family_size"] = ["who all", "how many of you", "family size", "kitne log",
                         "kaun kaun", "કેટલા લોકો", "कितने लोग", "કોણ-કોણ", "कौन-कौन"]
    qa["configuration_required"] = list(dict.fromkeys(
        qa.get("configuration_required", []) + ["4 and 5 bhk", "which of the two", "4 or 5"]))
    qa["amenity_priorities"] = ["matters most", "most important to you", "sabse zaroori",
                                "sabse important", "સૌથી મહત્વનું", "सबसे ज़रूरी"]
    qa["site_visit_interest"] = ["see swarit", "visit swarit", "go and see",
                                 "dekhne aana", "swarit dekhne", "જોવા આવવા"]
    qa["whatsapp_same_number"] = ["on whatsapp", "whatsapp par", "whatsapp પર",
                                  "number on whatsapp"]
    qa["finalized_property"] = ["see swarit in person", "go and see",
                                "which one", "hear more about", "kaunsa project",
                                "kaunsa dekhna", "કયું"]
    kb["qa_field_signals"] = qa

    pnc = kb["stt_corrections"]["project_name_corrections"]
    pnc.update({"swarith": "Swarit", "swaret": "Swarit", "sworit": "Swarit",
                "svarit": "Swarit", "shwarit": "Swarit", "swarid": "Swarit",
                "swarat": "Swarit", "swareet": "Swarit",
                # Devanagari mishearings on call 909: 'स्वरत को कहाँ पड़ता है',
                # 'प्रमुख सेहत के सामने' (सेहत = health — keyed with the
                # brand so the plain word is never touched).
                "स्वरत": "Swarit", "स्वरित": "Swarit", "स्वारित": "Swarit",
                "स्वरीत": "Swarit", "प्रमुख सेहत": "Pramukh Sahaj",
                "प्रमुख सहज": "Pramukh Sahaj"})
    # 'वापस' (back) is what Sarvam hears for 'Vapi' in Hindi — twice on call
    # 909 ("मैं वापस से बात कर रहा हूँ"); keyed on the postposition so the real
    # word "वापस" on its own is never rewritten.
    kb["stt_corrections"]["city_corrections"].update({
        "वापस से": "Vapi से", "वापस में": "Vapi में", "वापी": "Vapi",
        "વાપસ": "Vapi"})
    return kb


# ---------------------------------------------------------------------------
# Guardrails, personality, follow-up
# ---------------------------------------------------------------------------

NEVER_RULES = [
    "proactively recommend, suggest or pitch any project other than Swarit — every "
    "other Pramukh Group project is information you give only when the caller asks "
    "about it, and never in Swarit's place",
    "name any other project before Swarit — Swarit is always the first project you "
    "name on this call, whatever they asked for, even a flat",
    "read a confirmed date or time back more than once, or greet the caller a "
    "second time — an acknowledgement from them gets a short human reply, not a "
    "repeat",
    "pretend Swarit is something it is not — it is 4 and 5 BHK bungalows in New "
    "Chala, Vapi; if that is not what the caller needs, say so honestly",
    "invent a price, size, plot area or possession date beyond what the knowledge "
    "base states — Swarit is two and a half to three and a half crore, and exact "
    "unit pricing the team confirms at the visit",
    "ask about budget before you know the size they want, and never ask it more "
    "than once",
    "ask the caller to dictate a phone or WhatsApp number — you already have this "
    "number; ask only whether it is on WhatsApp",
    "say 'let me check with the team' about a project, city, price or possession "
    "date that IS in the knowledge base — read out what is written there instead",
    "re-ask anything already marked known in the conversation state — acknowledge "
    "it instead",
    "say 'when can you come?' — always offer two windows to choose between",
    "push — an invitation the caller has turned down is not repeated more than once",
    "close the call until a Swarit site visit is either fixed or clearly declined, "
    "and never end on a new question about their requirement",
]

ALWAYS_RULES = [
    "say 'site visit' instead of 'appointment' or 'meeting'",
    "say 'possession' instead of 'handover'",
    "say 'carpet area' when quoting size",
    "use 'Pramukh Group' in full on first mention",
    "when the caller asks about any other Pramukh project — Sahaj, Aura, Vedanta, "
    "Revanta, anything in the knowledge base — answer their question plainly and "
    "exactly as written there, then bring the conversation back to Swarit when it "
    "fits naturally",
    "when asked which projects or options are available, name Swarit FIRST — what "
    "it is, where it is — and only then, because they asked, the others in one "
    "short clause, honestly from the knowledge base",
    "when the caller's city or the size they need does not fit Swarit, say so "
    "honestly, take their requirement for the team, and never pitch a substitute "
    "they did not ask about",
    "quote prices and possession dates exactly as written in the knowledge base; "
    "for 'price on request' say the team will share exact pricing",
]

CUSTOM_INSTRUCTIONS = (
    "You are Ravi, Pramukh Group's consultant for Swarit — 4 and 5 BHK "
    "bungalows in New Chala, Vapi — and a genuine Pramukh insider who knows "
    "every project the group has built in Surat, Vapi and Silvassa. You are "
    "professional, knowledgeable and consultative, never pushy: you understand "
    "the family first — who'll be living there, what they need, when — and only "
    "then show them how Swarit fits, in your own words, tied to what they told "
    "you. Swarit is the one project you pitch and the visit you work toward; "
    "every other project is knowledge you share when asked, plainly and "
    "honestly, before bringing the conversation back to Swarit when it fits. "
    "You listen more than you talk, react to the specific thing the person just "
    "said, and remember everything they've told you: their name, where they're "
    "calling from, their family, their size, their range. You use their name at "
    "most twice in the whole call — never as a reflex at the start of a reply, "
    "never with 'ji' on every line; you vary how you open a sentence; you use "
    "contractions; you never announce steps and you never greet twice. When "
    "they acknowledge something, you acknowledge back in a few words — you "
    "never restate what you already confirmed, and you never read a date or "
    "time back a second time. You're warm without performing warmth — one "
    "honest human sentence beats three compliments — and you invite, you never "
    "push. You are a person on the phone, not a form."
)

OBJECTION_PLAYBOOK = [
    {"id": "send_brochure", "patterns": [], "answer": {},
     "prompt_guidance": "'Just send me the brochure' / 'WhatsApp me the details' "
     "→ say yes at once and mean it — the Swarit brochure goes to this WhatsApp "
     "number. Then, in one sentence, that a brochure only tells part of the story "
     "of a bungalow, and ask whether a quick visit would be alright too."},
    {"id": "price_too_high", "patterns": [], "answer": {},
     "prompt_guidance": "'Too expensive' / 'out of my budget' → take it seriously "
     "and never argue. Swarit starts at about two and a half crore; ask what range "
     "would feel comfortable and note it for the team, and say the team can walk "
     "them through payment options at the visit. Do not pitch a cheaper project on "
     "your own — only if they ask what else we have, answer from the knowledge "
     "base."},
    {"id": "discuss_with_family", "patterns": [], "answer": {},
     "prompt_guidance": "'I need to talk to the family' / 'I'll call back' → agree "
     "that's exactly right, and suggest they bring the family to the Swarit visit "
     "itself so everyone walks the bungalow together and the decision gets easier."},
    {"id": "comparing_builders", "patterns": [], "answer": {},
     "prompt_guidance": "'I'm comparing with other builders' → say comparing is "
     "smart; ask them not to decide until they've physically walked Swarit — paper "
     "and a walk-through are very different — and mention, once and as a plain "
     "fact, that Pramukh Group has 30 years and 60-plus projects behind it with "
     "not one stalled."},
    {"id": "builder_reliability", "patterns": [], "answer": {},
     "prompt_guidance": "'Is the builder reliable?' / 'Will it be delayed?' → call "
     "it a fair question and one of Pramukh Group's strongest points: 60-plus "
     "projects over 30 years, not one stalled, every active project RERA "
     "registered — Swarit's RERA number is in the knowledge base — and Swarit "
     "itself is ready to move."},
    {"id": "other_project_question", "patterns": [], "answer": {},
     "prompt_guidance": "'Tell me about Sahaj' / 'What's the price of Aura?' / "
     "'Which projects do you have?' → answer plainly and exactly from the "
     "knowledge base (location, size, price, status as written — never a guess), "
     "as much as they asked and no more. Then, if it fits, connect it back to "
     "Swarit — for instance that Swarit stands right opposite Sahaj — and carry on "
     "toward the Swarit visit. Never volunteer a project they did not ask about."},
    {"id": "swarit_details", "patterns": [], "answer": {},
     "prompt_guidance": "'Tell me more about Swarit' / 'how big' / 'what's the "
     "price' / 'how many bungalows' → answer like a person who knows the "
     "project, not a brochure: pick the two or three facts that fit what they "
     "have told you (a big family → the 5 BHK at five and a half to nearly seven "
     "thousand square feet on a bigger plot; a budget question → two and a half "
     "to three and a half crore and what that buys; kids → the play area, the "
     "lawn, the pool), say numbers the way people say them on the phone, and "
     "end with one question back. Never recite the whole list in one breath."},
    {"id": "what_options", "patterns": [], "answer": {},
     "prompt_guidance": "'What options do you have?' / 'what do you have in "
     "Vapi?' / 'which projects are available?' → Swarit first, by name, with "
     "what it is (4 and 5 BHK bungalows, New Chala, ready to move); then, only "
     "because they asked, the other Vapi projects in ONE short clause, exactly "
     "as the knowledge base has them; then back to the family and what they "
     "need. Never the list before Swarit."},
    {"id": "needs_smaller_home", "patterns": [], "answer": {},
     "prompt_guidance": "'I need a 2 BHK' / 'something smaller' / 'a flat, not a "
     "bungalow' → say honestly that Swarit is 4 and 5 BHK bungalows, and ask "
     "whether a bungalow could work for the family before assuming it can't. If "
     "it genuinely can't, take their requirement for the team to follow up on; "
     "answer about other projects only if they ask what else we have."},
]


def _guardrails(g: dict) -> dict:
    g = copy.deepcopy(g)
    g["never_rules"] = NEVER_RULES
    g["always_rules"] = ALWAYS_RULES
    return _rename(g)


def _objection_playbook(direction: str, old: list[dict]) -> list[dict]:
    by = {o["id"]: copy.deepcopy(o) for o in old}
    out = copy.deepcopy(OBJECTION_PLAYBOOK)
    # the direction-specific "how did you get my number" answer is kept as-is
    if "how_got_number" in by:
        out.append(by["how_got_number"])
    return out


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build(direction: str, d: dict, swarit: dict) -> dict:
    d = _rename(copy.deepcopy(d))
    ident = d["identity"]
    ident["agent_name"] = "Ravi"
    ident["role_description"] = ("Swarit sales consultant — inbound" if direction == "inbound"
                                 else "Swarit sales consultant — follow-up calls")
    # Owner, 2026-09-24: primary language is Hindi. Seeds the router's default
    # (bridge line ~813) — the second beat and the first TTS accent are Hindi
    # until the caller's own language shows; gu/en stay supported.
    d["languages"]["default"] = "hi"

    cf = d["call_flow"]
    cf["stages"] = _stages(direction, cf["stages"])
    cf["call_goal"] = (
        "Understand what this family is looking for, present Pramukh Swarit — 4 "
        "and 5 BHK bungalows in New Chala, Vapi — as the home for them, answer any "
        "question about any Pramukh Group project honestly, and invite them to walk "
        "Swarit at a time that suits them.")
    cf["termination"]["end_conditions"] = [
        "once the Swarit visit is fixed, read back, and the WhatsApp follow-up is "
        "confirmed, wrap up warmly",
        "if the caller has clearly declined a visit and a follow-up is agreed, thank "
        "them and let them go",
    ]

    d["knowledge_base"] = _knowledge(d["knowledge_base"], swarit)
    d["slots"] = _slots(d["slots"])
    d["guardrails"] = _guardrails(d["guardrails"])
    d["response_bank"]["objection_playbook"] = _objection_playbook(
        direction, d["response_bank"]["objection_playbook"])

    pers = d["personality"]
    pers["custom_instructions"] = CUSTOM_INSTRUCTIONS
    pers["traits"] = ["professional", "knowledgeable", "consultative", "unhurried",
                      "attentive", "genuinely helpful", "natural — never repeats itself"]

    d["follow_up"]["whatsapp_promise"]["assets"] = [
        "Swarit brochure", "Swarit site location pin (opposite Pramukh Sahaj, New Chala)",
        "Ravi's direct number"]

    if direction == "outbound":
        d["outbound"]["direction_brief"] = (
            "You're following up with someone who enquired about a Pramukh Group "
            "project. Find out what they were looking at and what the family needs, "
            "present Swarit — 4 and 5 BHK bungalows in New Chala, Vapi — as the fit, "
            "answer honestly about any other project they ask about, and invite them "
            "for a Swarit site visit.")

    for k in ("revision", "updated_at", "updated_by"):
        d.pop(k, None)
    return d


def _report(slug: str, agents_dir: Path) -> None:
    agent = AgentDefinition.from_yaml(agents_dir / slug / "agent.yaml")
    pf = preflight_agent(agent, languages=["en", "hi", "gu"])
    status = pf.get("status") if isinstance(pf, dict) else getattr(pf, "status", pf)
    print(f"  preflight: {status}")
    if status != "ok":
        print(json.dumps(pf, ensure_ascii=False, indent=1, default=str)[:3000])
    direction = agent.call_direction
    surf = resolve_spoken_surfaces(agent, direction)
    for lang in ("en", "hi", "gu"):
        s = surf.get(lang) if isinstance(surf, dict) else None
        print(f"  [{lang}] surfaces: {json.dumps(s, ensure_ascii=False, default=str)[:400]}")
    kb = agent.knowledge_base
    for slots in ({}, {"preferred_location": "Surat"},
                  {"preferred_location": "Vapi", "configuration_required": "2 BHK"}):
        hits = evaluate_match_rules(kb, slots)
        print(f"  rules{slots} → {[h['recommend'] for h in hits]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--show-prompt", action="store_true")
    args = ap.parse_args()
    if not (args.dry_run or args.apply):
        ap.error("--dry-run or --apply")

    swarit = yaml.safe_load(KNOWLEDGE.read_text(encoding="utf-8"))["catalog_item"]

    if args.dry_run:
        scratch = Path(__file__).resolve().parent / "agents_dry"
        shutil.rmtree(scratch, ignore_errors=True)
        for slug in SLUGS.values():
            (scratch / slug).mkdir(parents=True)
            shutil.copy(LIVE_AGENTS / slug / "agent.yaml", scratch / slug / "agent.yaml")
        agents_dir, owner, is_admin = scratch, "", True
    else:
        agents_dir, owner, is_admin = LIVE_AGENTS, OWNER, True

    for direction, slug in SLUGS.items():
        current = yaml.safe_load((agents_dir / slug / "agent.yaml").read_text(encoding="utf-8"))
        assert current["identity"]["agent_name"] in ("Prakhar", "Vikrant", "Ravi"), current["identity"]
        new = build(direction, current, swarit)
        res = store.write_agent(slug, new, actor=ACTOR, reason=REASON, owner=owner,
                                is_admin=is_admin, agents_dir=agents_dir)
        print(f"{slug}: revision {res['revision']} effective={res['effective']}")
        _report(slug, agents_dir)
        if args.show_prompt:
            agent = AgentDefinition.from_yaml(agents_dir / slug / "agent.yaml")
            print("\n".join(pb.portfolio_block(agent, "en")))
            print("\n".join(pb.match_rules_block(agent, {"preferred_location": "Surat"}, "recommend")))
            print("\n".join(pb.match_rules_block(agent, {}, "recommend")))
            print("\n".join(pb.recommend_first_block(agent, {})))
            print(render_catalog_prompt(agent.knowledge_base)[:600])


if __name__ == "__main__":
    main()
