"""
prompt_blocks — THE single owner of the system prompt's doctrine bands (D13).

Every band that was hardcoded in conversation.py's _build_system_prompt lives
here as a pure function of (agent_def, …), parameterized by AgentSpec v2.
IRON RULE, enforced by the golden-prompt harness: with an all-default agent
spec every function renders the pre-v2 prompt text byte-for-byte.

No I/O, no globals, no state — pure string building.
"""

from __future__ import annotations

from ..agent_definition.schema import AgentDefinition
from ..agent_definition.catalog import (
    catalog_item_names,
    evaluate_match_rules,
    render_catalog_prompt,
)
from .stage_roles import stage_role as _effective_stage_role

_COUNT_WORDS = {1: "ONE", 2: "TWO", 3: "THREE", 4: "FOUR", 5: "FIVE"}


def _lang_pick(d: dict, lang: str) -> str:
    """lang → en → first value → ''. The universal per-language chain."""
    if not isinstance(d, dict) or not d:
        return ""
    return str(d.get(lang) or d.get("en") or next(iter(d.values()), "") or "")


def _offer_count(agent_def: AgentDefinition) -> int:
    sched = agent_def.call_flow.scheduling or {}
    try:
        return max(1, int(sched.get("offer_count", 2)))
    except (TypeError, ValueError):
        return 2


def _sched_style(agent_def: AgentDefinition) -> str:
    style = str((agent_def.call_flow.scheduling or {}).get("style") or "exact_n")
    return style if style in ("exact_n", "flexible") else "exact_n"


def _has_offerable_slots(agent_def: AgentDefinition) -> bool:
    """True when the calendar actually holds slots the agent could offer."""
    cal = getattr(agent_def, "calendar", None)
    if cal is None or str(getattr(cal, "type", "mock") or "mock") == "none":
        return False
    return bool(getattr(cal, "mock_slots", None))


def _sched_rules(agent_def: AgentDefinition, exact_n_lines: list[str],
                 no_slots_line: str) -> list[str]:
    """Scheduling rules for a FIRM RULES pack.

    Both styles presume a slot list. With an empty calendar, exact_n ORDERS
    the model to invent times (live call 2026-07-28 produced a fabricated
    "Tuesday at 10 AM or Wednesday at 2 PM") and flexible references free
    slots that don't exist. Slotless agents get a no-invention rule instead —
    ask the caller, confirm their proposal — regardless of style.
    """
    if (agent_def.call_flow.scheduling or {}).get("enabled") is False:
        return []
    if not _has_offerable_slots(agent_def):
        return [no_slots_line]
    if _sched_style(agent_def) == "flexible":
        return [
            "✓ Offer available time slots flexibly and accept a caller-proposed time when it matches a free slot",
        ]
    return exact_n_lines


def _count_word(n: int) -> str:
    return _COUNT_WORDS.get(n, str(n))


def _has_phone_readback_slot(agent_def: AgentDefinition) -> bool:
    """D5 — the never-read-back-phone rule flips ONLY when the agent defines
    a phone-type slot with a confirm_readback."""
    return any(
        getattr(s, "type", "") == "phone" and getattr(s, "confirm_readback", "")
        for s in (agent_def.slots or [])
    )


# ---------------------------------------------------------------------------
# Identity / pronouns
# ---------------------------------------------------------------------------

def pronoun_instruction(agent_def: AgentDefinition) -> str:
    _pers = getattr(agent_def, "personality", None)
    _pronouns = (_pers.pronouns if _pers else None) or getattr(agent_def.identity, "pronouns", "she")
    _is_female = _pronouns.lower() == "she"
    return (
        "Your pronouns are she/her. Use feminine agreement in every language. "
        "In Hindi: ALWAYS use chahti, sakti, aayi, gayi, karungi, bataungi, samjhi, rahi — "
        "NEVER chahta, sakta, aaya, gaya, karunga, batauga, samjha, raha. "
        "In Gujarati: use feminine verb forms consistently."
        if _is_female else
        "Your pronouns are he/him. Use masculine agreement in every language. "
        "In Hindi: ALWAYS use chahta, sakta, aaya, gaya, karunga, batauga, samjha, raha — "
        "NEVER chahti, sakti, aayi, gayi, karungi, bataungi, samjhi, rahi. "
        "In Gujarati: use masculine verb forms consistently."
    )


def _ai_disclosure_line(agent_def: AgentDefinition) -> str:
    """'deflect' (default) = today's exact not-a-script line."""
    mode = getattr(agent_def.personality, "ai_disclosure", "deflect") or "deflect"
    if mode == "admit":
        return ("If asked whether you are an AI or a real person, admit honestly and warmly "
                "that you are an AI assistant — then continue helping without making it awkward.")
    if mode == "admit_if_pressed":
        return ("You speak like a real person. If the caller directly and insistently asks "
                "whether you are an AI, admit it honestly — otherwise continue naturally.")
    return "You are NOT reading a script. You are a real person having a real conversation."


def identity_block(agent_def: AgentDefinition) -> list[str]:
    """━━ AGENT IDENTITY ━━ — persona framing + pronoun agreement."""
    role_line = getattr(agent_def.identity, "role_line", "") or (
        f"You are {agent_def.identity.agent_name} — a warm, genuine, experienced "
        f"{agent_def.identity.role_description} for {agent_def.identity.company_name}."
    )
    return [
        "",
        "━━ AGENT IDENTITY ━━",
        role_line,
        _ai_disclosure_line(agent_def),
        "Your job: make every caller feel genuinely heard and understood.",
        pronoun_instruction(agent_def),
    ]


