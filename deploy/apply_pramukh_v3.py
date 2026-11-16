#!/usr/bin/env python3
"""Pramukh agents on the customer account — the v3 changes (RUNBOOK §85).

Owner, 2026-11-13, after the 2026-10-22 / 2026-11-04 campaign calls on
patelashishk007@gmail.com (calls 1126, 1127, 1130, 1131, 1136, 1137, 1188,
1190, 1193): apply the fixes to EVERY agent on that account, and put the
same agents on angelo@dhronai.com with a `_v3` suffix.

What it writes on each agent (through the store — one revision per agent,
rollback-safe; the engine side needs `docker restart lescall-runtime`):

  call_flow.recovery            enabled + the owner's lines (never-showed-
                                interest / don't-call close, cost-sheet ack)
  outbound.identity_ack_line    "OK." the instant identity is confirmed
  outbound.busy_response        the owner's busy wording
  response_bank.objection_responses[0]   the same wording (was "मैं ज़्यादा
                                टाइम नहीं लूँगा…")
  call_flow.turn_taking         answer_hold_ms (short hold for a complete
                                answer); scripted agents' pause_hold_ms
                                1500 → 1100
  stages[*].script.entry_line   scripted agents only: the first requirement
                                line, spoken with no model turn on the
                                caller's "yes, go ahead"
  stage goal (intro)            "never enquired" → apologise + remove, not
                                "it may have been a portal"
  llm_config.extra_*_rules      the "didn't catch that" re-ask is only for
                                an unintelligible answer; nothing after a
                                goodbye

    calling-agent/venv/bin/python deploy/apply_pramukh_v3.py --dry-run
    docker exec -i lescall-api python - --apply   < deploy/apply_pramukh_v3.py
    docker exec -i lescall-api python - --copy-v3 < deploy/apply_pramukh_v3.py
"""
from __future__ import annotations

import argparse
import copy
import re
import sys
from pathlib import Path

REPO = Path("/home/angelo/lescall")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "calling-agent"))
sys.path.insert(0, str(REPO / "dashboard"))

import yaml  # noqa: E402

LIVE_AGENTS = REPO / "calling-agent" / "agents"
CUSTOMER = "patelashishk007@gmail.com"
STAFF = "angelo@dhronai.com"
ACTOR = "tech@dhronai.com:pramukh-v3-2026-11-14"
REASON = ("v3: busy / refusal / wrong-number / cost-sheet handling, OK after "
          "identity, answer-aware pause hold; removal offered and confirmed "
          "before the close, Gujarati understood but answered in Hindi "
          "(owner review 2026-11-13/14, RUNBOOK §85)")

# customer family → the `_v3` family on the staff account
V3_FAMILY = {
    "pramukh_aristo_test-2": "pramukh_aristo_v3",
    "pramukh_blue_stone_test-2": "pramukh_blue_stone_v3",
    "pramukh_green_county-2-2": "pramukh_green_county_v3",
    "pramukh_silver_stone-2-2": "pramukh_silver_stone_v3",
    "pramukh_vedanta_test-2": "pramukh_vedanta_v3",
    "pramukh_swarit_test-2": "pramukh_swarit_v3",
    "pramukh_vivan-2": "pramukh_vivan_v3",
    "outbound_lead_qualifier": "pramukh_ravi_swarit_v3",
}