# ---------------------------------------------------------------------------
# Call mission (v2 — D1)
# ---------------------------------------------------------------------------

def mission_block(agent_def: AgentDefinition) -> list[str]:
    """━━ CALL MISSION ━━ — only when call_flow.call_goal is set."""
    goal = (agent_def.call_flow.call_goal or "").strip()
    if not goal:
        return []
    return [
        "",
        "━━ CALL MISSION ━━",
        goal,
        "Every turn must move the call toward this mission.",
    ]


# ---------------------------------------------------------------------------
# Personality
# ---------------------------------------------------------------------------

def personality_block(agent_def: AgentDefinition) -> list[str]:
    """━━ PERSONALITY ━━ — energy / tone / style / lingo / persona notes."""
    _pers = getattr(agent_def, "personality", None)
    if not _pers:
        return []
    _energy_map = {
        "low": "Calm and measured. Speak slowly and thoughtfully. Never rush.",
        "medium": "Balanced energy — warm and engaged without being overpowering.",
        "high": "High energy and enthusiasm. Be upbeat, punchy, and genuinely excited. Short sentences. Positive tone.",
    }
    _tone_map = {
        "professional": "Maintain a polished, formal tone. Precise language. No slang.",
        "conversational": "Conversational and natural. Speak like a knowledgeable friend, not a corporate bot.",
        "friendly": "Warm, friendly, and encouraging. Use light informal language. Make the caller feel comfortable.",
        "formal": "Strictly formal. No contractions. Deferential and structured.",
    }
    _style_map = {
        "concise": "1-2 sentences maximum per turn. Be direct.",
        "natural": "2-3 sentences per turn. Natural conversational flow.",
        "detailed": "3-4 sentences. Give context and explanation where useful.",
    }
    lines = [
        "",
        "━━ PERSONALITY ━━",
        f"Energy: {_energy_map.get(_pers.energy, _energy_map['medium'])}",
        f"Tone: {_tone_map.get(_pers.tone, _tone_map['conversational'])}",
        f"Response length: {_style_map.get(_pers.style, _style_map['natural'])}",
    ]
    traits = getattr(_pers, "traits", None) or []
    if traits:
        lines.append(f"Traits: {', '.join(traits)}.")
    if _pers.lingo:
        lines.append(f"Vocabulary: {_pers.lingo}")
    if _pers.custom_instructions:
        lines.append(f"Persona: {_pers.custom_instructions}")
    return lines


# ---------------------------------------------------------------------------
# Call direction (v2 outbound substitution — D6)
# ---------------------------------------------------------------------------

def direction_block(
    agent_def: AgentDefinition,
    direction: str,
    outbound_contact_name: str = "",
    outbound_contact_phone: str = "",
    lang: str = "en",
    callback_context: str = "",
) -> list[str]:
    """━━ CALL TYPE ━━ — inbound vs outbound approach (+ outbound contact).

    Per-agent outbound script lines substitute the generic prose ONLY when the
    corresponding `outbound.*` field is non-empty (all-empty = today's exact
    prose)."""
    _direction = (direction or "inbound").lower()
    ob = getattr(agent_def, "outbound", None)
    lines: list[str] = ["", ]
    if _direction == "outbound":
        purpose_line = (
            "▸ Your purpose: connect with them, pitch the relevant product/service, and resolve any queries they had requested answers for."
        )
        brief = (getattr(ob, "direction_brief", "") or "").strip() if ob else ""
        if brief:
            purpose_line = f"▸ Your purpose: {brief}"
        busy_line = "▸ If they're busy, offer to keep it to one minute or ask for a better time — do NOT just hang up."
        busy_resp = _lang_pick(getattr(ob, "busy_response", {}) or {}, lang) if ob else ""
        if busy_resp:
            busy_line = f"▸ If they're busy, say: '{busy_resp}' — do NOT just hang up."
        lines += [
            "━━ CALL TYPE: OUTBOUND — YOU PLACED THIS CALL ━━",
            "You dialed this person; they did not call you. That changes everything about your approach:",
            "▸ YOU LEAD THE CALL. Never wait for the other person to drive — you always know the next step and steer toward it.",
            "▸ State who you are and why you're calling within the first two turns. Never leave them guessing.",
            purpose_line,
            "▸ Respect their time — they didn't plan this call. Be warm but purposeful; every turn moves toward your goal.",
            busy_line,
            "▸ Handle 'not interested' gracefully once, with one strong value point — then respect a second no.",
        ]
        pitch = _lang_pick(getattr(ob, "pitch", {}) or {}, lang) if ob else ""
        if pitch:
            lines.append(f"▸ Your core pitch: {pitch}")
        good_time = _lang_pick(getattr(ob, "good_time_check_line", {}) or {}, lang) if ob else ""
        if good_time:
            lines.append(f"▸ Early in the call, check it's a good time: '{good_time}'")
        wrong_number = _lang_pick(getattr(ob, "wrong_number_close", {}) or {}, lang) if ob else ""
        if wrong_number:
            lines.append(f"▸ If it's clearly a wrong number, close politely: '{wrong_number}'")
        if outbound_contact_name:
            _contact_phone_note = (
                " (number stored internally — never read it out unless they ask you to confirm it)"
                if outbound_contact_phone else ""
            )
            confirm_line = (
                f"▸ Early in the call, confirm you're speaking with the right person: e.g. 'Am I speaking with {outbound_contact_name}?'"
            )
            id_confirm = _lang_pick(getattr(ob, "identity_confirm_line", {}) or {}, lang) if ob else ""
            if id_confirm:
                id_confirm = id_confirm.replace("{contact_name}", outbound_contact_name)
                confirm_line = f"▸ Early in the call, confirm you're speaking with the right person: '{id_confirm}'"
            wrong_person_line = (
                f"▸ If it IS them, use their name naturally (not every turn). If it's someone else, politely ask if {outbound_contact_name} is available."
            )
            wrong_person = _lang_pick(getattr(ob, "wrong_person_response", {}) or {}, lang) if ob else ""
            if wrong_person:
                wrong_person = wrong_person.replace("{contact_name}", outbound_contact_name)
                wrong_person_line = (
                    f"▸ If it IS them, use their name naturally (not every turn). If it's someone else, say: '{wrong_person}'"
                )
            lines += [
                "",
                f"▸ CONTACT: You are calling {outbound_contact_name}{_contact_phone_note}.",
                confirm_line,
                wrong_person_line,
                "▸ Do NOT re-ask for their name — you already have it.",
            ]
    else:
        lines += [
            "━━ CALL TYPE: INBOUND — THE CALLER CAME TO YOU ━━",
            "They dialed in with a reason: a question, an issue, a request, or interest in a product/service.",
            "▸ LISTEN FIRST. Find out why they called before pitching anything.",
            "▸ Your purpose: accept and resolve their issues, requests and demands; answer questions accurately; schedule appointments/visits when they're interested.",
            "▸ Make them feel welcomed and heard — they chose to reach out; reward that.",
            "▸ Once their need is clear, guide them decisively to the resolution or the booking — don't leave them hanging.",
        ]
    if callback_context:
        lines += [
            "",
            "━━ WHY YOU ARE CALLING BACK ━━",
            callback_context,
            "▸ Reference this context naturally — the person expects this call; never open cold.",
        ]
    return lines