BUSY = {
    "hi": "कोई बात नहीं। अगर अभी ठीक नहीं है तो बता दीजिएगा आप कब available "
          "रहेंगे, ताकि हम बात कर सकें।",
    "en": "No problem at all. If now isn't a good time, just tell me when "
          "you'll be available so we can talk.",
    "gu": "કોઈ વાંધો નહીં. અત્યારે ઠીક ન હોય તો કહેજો તમે ક્યારે available "
          "રહેશો, જેથી આપણે વાત કરી શકીએ.",
}
OLD_OBJECTION_HEADS = {
    "hi": "ज़्यादा टाइम नहीं लूँग",
    "en": "I'll keep it short",
    "gu": "હું વધુ સમય નહીં લઉં",
}
RECOVERY = {
    "enabled": True,
    "no_interest_close": {
        "hi": "माफ़ कीजिए, आपको परेशान करने के लिए। मैं आपका नंबर list से हटा "
              "देता हूँ। आपका दिन शुभ हो।",
        "en": "I'm sorry to have troubled you. I'll take your number off our "
              "list. Have a good day.",
        "gu": "તમને તકલીફ આપવા બદલ માફ કરશો. હું તમારો નંબર list માંથી કાઢી "
              "નાખું છું. તમારો દિવસ શુભ રહે.",
    },
    # The same promise naming the project under discussion (call 1224).
    "share_ack_project": {
        "hi": "ज़रूर, मैं इस call के तुरंत बाद {project} की complete cost "
              "sheet और details आपके WhatsApp पर share कर देता हूँ।",
        "en": "Of course — I'll send the {project} cost sheet and details "
              "to you on WhatsApp right after this call.",
    },
    # Owner flow 2026-11-14: "I never showed interest" → offer removal →
    # confirm once more → close; a "no" at either step keeps the call.
    "removal_confirm": True,
    "removal_offer": {
        "hi": "माफ़ कीजिएगा अगर आपको Pramukh properties में अब दिलचस्पी नहीं "
              "है। अगर आप बोलें तो मैं list से आपका नाम हटा दूँ?",
        "en": "I'm sorry if you are no longer interested in Pramukh "
              "properties. If you'd like, shall I take your name off the "
              "list?",
    },
    "removal_confirm_line": {
        "hi": "बस confirmation के लिए आख़िरी बार बता दीजिए, ताकि हम आपको "
              "दोबारा call न करें — आपका नाम contact list से हटा दूँ?",
        "en": "Just to confirm one last time, so that we don't call you "
              "again — shall I remove your name from the contact list?",
    },
    "removal_keep": {
        "hi": "ठीक है {honorific}, अगर आप चाहते हैं कि हम नाम न हटाएँ तो "
              "continue करते हैं।",
        "en": "All right {honorific}, since you'd like us to keep your "
              "name, let's continue.",
    },
    "removal_close": {
        "hi": "तकलीफ़ के लिए माफ़ी माँगता हूँ, आपका दिन शुभ हो।",
        "en": "I apologise for the trouble. Have a good day.",
    },
    "share_ack": {
        "hi": "ज़रूर, मैं इस call के तुरंत बाद complete cost sheet और project "
              "details आपके WhatsApp पर share कर देता हूँ।",
        "en": "Of course — I'll send the complete cost sheet and project "
              "details to you on WhatsApp right after this call.",
        "gu": "ચોક્કસ, આ call પછી તરત જ હું complete cost sheet અને project "
              "details તમારા WhatsApp પર share કરી દઉં છું.",
    },
}
ARISTO_TURN_TAKING = {
    "pause_hold_ms": 1100, "max_merged_fragments": 6,
    "backchannel_guard": True, "sustained_barge_ms": 700,
    "tail_barge_in": True, "answer_hold_ms": 400,
}
IDENTITY_ACK = {"hi": "OK.", "en": "OK.", "gu": "OK."}

RULE_ALWAYS = (
    "use the 'माफ़ कीजिए, ठीक से सुनाई नहीं दिया' re-ask ONLY when the "
    "caller's answer was genuinely unintelligible; when they ask a question "
    "or make a request instead of answering, answer or acknowledge that "
    "first, then ask your question once")
RULE_NEVER = (
    "ask a qualification question, pitch or invite a visit after the caller "
    "has declined the call, asked not to be called, or after you have said "
    "goodbye or promised a callback")
OLD_NO_RECALL = re.compile(
    r"If they don't recall enquiring, don't argue — say it may have been a "
    r"portal or a site form, and ask whether property is still on their "
    r"mind at all\.")
NEW_NO_RECALL = (
    "If they say they never enquired or never showed interest, do not argue "
    "and do not pitch: apologise for the disturbance, say you will remove "
    "their number from the list, and end the call.")
NEW_NO_RECALL_V2 = (
    "If they say they never enquired or never showed interest, do not argue "
    "and do not pitch: apologise and ask whether they would like their name "
    "removed from the list. Remove it and end the call only after they have "
    "confirmed; if they say no, carry on with the call.")
RULE_GUJARATI = (
    "reply in Hindi even when the caller speaks Gujarati or their words "
    "arrive in Gujarati script — understand what they said and answer it in "
    "Hindi")
ENTRY_RE = re.compile(r'Say ONLY, word for word:\s*"([^"]{40,400})"')


def patch_agent(d: dict) -> tuple[dict, list[str]]:
    """The v3 definition for `d` + a human list of what changed."""
    new = copy.deepcopy(d)
    notes: list[str] = []
    outbound_agent = str(new.get("call_direction") or "") == "outbound"

    cf = new.setdefault("call_flow", {})
    want_rec = copy.deepcopy(RECOVERY)
    if _agent_is_female(new):
        want_rec["removal_close"]["hi"] = want_rec["removal_close"]["hi"] \
            .replace("माँगता", "माँगती")
    if cf.get("recovery") != want_rec:
        cf["recovery"] = want_rec
        notes.append("call_flow.recovery")

    # The WhatsApp share tracker (RUNBOOK §72) on every agent with a
    # catalogue: the model is told which project the details are about.
    # The Ravi pair never had it (call 1224: Aura agreed, Swarit promised).
    fu = new.setdefault("follow_up", {})
    wp = fu.get("whatsapp_promise")
    kb = new.get("knowledge_base") or {}
    if (isinstance(wp, dict) and wp.get("enabled")
            and not wp.get("track_projects") and kb.get("catalog")):
        wp["track_projects"] = True
        wp.setdefault("offer_from_roles",
                      ["recommend", "scheduling", "confirmation"])
        notes.append("follow_up.whatsapp_promise.track_projects")

    # Gujarati is understood, never spoken (owner 2026-11-14): the reply
    # language is bounded by languages.supported.
    lg = new.setdefault("languages", {})
    # An answer that only SOUNDS like an offered option is matched by
    # sound, or confirmed with the caller (RUNBOOK §85.12, call 1232).
    # The option re-read Aristo and Blue Stone already have (RUNBOOK §81),
    # wherever STT is locked to one language.
    if lg.get("stt_forced_language") and not lg.get("stt_enum_recheck"):
        lg["stt_enum_recheck"] = True
        notes.append("languages.stt_enum_recheck")
    if not lg.get("phonetic_match"):
        lg["phonetic_match"] = True
        notes.append("languages.phonetic_match")
    sup = list(lg.get("supported") or [])
    if "gu" in sup:
        lg["supported"] = [x for x in sup if x != "gu"]
        notes.append(f"languages.supported {sup} → {lg['supported']}")

    # 2026-11-16 (owner: "every agent must behave like the Aristo v3 I just
    # tested"): ONE turn-taking profile for all eight families — the pause
    # hold, short-answer hold, backchannel guard and barge-in handling the
    # owner signed off on Aristo. The generic families used to carry only
    # the short-answer hold.
    tt = dict(cf.get("turn_taking") or {})
    want_tt = {**tt, **ARISTO_TURN_TAKING}
    if want_tt != tt:
        cf["turn_taking"] = want_tt
        notes.append(f"call_flow.turn_taking {tt or '{}'} → {want_tt}")

    ob = new.setdefault("outbound", {})
    if ob.get("identity_ack_line") != IDENTITY_ACK:
        ob["identity_ack_line"] = dict(IDENTITY_ACK)
        notes.append("outbound.identity_ack_line")
    br = dict(ob.get("busy_response") or {})
    if any(br.get(k) != v for k, v in BUSY.items()):
        br.update(BUSY)
        ob["busy_response"] = br
        notes.append("outbound.busy_response")

    rb = new.setdefault("response_bank", {})
    objs = rb.get("objection_responses")
    if isinstance(objs, dict):
        for lang, head in OLD_OBJECTION_HEADS.items():
            lines = objs.get(lang)
            if isinstance(lines, list):
                for i, line in enumerate(lines):
                    if head in str(line):
                        lines[i] = BUSY[lang]
                        notes.append(f"response_bank.objection_responses.{lang}[{i}]")
            elif isinstance(lines, str) and head in lines:
                objs[lang] = BUSY[lang]
                notes.append(f"response_bank.objection_responses.{lang}")

    for st in cf.get("stages") or []:
        goal = str(st.get("goal") or "")
        if OLD_NO_RECALL.search(goal):
            goal = OLD_NO_RECALL.sub(NEW_NO_RECALL_V2, goal)
            st["goal"] = goal
            notes.append(f"stages.{st.get('id')}.goal (never-enquired)")
        if NEW_NO_RECALL in goal:
            goal = goal.replace(NEW_NO_RECALL, NEW_NO_RECALL_V2)
            st["goal"] = goal
            notes.append(f"stages.{st.get('id')}.goal (removal is confirmed)")
        script = st.get("script")
        if (outbound_agent and isinstance(script, dict)
                and script.get("ask_verbatim") and not script.get("entry_line")):
            m = ENTRY_RE.search(goal)
            if m and st is _first_scripted(cf):
                script["entry_line"] = {"hi": m.group(1).strip()}
                notes.append(f"stages.{st.get('id')}.script.entry_line")

    lc = new.setdefault("llm_config", {})
    always = list(lc.get("extra_always_rules") or [])
    if RULE_ALWAYS not in always:
        always.append(RULE_ALWAYS)
        lc["extra_always_rules"] = always
        notes.append("llm_config.extra_always_rules")
    if RULE_GUJARATI not in always:
        always.append(RULE_GUJARATI)
        lc["extra_always_rules"] = always
        notes.append("llm_config.extra_always_rules (Gujarati → Hindi)")
    never = list(lc.get("extra_never_rules") or [])
    if RULE_NEVER not in never:
        never.append(RULE_NEVER)
        lc["extra_never_rules"] = never
        notes.append("llm_config.extra_never_rules")
    return new, notes