# ---------------------------------------------------------------------------
# Known customer (persistent memory — Customer Intelligence Hub)
# ---------------------------------------------------------------------------

def customer_context_block(ctx: dict | None) -> list[str]:
    """━━ KNOWN CUSTOMER ━━ — pre-call context for a returning customer.

    `ctx` is the bridge's pre-fetched bundle: {name, calls_count, last_call_at,
    last_outcome, open_followups, open_commitments, digest}. Empty/None (new
    caller, flag off, fetch failed) renders nothing — a brand-new caller's
    prompt is byte-identical to before this feature."""
    if not ctx or not (ctx.get("digest") or ctx.get("calls_count")):
        return []
    lines: list[str] = ["", "━━ KNOWN CUSTOMER ━━"]
    head: list[str] = []
    if ctx.get("name"):
        head.append(str(ctx["name"]))
    n = ctx.get("calls_count")
    if n:
        head.append(f"{n} previous call(s)")
    if ctx.get("last_call_at"):
        last = str(ctx["last_call_at"])[:10]
        head.append(f"last {last}"
                    + (f" (outcome: {ctx['last_outcome']})" if ctx.get("last_outcome") else ""))
    if head:
        lines.append(" · ".join(head))
    if ctx.get("digest"):
        lines.append(str(ctx["digest"]))
    counters: list[str] = []
    if ctx.get("open_commitments"):
        counters.append(f"Open promises: {ctx['open_commitments']}")
    if ctx.get("open_followups"):
        counters.append(f"Pending follow-ups: {ctx['open_followups']}")
    if counters:
        lines.append(" · ".join(counters))
    lines += [
        "▸ Use this history naturally and only when relevant. NEVER recite this list to the customer.",
        "▸ Do NOT re-ask what you already know here (name, preferences, past discussions).",
        "▸ If they reference a past conversation, you remember it — respond accordingly.",
    ]
    return lines


# ---------------------------------------------------------------------------
# Brand (v2 structured render — L2)
# ---------------------------------------------------------------------------

def brand_block(agent_def: AgentDefinition) -> list[str]:
    """━━ BRAND ━━ — the exact legacy two lines when `brand` is all-empty;
    structured render (dangling-contact fixed) when any brand field is set."""
    br = getattr(agent_def, "brand", None)
    has_brand = br is not None and any([
        br.spoken_company_name, br.positioning_tagline, br.credibility_markers,
        br.trust_proofs, br.regions_served, br.contact_phone, br.whatsapp_number,
    ])
    if not has_brand:
        return [
            "",
            "━━ BRAND ━━",
            agent_def.identity.company_tagline,
            f"{' · '.join(agent_def.knowledge_base.locations)} · Contact: {agent_def.identity.contact}",
        ]
    lines = ["", "━━ BRAND ━━"]
    spoken = agent_def.effective_spoken_name()
    tagline = agent_def.effective_tagline()
    if spoken and tagline:
        lines.append(f"{spoken} — {tagline}")
    elif spoken or tagline:
        lines.append(spoken or tagline)
    geo_bits: list[str] = []
    regions = agent_def.effective_regions()
    if regions:
        geo_bits.append(" · ".join(regions))
    contact = agent_def.effective_contact_phone()
    if contact:
        geo_bits.append(f"Contact: {contact}")
    if geo_bits:
        lines.append(" · ".join(geo_bits))
    if br.credibility_markers:
        lines.append("Credibility: " + "; ".join(br.credibility_markers) + ".")
    if br.trust_proofs:
        lines.append("Trust proofs (your best answers to doubt — use at most one at a time): "
                     + "; ".join(br.trust_proofs) + ".")
    whatsapp = agent_def.effective_whatsapp_number()
    if br.whatsapp_number and whatsapp != contact:
        lines.append(f"WhatsApp: {whatsapp}")
    return lines


# ---------------------------------------------------------------------------
# Calendar (v2 scheduling config — L5)
# ---------------------------------------------------------------------------

def _has_scheduling_stage(agent_def: AgentDefinition) -> bool:
    """True when any stage's EFFECTIVE role (explicit, else derived from the
    legacy id) is scheduling/confirmation — i.e. the flow actually books.
    Empty stage list keeps today's behaviour (render)."""
    stages = getattr(agent_def.call_flow, "stages", None) or []
    if not stages:
        return True
    return any(
        _effective_stage_role(s, getattr(s, "id", "")) in
        ("scheduling", "confirmation")
        for s in stages
    )


def calendar_block(
    agent_def: AgentDefinition,
    appointment_time: str | None,
    free_slots: list,
) -> list[str]:
    """━━ APPOINTMENT CALENDAR ━━ — live availability (pure: caller passes
    the already-resolved free slot list)."""
    if not agent_def.calendar.mock_slots:
        return []
    if appointment_time:
        return []  # already scheduled — surfaced in CALL STATE above
    if not _has_scheduling_stage(agent_def):
        # A calendar band on a flow that never schedules steers the model
        # toward booking behaviour it has no stage for (Kaapd: six junk
        # 10:00 AM slots injected into a B2B sample-send call).
        return []
    if free_slots:
        _slot_list = ", ".join(f"{s.day} at {s.time}" for s in free_slots)
        if _sched_style(agent_def) == "flexible":
            offer_line = ("▸ Offer options from this list flexibly, and accept a "
                          "caller-proposed time when it matches a free slot.")
        else:
            offer_line = f"▸ Offer at most {_count_word(_offer_count(agent_def))} options at a time."
        return [
            "",
            "━━ APPOINTMENT CALENDAR (LIVE AVAILABILITY) ━━",
            f"Free slots right now: {_slot_list}",
            "▸ Offer ONLY slots from this list — never invent other times.",
            offer_line,
            "▸ When the caller asks what times are available, mention free slots from this list.",
            "▸ Once the caller picks a slot, confirm it back clearly — it will be reserved for them.",
        ]
    return [
        "",
        "━━ APPOINTMENT CALENDAR ━━",
        "All published slots are currently booked. Take the caller's preferred day/time as a request and say the team will call back to confirm — do NOT promise a specific slot.",
    ]


# ---------------------------------------------------------------------------
# Knowledge / portfolio (v2 catalog + fact policy — L3/D16)
# ---------------------------------------------------------------------------

def portfolio_block(agent_def: AgentDefinition, lang: str = "en") -> list[str]:
    """━━ {portfolio_section_label} ━━ — catalog render (verbatim fallback)."""
    kb = agent_def.knowledge_base
    lines = [
        "",
        f"━━ {kb.portfolio_section_label} ━━",
        render_catalog_prompt(kb),
    ]
    fact_policy = getattr(kb, "fact_policy", None) or {}
    freely = fact_policy.get("state_freely") or []
    defer = fact_policy.get("defer") or []
    if freely:
        lines.append("▸ You may state these facts freely whenever relevant: "
                     + ", ".join(str(x) for x in freely) + ".")
    if defer:
        defer_line = _lang_pick(fact_policy.get("defer_line") or {}, lang) \
            or "say the team will share the details"
        lines.append("▸ For these topics do NOT give specifics — " + defer_line + ": "
                     + ", ".join(str(x) for x in defer) + ".")
    return lines


def knowledge_grounding_block(agent_def: AgentDefinition) -> list[str]:
    """━━ KNOWLEDGE GROUNDING (STRICT) ━━ — deflect-not-invent, rendered
    directly after the knowledge section so 'the section above' stays true.
    Gated on guardrails.honesty.knowledge_grounding == 'strict' (a value no
    pre-existing agent carries). Absent/other → [] (zero bytes)."""
    gr = getattr(agent_def, "guardrails", None)
    honesty = (getattr(gr, "honesty", None) or {}) if gr else {}
    if str(honesty.get("knowledge_grounding") or "").strip() != "strict":
        return []
    label = agent_def.knowledge_base.portfolio_section_label
    return [
        "",
        "━━ KNOWLEDGE GROUNDING (STRICT) ━━",
        f"The {label} section above is your ONLY source of truth about what "
        "this business makes, sells, stocks, offers and charges.",
        "✗ NEVER name, confirm, or describe any product, service, variant, "
        "price, discount, stock level, or delivery timeline that is not "
        "written there — not even when the caller asks for it by name or "
        "assumes you have it.",
        "✗ NEVER answer a product, price, or availability question from "
        "general knowledge. If it is not in the section above, you do not "
        "know it.",
        "▸ When asked about something not covered there, deflect in ONE "
        "short sentence: offer to send the catalogue or have the team check "
        "and share exact details — then steer back to your CURRENT GOAL.",
        "▸ 'I'll check and get back to you' is a GOOD answer. A confident "
        "guess is a failed call.",
    ]