def _agent_is_female(d: dict) -> bool:
    try:
        from backend.app.agent_definition.schema import AgentDefinition
        pr = str(AgentDefinition.from_dict(copy.deepcopy(d))
                 .identity.pronouns or "").lower()
        return pr.startswith("she")
    except Exception:
        return False


def _first_scripted(cf: dict):
    for st in cf.get("stages") or []:
        sc = st.get("script")
        if isinstance(sc, dict) and sc.get("ask_verbatim"):
            return st
    return None


def _customer_slugs() -> list[str]:
    from shared.db.database import get_db
    with get_db() as db:
        rows = db.execute(
            "SELECT slug FROM agents WHERE owner=? AND archived_at IS NULL "
            "ORDER BY id", (CUSTOMER,)).fetchall()
    return [r["slug"] for r in rows
            if (LIVE_AGENTS / r["slug"] / "agent.yaml").exists()]


def _disk_slugs() -> list[str]:
    """Dry-run without a database: the customer families by name."""
    fams = tuple(V3_FAMILY)
    return sorted(p.name for p in LIVE_AGENTS.iterdir()
                  if p.is_dir() and re.sub(r"-(?:inbound|outbound)$", "",
                                           p.name) in fams)


def apply(dry: bool) -> None:
    from backend.app.agent_definition import store
    from backend.app.agent_definition.schema import AgentDefinition
    slugs = _disk_slugs() if dry else _customer_slugs()
    for slug in slugs:
        path = LIVE_AGENTS / slug / "agent.yaml"
        d = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        new, notes = patch_agent(d)
        AgentDefinition.from_dict(new)          # validates before any write
        if not notes:
            print(f"  = {slug}: already v3")
            continue
        print(f"  {'~' if dry else '✓'} {slug}:")
        for n in notes:
            print(f"        {n}")
        if dry:
            continue
        res = store.write_agent(slug, new, actor=ACTOR, reason=REASON,
                                owner=CUSTOMER, is_admin=True)
        print(f"        → revision {res['revision']}")


def copy_v3() -> None:
    """The customer's (already patched) agents onto the staff account as
    `<project>_v3-inbound/-outbound` — new, inactive, with their documents."""
    import agent_builder_api as builder
    import agent_share_api as share
    from backend.app.agent_definition.registry import agent_registry
    from shared.db.database import get_db
    taken = share._existing_agent_ids()
    for slug in _customer_slugs():
        m = re.match(r"^(.*)-(inbound|outbound)$", slug)
        if not m or m.group(1) not in V3_FAMILY:
            print(f"  ! {slug}: no v3 family mapped — skipped")
            continue
        new_slug = f"{V3_FAMILY[m.group(1)]}-{m.group(2)}"
        src = LIVE_AGENTS / slug / "agent.yaml"
        if (LIVE_AGENTS / new_slug).exists():
            # Refresh an existing v3 copy in place (keeps its agent_id).
            from backend.app.agent_definition import store
            cur = yaml.safe_load((LIVE_AGENTS / new_slug / "agent.yaml")
                                 .read_text(encoding="utf-8")) or {}
            spec = share._replace_spec(src, str(cur.get("agent_id") or ""))
            same = {k: v for k, v in spec.items()
                    if k not in share._HISTORY_FIELDS} == {
                        k: v for k, v in cur.items()
                        if k not in share._HISTORY_FIELDS}
            if same:
                print(f"  = {new_slug}: up to date")
                continue
            res = store.write_agent(new_slug, spec, actor=ACTOR,
                                    reason=REASON + f" (from {slug})",
                                    owner=STAFF, is_admin=True)
            print(f"  ✓ {new_slug}: refreshed from {slug} → revision "
                  f"{res['revision']}")
            continue
        spec = share._prepare_spec(src, new_slug, taken)
        agent = builder.write_agent_dir(new_slug, spec, STAFF)
        if builder._slug_owner(new_slug) != STAFF:
            raise SystemExit(f"{new_slug}: ownership claim failed")
        share._deactivate(new_slug)
        docs = share._copy_documents(slug, new_slug)
        with get_db() as db:
            db.execute("UPDATE agents SET display_name=? WHERE slug=?",
                       (f"{agent.identity.agent_name} v3", new_slug))
        agent_registry.invalidate(new_slug)
        print(f"  ✓ {new_slug}: created from {slug} ({docs} document(s))")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--copy-v3", action="store_true")
    args = ap.parse_args()
    if not (args.dry_run or args.apply or args.copy_v3):
        ap.error("--dry-run, --apply or --copy-v3")
    if args.dry_run or args.apply:
        apply(dry=not args.apply)
    if args.copy_v3:
        copy_v3()


if __name__ == "__main__":
    main()