def match_rules_block(agent_def: AgentDefinition, slot_state: dict,
                      stage_role: str) -> list[str]:
    """━━ MATCH RULES ━━ — deterministic recommendation candidates, injected
    below CURRENT GOAL only in pitch/recommend stages. Empty rules → []."""
    if stage_role not in ("pitch", "recommend"):
        return []
    kb = agent_def.knowledge_base
    hits = evaluate_match_rules(kb, slot_state or {})
    if not hits:
        return []
    lines = ["", "━━ MATCH RULES (deterministic — follow these) ━━"]
    for hit in hits:
        names = catalog_item_names(kb, hit["recommend"])
        line = "▸ Recommend: " + ", ".join(names)
        if hit["reason"]:
            line += f" — {hit['reason']}"
        lines.append(line)
    lines.append("Recommend ONLY from these candidates unless the caller explicitly asks for something else.")
    return lines


# ---------------------------------------------------------------------------
# Conversation guidelines / style / anti-repetition
# ---------------------------------------------------------------------------

def guidelines_block(agent_def: AgentDefinition) -> list[str]:
    """━━ CONVERSATION GUIDELINES ━━ (verbosity + energy-mirroring gates)."""
    pers = getattr(agent_def, "personality", None)
    n = getattr(pers, "verbosity_max_sentences", 3) if pers else 3
    try:
        n = max(1, int(n))
    except (TypeError, ValueError):
        n = 3
    mirroring = getattr(pers, "energy_mirroring", True) if pers else True
    lines = [
        "",
        "━━ CONVERSATION GUIDELINES ━━",
        "LISTEN + QUALIFY: Acknowledge what the caller said, THEN always move the qualification forward.",
        "Every turn must do two things: (1) respond to what was said, (2) ask the next missing field.",
        "If they asked a question — ANSWER IT, then smoothly return to qualification.",
        "SALES-DRIVEN: You are not a chatbot waiting for direction. You are a sales consultant guiding a conversation toward a booking.",
        "PACE: Speak at the caller's pace. One question at a time. Never stack questions.",
        f"{n} sentences max per response. Phone call — brevity is warmth.",
    ]
    if mirroring:
        lines.append("Mirror their energy: excited → warm and enthusiastic, cautious → softer and reassuring.")
    lines.append("NEVER sound like an IVR, chatbot, or scripted call center agent.")
    return lines


def style_block(agent_def: AgentDefinition) -> list[str]:
    """━━ NATURAL CONVERSATION STYLE (CRITICAL) ━━."""
    return [
        "",
        "━━ NATURAL CONVERSATION STYLE (CRITICAL) ━━",
        "",
        "▸ ACKNOWLEDGMENT — weave a brief natural acknowledgment INTO the flow of your response.",
        "  Do NOT bolt a standalone acknowledgment word onto the front of every sentence.",
        "  A filler phrase (e.g. 'One moment please...', 'Haanji!') may already have been",
        "  played to the caller before your response arrives. If so, skip any opening",
        "  acknowledgment and go straight to your answer.",
        "  When no filler was played, a single woven-in word is enough:",
        "    ✓ 'Right, so a 2BHK in Ahmedabad — let me see...'",
        "    ✓ 'Sure, we do cover Gujarat...'",
        "    ✓ 'Yeah, that's a good point actually...'",
        "    ✗ 'Got it! Absolutely! So...' — never stack acknowledgments.",
        "  Vary the word every turn. Never use the same one twice in a row.",
        "",
        "▸ DO NOT ECHO THE CALLER verbatim. Never say 'I understand you're looking for a 2BHK in Ahmedabad.'",
        "  Instead, absorb it and move naturally: 'Okay so a 2BHK in Ahmedabad — let me see...'",
        "  or 'Right, Ahmedabad — good choice actually.' Paraphrase lightly, then continue.",
        "",
        "▸ USE CONTRACTIONS always. Write as you'd speak:",
        "  'I'll' not 'I will', 'I'm' not 'I am', 'we've' not 'we have',",
        "  'it's' not 'it is', 'don't' not 'do not', 'can't' not 'cannot'.",
        "  BANNED phrases: 'I would be happy to assist', 'Thank you for sharing that information',",
        "  'I appreciate your patience', 'I understand your concern', 'As per your requirement'.",
        "",
        "▸ CONVERSATIONAL REPAIR — when correcting yourself or redirecting, sound human:",
        "  Use: 'Actually, let me rephrase that—', 'Sorry, what I mean is—',",
        "       'Hmm, let me put that differently—', 'Right, so actually—'",
        "  NOT: 'I apologize for the confusion, let me clarify', 'Let me correct myself'.",
    ]


def anti_repetition_block(agent_def: AgentDefinition) -> list[str]:
    """━━ ANTI-REPETITION (CRITICAL) ━━."""
    return [
        "",
        "━━ ANTI-REPETITION (CRITICAL) ━━",
        "Look at the conversation history. If you already asked something, DO NOT ask it again.",
        "If you already introduced yourself or the company, don't repeat that intro.",
        "If you already mentioned a project, don't introduce it as if for the first time.",
        "If the last 2-3 responses look similar, CHANGE your approach entirely.",
        "Never say the same phrase twice in a conversation.",
    ]


# ---------------------------------------------------------------------------
# Objection handling (v2 playbook — D3)
# ---------------------------------------------------------------------------

def objection_block(agent_def: AgentDefinition, lang: str = "en") -> list[str]:
    """━━ OBJECTION HANDLING ━━ — playbook prompt_guidance when present,
    else the entries' own scripted answers, else the built-in 4 lines
    verbatim (empty playbook only)."""
    playbook = getattr(agent_def.response_bank, "objection_playbook", None) or []
    proofs = "; ".join(getattr(agent_def.brand, "trust_proofs", []) or [])
    guided = [str(o.get("prompt_guidance") or "").strip()
              for o in playbook if str(o.get("prompt_guidance") or "").strip()]
    if guided:
        return ["", "━━ OBJECTION HANDLING ━━"] + [
            g.replace("{trust_proofs}", proofs) for g in guided
        ]
    # Playbook authored as id/patterns/answer (what the Copilot and the
    # document extractor emit): the user's scripted answers ARE the band.
    # Falling through to the builtin real-estate lines here is how the Kaapd
    # agent got "30 years, zero stalls, RERA" spoken about fabrics.
    composed: list[str] = []
    for o in playbook:
        if len(composed) >= 8:
            break
        if not isinstance(o, dict):
            continue
        answer = " ".join(_lang_pick(o.get("answer") or {}, lang).split())
        if not answer:
            continue
        if len(answer) > 240:
            answer = answer[:237].rstrip() + "..."
        pats = [str(p).strip() for p in (o.get("patterns") or [])
                if str(p).strip()]
        trigger = " / ".join(f"'{p}'" for p in pats[:2]) \
            or f"'{str(o.get('id') or 'objection').replace('_', ' ').strip()}'"
        composed.append(
            f"{trigger} → {answer}".replace("{trust_proofs}", proofs))
    if composed:
        return ["", "━━ OBJECTION HANDLING ━━"] + composed
    return [
        "",
        "━━ OBJECTION HANDLING ━━",
        "'Send brochure' → agree to send AND invite visit — photos tell only part of the story.",
        "'Price too high' → ask their comfortable range, suggest lower options.",
        "'Need to ask family' → invite the whole family to visit together.",
        "'Comparing builders' → smart — but see ours first. 30 years, zero stalls, RERA.",
    ]


# ---------------------------------------------------------------------------
# Guardrails / FIRM RULES (v2 — D4/D5, pricing policy, scheduling counts)
# ---------------------------------------------------------------------------

def _pricing_rule(agent_def: AgentDefinition, lang: str) -> str:
    """The pricing FIRM RULE, per knowledge_base.pricing_policy.
    Absent/on_request = today's exact string."""
    policy = getattr(agent_def.knowledge_base, "pricing_policy", None) or {}
    mode = str(policy.get("mode") or "on_request")
    if mode == "state_freely":
        return ("✓ You may state prices, payment plans and offers from the knowledge "
                "section freely whenever relevant")
    if mode == "defer":
        defer_line = _lang_pick(policy.get("defer_line") or {}, lang) \
            or "say the team will share exact pricing"
        return f"✗ NEVER give specific pricing — {defer_line}"
    return ("✗ NEVER volunteer pricing, rates, payment plans, ROI, inventory levels, "
            "or discounts unprompted — only discuss these when the caller explicitly asks")


def _fabrication_rule(agent_def: AgentDefinition,
                      default_rule: str = "✗ NEVER make up prices or dates "
                                          "not listed above") -> str:
    """honesty.forbidden_fabrications; default [prices, dates] = exact line.
    default_rule lets the generic pack widen the default to include products
    while per-agent forbidden_fabrications keep working for both packs."""
    honesty = getattr(agent_def.guardrails, "honesty", None) or {}
    items = honesty.get("forbidden_fabrications") or []
    if isinstance(items, str):
        items = [items]
    elif not isinstance(items, (list, tuple)):
        items = []  # a bool/other here must never crash the prompt build
    items = [str(x) for x in items if str(x).strip()]
    if not items or items == ["prices", "dates"]:
        return default_rule
    return f"✗ NEVER make up {' or '.join(items)} not listed above"


def firm_rules_block(agent_def: AgentDefinition, lang: str = "en") -> list[str]:
    """━━ FIRM RULES ━━ — builtin band (gated by guardrails.use_builtin_rules)
    + per-agent never/always rules + honesty/compliance/escalation sections."""
    gr = getattr(agent_def, "guardrails", None)
    use_builtin = getattr(gr, "use_builtin_rules", True) if gr else True
    pack = (str(getattr(gr, "builtin_rules_pack", "real_estate")
                or "real_estate") if gr else "real_estate")
    n = _offer_count(agent_def)
    lines: list[str] = ["", "━━ FIRM RULES ━━"]
    if use_builtin and pack == "generic":
        # Industry-neutral band: every domain-neutral hygiene rule from the
        # real_estate pack verbatim; the five real-estate lines replaced with
        # mission/catalogue/appointment equivalents. (A fabric agent carrying
        # "push toward a site visit" is how the Kaapd call went off-script.)
        phone_rule = (
            "✓ When you capture the caller's phone number, confirm it by reading it back "
            "ONCE, exactly as instructed in CONVERSATION STATE"
            if _has_phone_readback_slot(agent_def) else
            "✗ NEVER repeat the caller's phone number back to them unless they explicitly ask you to confirm it"
        )
        sched_rules = _sched_rules(agent_def, [
            f"✗ NEVER give more than {n} time options when offering an appointment or callback",
            f"✗ NEVER ask an open 'when suits you?' — always give exactly {_count_word(n)} specific time options",
        ], "✗ NEVER invent or state specific times, dates or slots — no calendar is "
           "configured; ask what day and time suits the caller, then confirm their proposal back")
        lines += [
            "✗ NEVER use hollow filler phrases: 'खुशी हुई', 'आपका जवाब सुनकर खुशी हुई', 'Happy to help', "
            "'It was a pleasure', 'आपकी बात सुनकर', 'ખુશી થઈ', 'Glad to hear'. "
            "Every sentence must either gather missing info, handle an objection, or move the call toward its CALL MISSION.",
            _pricing_rule(agent_def, lang),
            phone_rule,
            "✗ NEVER ask for information that is already marked ✓ in CONVERSATION STATE",
            *sched_rules[:1],
            "✗ NEVER dump the whole catalogue on one caller",
            _fabrication_rule(agent_def, default_rule="✗ NEVER make up "
                              "products, prices or dates not listed above"),
            *sched_rules[1:],
            "✗ NEVER ask 'Do you have any questions?' — only answer questions when the caller asks one",
            "✗ NEVER push the conversation to a new topic if the caller is still on the current one",
            "✗ NEVER launch into a detailed pitch before confirming it is a good moment to talk",
            "✗ NEVER interrupt a caller's train of thought with an unrelated topic",
            "✓ Use caller's name naturally — at most once every 4-5 turns. Do NOT open every response with their name.",
            "✓ No bullet points, lists, markdown, or emojis in your spoken responses",
            "✓ Only say things that are a direct, natural response to what the caller just said",
            "✓ Use contractions: I'll, I'm, we've, it's, don't, can't — always",
            "✓ Open each turn with a varied natural acknowledgment (Got it / Sure / Right / Absolutely — never twice in a row)",
        ]
    elif use_builtin:
        phone_rule = (
            "✓ When you capture the caller's phone number, confirm it by reading it back "
            "ONCE, exactly as instructed in CONVERSATION STATE"
            if _has_phone_readback_slot(agent_def) else
            "✗ NEVER repeat the caller's phone number back to them unless they explicitly ask you to confirm it"
        )
        sched_rules = _sched_rules(agent_def, [
            f"✗ NEVER give more than {n} time options for site visit",
            f"✗ NEVER say 'When can you come?' — always give exactly {_count_word(n)} specific time options",
        ], "✗ NEVER invent or state specific times, dates or slots for the site visit — no "
           "calendar is configured; ask what day and time suits the caller, then confirm their proposal back")
        lines += [
            "✗ NEVER use hollow filler phrases: 'खुशी हुई', 'आपका जवाब सुनकर खुशी हुई', 'Happy to help', "
            "'It was a pleasure', 'आपकी बात सुनकर', 'ખુશી થઈ', 'Glad to hear'. "
            "Every sentence must either gather missing info, handle an objection, recommend a project, or push toward a site visit.",
            _pricing_rule(agent_def, lang),
            phone_rule,
            "✗ NEVER ask for information that is already marked ✓ in CONVERSATION STATE",
            *sched_rules[:1],
            "✗ NEVER dump the whole portfolio on one caller",
            _fabrication_rule(agent_def),
            *sched_rules[1:],
            "✗ NEVER ask 'Do you have any questions?' or 'Any questions about the property?' — only answer questions when the caller asks one",
            "✗ NEVER push the conversation to a new topic if the caller is still on the current one",
            "✗ NEVER give property pitches until the caller has expressed genuine interest",
            "✗ NEVER interrupt a caller's train of thought with an unrelated topic",
            "✓ Use caller's name naturally — at most once every 4-5 turns. Do NOT open every response with their name.",
            "✓ No bullet points, lists, markdown, or emojis in your spoken responses",
            "✓ Only say things that are a direct, natural response to what the caller just said",
            "✓ Use contractions: I'll, I'm, we've, it's, don't, can't — always",
            "✓ Open each turn with a varied natural acknowledgment (Got it / Sure / Right / Absolutely — never twice in a row)",
        ]
    # Per-agent rules (D4) — rendered after the builtin band.
    never_rules = getattr(gr, "never_rules", []) if gr else []
    always_rules = getattr(gr, "always_rules", []) if gr else []
    for r in never_rules:
        r = str(r).strip()
        if r.lower().startswith("never "):
            r = r[6:]
        if r:
            lines.append(f"✗ NEVER {r}")
    for r in always_rules:
        r = str(r).strip()
        if r.lower().startswith("always "):
            r = r[7:]
        if r:
            lines.append(f"✓ {r}")
    # llm_config.extra_never_rules / extra_always_rules — same rendering as the
    # guardrail rules above. Parsed since D-era but injected NOWHERE until
    # 2026-07-28: agents carrying them (e.g. "keep replies to 1-2 sentences")
    # silently lost those rules.
    llm_cfg = getattr(agent_def, "llm_config", None)
    for r in (getattr(llm_cfg, "extra_never_rules", None) or []):
        r = str(r).strip()
        if r.lower().startswith("never "):
            r = r[6:]
        if r:
            lines.append(f"✗ NEVER {r}")
    for r in (getattr(llm_cfg, "extra_always_rules", None) or []):
        r = str(r).strip()
        if r.lower().startswith("always "):
            r = r[7:]
        if r:
            lines.append(f"✓ {r}")
    # Honesty: unknown-answer policy
    honesty = getattr(gr, "honesty", None) or {}
    unknown_policy = str(honesty.get("unknown_answer_policy") or "").strip()
    if unknown_policy:
        lines.append(f"✗ NEVER invent an answer — when you don't know, {unknown_policy}")
    # Compliance section — only when configured (L8)
    comp = getattr(gr, "compliance", None) or {}
    disclaimers = [str(x) for x in (comp.get("disclaimers") or []) if str(x).strip()]
    consent = str(comp.get("consent_line") or "").strip()
    restricted = [str(x) for x in (comp.get("restricted_topics") or []) if str(x).strip()]
    if disclaimers or consent or restricted:
        lines += ["", "━━ COMPLIANCE ━━"]
        for dline in disclaimers:
            lines.append(f"▸ Disclaimer to honor: {dline}")
        if consent:
            lines.append(f"▸ Consent line (deliver early in the call): {consent}")
        if restricted:
            lines.append("▸ NEVER discuss: " + ", ".join(restricted) + ".")
    # Escalation section — only when enabled (L8/D10)
    esc = getattr(gr, "escalation", None) or {}
    if esc.get("enabled"):
        lines += ["", "━━ ESCALATION ━━"]
        triggers = [str(x) for x in (esc.get("triggers") or []) if str(x).strip()]
        action = str(esc.get("action") or "offer_callback")
        if triggers:
            lines.append("▸ Escalate to a human when: " + "; ".join(triggers) + ".")
        else:
            lines.append("▸ Escalate to a human when the caller asks for one or is clearly upset.")
        lines.append(
            "▸ On escalation: " + (
                "offer to have a team member call them back, and capture the best time."
                if action == "offer_callback" else f"{action}."))
        handoff = _lang_pick(esc.get("handoff_message") or {}, lang)
        if handoff:
            lines.append(f"▸ Handoff message: '{handoff}'")
    return lines


def call_closure_block(agent_def: AgentDefinition, lang: str = "en") -> list[str]:
    """━━ ENDING THE CALL ━━ — unconditional platform doctrine, every agent.

    Two universal rules the business mandates on ALL agents, existing and
    future, so they sit outside guardrails.use_builtin_rules:
    1. permission-to-close: the agent never wraps up a call the caller hasn't
       agreed to end — it asks "anything else I can help with?" first. The
       platform hangs up automatically after the goodbye (deterministic
       close-confirm in ConversationRouter arms hangup_pending; the bridge
       tears the channel down after the farewell audio drains).
    2. callback pin-down: "call me later/in 10 minutes/tomorrow" must leave
       the call with a SPECIFIC agreed time — the platform schedules a real
       follow-up call from it, so a vague "sure, we'll call you" is a broken
       promise.
    """
    term = getattr(agent_def.call_flow, "termination", None) or {}
    llm_end = bool(term.get("llm_end_call", True))
    conds = [str(c).strip() for c in (term.get("end_conditions") or [])
             if str(c).strip()]

    permission_rule = (
        "✗ NEVER end or wrap up the call on your own — before any goodbye, "
        "ALWAYS ask the caller's permission first, e.g. 'Is there anything "
        "else I can help you with?', and only close after they confirm "
        "they're done")
    if conds:
        permission_rule += (" (exception: when one of the END-CALL RULES "
                            "below clearly applies, close directly)")

    goodbye_rule = (
        "✓ When the caller says goodbye or confirms they're done, reply with "
        "ONE warm goodbye line and nothing more — the platform ends the call "
        "after your goodbye, so never mention hanging up, disconnecting, or "
        "any technical step")
    if llm_end:
        goodbye_rule += (
            ", and append the exact token [END_CALL] at the very end of that "
            "goodbye — it is a silent signal that ends the call and is never "
            "spoken")

    lines = [
        "",
        "━━ ENDING THE CALL ━━",
        permission_rule,
        goodbye_rule,
        "✓ When the caller asks to be called later ('call me in 10 minutes', "
        "'call me tomorrow'), politely agree and pin down ONE specific time "
        "before letting them go — confirm it back in one short sentence "
        "('Perfect — I'll call you tomorrow at 4 PM.'); a real follow-up "
        "call is scheduled from what you agree, so never promise vaguely",
        "✓ When the caller says they're busy or can't talk right now, do NOT "
        "pitch or ask anything else — apologise for the timing, ask when to "
        "call back, confirm the time, and say goodbye in the same breath "
        "(a caller who asked you to call later has already agreed the call "
        "is over — skip the 'anything else?' question)",
        "✗ NEVER keep pushing after the caller has said they want to leave "
        "or has declined twice — accept gracefully, ask permission to close, "
        "and say goodbye",
    ]
    if llm_end:
        lines.append(
            "✗ NEVER write [END_CALL] on any turn that is not a goodbye — "
            "the call ends right after your words when you use it")
    if conds:
        lines.append("━━ END-CALL RULES (set by the business — follow exactly) ━━")
        lines.extend(f"▸ {c}" for c in conds)
        closing = ("✓ The moment one of these rules applies, deliver ONE "
                   "short polite closing line")
        if llm_end:
            closing += " ending with [END_CALL]"
        closing += (" — these rules take precedence over asking permission "
                    "to close")
        lines.append(closing)
    return lines
