"""Deterministic readings of caller turns that the model kept getting wrong
on the Pramukh campaign calls of 2026-10-22 / 2026-11-04 (RUNBOOK §85).

Pure functions + regexes, no engine state — `conversation.py` decides what
to do with each verdict. Everything here is anchored on phrasing that was
swept over the caller-line corpus before it shipped; re-run that sweep
(tests/test_call_recovery.py lists the pinned lines) before widening one.

Never `\\b` next to Indic text — `_NB` / `_NA` are the script-aware edges.
"""
from __future__ import annotations

import re

_IND = "ऀ-ॿ઀-૿"
_NB = rf"(?<![{_IND}])"
_NA = rf"(?![{_IND}])"
_NEG = r"(?:नहीं|नही|नहि|मत|ना" + _NA + r"|nahi+n?|nhi|mat)"


_LET = "\u0900-\u0963\u0966-\u097F\u0A80-\u0AFF"   # no daṇḍa


def _tokens(text: str) -> list[str]:
    return re.sub(rf"[^0-9a-z'{_LET}]+", " ", (text or "").lower()).split()


# ── 1. "Is this the wrong number, or the right one?" ──────────────────────
# The question is either/or, so a bare "हाँ" answers nothing — and the word
# that answers it, "सही", is the one STT mangles: call 1188 heard "सही है" as
# "भाई है", the verdict read two polite tokens as an acknowledgement of
# WRONG number and the agent cut the call on the right person.
_RIGHT_WORD_RE = re.compile(
    _NB + r"(?:सही|सहि|सई|साही|शाही|सहीह|सइ|सहीं|बराबर|सच्चा|करेक्ट|राइट(?!\s*नाउ)|राईट)" + _NA
    + r"|\b(?:sahi|sahih|sai|saahi|shahi|barabar|correct)\b"
    + r"|\bright\s*(?:number|no\b|one)|\b(?:it'?s|that'?s|is)\s*right\b(?!\s*now)"
    + r"|સાચો|સાચું|બરાબર",
    re.IGNORECASE)
_WRONG_WORD_RE = re.compile(
    _NB + r"(?:गलत|ग़लत|गल्त|गलद|ग़लती|गलती|रॉन्ग|रोंग|राँग|रॉंग)" + _NA
    + r"|\b(?:galat|galt|ghalat|wrong)\b|ખોટો|ખોટું",
    re.IGNORECASE)
# Whole-utterance mishearings of "सही है" (two tokens, nothing else said).
_SAHI_HAI_MISHEARD = frozenset({
    "भाई है", "भाई हैं", "भाई हे", "साई है", "सई है", "सही हे", "साही है",
    "शाही है", "सहाय है", "सहाई है", "bhai hai", "sai hai", "sahi he",
})


def reconfirm_hint(text: str) -> str:
    """'right' | 'wrong' | '' for the answer to the either/or question.

    "ग़लत नहीं है" / "सही नहीं है" flip; both words in one breath is no
    answer ("ग़लत है या सही — क्या मतलब?")."""
    t = (text or "").strip()
    if not t or "?" in t or len(_tokens(t)) > 6:
        return ""
    joined = " ".join(_tokens(t))
    if joined in _SAHI_HAI_MISHEARD:
        return "right"
    right = bool(_RIGHT_WORD_RE.search(t))
    wrong = bool(_WRONG_WORD_RE.search(t))
    if right == wrong:
        return ""
    negated = bool(re.search(
        r"(?:सही|गलत|ग़लत|sahi|galat|wrong|right|correct)\s*(?:नंबर\s*|number\s*)?"
        r"(?:नहीं|नही|nahi+n?|not)", t, re.IGNORECASE)
        or re.search(r"\bnot\s+(?:the\s+)?(?:right|wrong|correct)", t,
                     re.IGNORECASE))
    if negated:
        return "wrong" if right else "right"
    return "right" if right else "wrong"


# ── 2. "I never showed any interest" ──────────────────────────────────────
# Not a time objection: the caller disputes being on the list at all (call
# 1136 got "I won't take much of your time" for it).
_DENIES_INTEREST_RE = re.compile(
    r"(?:इंटरेस्ट|इन्ट्रेस्ट|इंट्रेस्ट|दिलचस्पी|रुचि|interest)[^\n।?]{0,14}"
    r"(?:नहीं|नही)\s*(?:दिखाया|दिखाई|दिखायी|दिखाए|लिया|ली|जताया|जताई)"
    r"|(?:नहीं|नही|कभी\s*नहीं)\s*(?:\S+\s*){0,2}?(?:इंटरेस्ट|इन्ट्रेस्ट|दिलचस्पी|interest)"
    r"\s*(?:दिखाया|दिखाई|दिखायी)"
    r"|(?:इंक्वायरी|इन्क्वायरी|एन्क्वायरी|enquiry|inquiry|पूछताछ|फॉर्म|form)"
    r"[^\n।?]{0,14}(?:नहीं|नही)\s*(?:की|किया|करी|भरा|भरी|डाली|डाला)"
    r"|\binterest\s*(?:nahi+n?|nhi)\s*dikha"
    r"|\b(?:enquiry|inquiry)\s*(?:nahi+n?|nhi)\s*(?:ki|kari|kiya)"
    r"|\b(?:i\s+)?(?:never|did\s*n[o']?t|have\s*n[o']?t|haven'?t)\s+"
    r"(?:show(?:n|ed)?|express(?:ed)?|enquir(?:e|ed)|inquir(?:e|ed)|"
    r"register(?:ed)?|fill(?:ed)?|ask(?:ed)?\s+(?:for|about))\b"
    r"|રસ\s*(?:નથી|નહોતો)\s*(?:બતાવ્યો|દાખવ્યો)|ઇન્ક્વાયરી\s*નથી\s*કરી",
    re.IGNORECASE)


# Word order is free in speech: "इंटरेस्ट दिखाया ही नहीं", "दिखाया ही नहीं है
# इंटरेस्ट कभी" (call 1221 — all three missed by the ordered pattern above).
# One clause holding an interest word, a show/enquire verb and a negation.
_INTEREST_WORD_RE = re.compile(
    r"इंटरेस्ट|इन्ट्रेस्ट|इंट्रेस्ट|इन्टरेस्ट|दिलचस्पी|रुचि|interest|"
    r"इंक्वायरी|इन्क्वायरी|एन्क्वायरी|enquiry|inquiry|पूछताछ", re.IGNORECASE)
_SHOW_VERB_RE = re.compile(
    r"दिखाया|दिखाई|दिखायी|दिखाए|जताया|जताई|dikha(?:ya|yi|i)|"
    r"show(?:n|ed)|express(?:ed)", re.IGNORECASE)
_NEG_SHOW_RE = re.compile(
    r"(?:दिखाया|दिखाई|दिखायी|दिखाए|जताया|जताई)\s*(?:ही\s*|भी\s*)?(?:नहीं|नही)"
    r"|(?:नहीं|नही)\s*(?:दिखाया|दिखाई|दिखायी|दिखाए|जताया|जताई)"
    r"|\bdikha(?:ya|yi|i)\s*(?:hi\s*)?(?:nahi+n?|nhi)\b"
    r"|\b(?:nahi+n?|nhi)\s*dikha", re.IGNORECASE)


def _denies_interest_free_order(t: str) -> bool:
    for clause in re.split(r"[।.!\n]", t):
        if len(clause) > 90 or not _INTEREST_WORD_RE.search(clause):
            continue
        if _NEG_SHOW_RE.search(clause):
            return True
    return False


def denies_interest(text: str) -> bool:
    t = text or ""
    if "?" in t:
        return False
    return bool(_DENIES_INTEREST_RE.search(t)) or _denies_interest_free_order(t)


# ── 2b. The answer to "shall I take your name off the list?" ──────────────
# Owner flow (2026-11-14): offer → confirm once more → close; a "no" at
# either step keeps the caller and the call continues.
_KEEP_CUE_RE = re.compile(
    r"(?:मत|ना|न|नहीं|नही)\s*(?:हटा|निकाल|काट)"
    r"|(?:हटा|निकाल)\S*\s*(?:मत|नहीं|नही)"
    r"|रहने\s*(?:दो|दीजिए|दीजिये|दें)|रखो|रखिए|रख\s*(?:लो|लीजिए)"
    r"|कंटिन्यू|जारी\s*रख|आगे\s*(?:बताइए|बताओ|बोलिए|बोलो|बढ़)"
    r"|\b(?:mat|na|nahi+n?)\s*(?:hata|nikal)|\brehne\s*d"
    r"|\b(?:do\s*n[o']?t|no\s+need\s+to)\s+remove|\bkeep\s+(?:it|me|my)"
    r"|\bcontinue\b|\bgo\s+(?:on|ahead)\b"
    r"|ન\s*(?:કાઢ|હટાવ)|રહેવા\s*દો",
    re.IGNORECASE)
_REMOVE_CUE_RE = re.compile(
    r"हटा\s*(?:दो|दीजिए|दीजिये|दें|देना|दे|दू|दूँ|दूं|ओ|इए|इये)"
    r"|निकाल\s*(?:दो|दीजिए|दीजिये|दें|देना)"
    r"|रिमूव|डिलीट|\bremove\b|\bdelete\b|\btake\s+(?:me|it|my\s+\w+)\s+off"
    r"|\b(?:hata|nikal)\s*(?:do|dijiye|dijie|den|dena)\b"
    r"|(?:कॉल|फोन|फ़ोन)\s*(?:मत|नहीं|नही|ना)\s*(?:कर|आ)"
    r"|(?:इंटरेस्ट|दिलचस्पी|रुचि)\S*\s*(?:ही\s*)?(?:नहीं|नही)"
    r"|\bnot\s+interested\b"
    r"|કાઢી\s*નાખ|હટાવી\s*દ",
    re.IGNORECASE)
_YES_LEAD_RE = re.compile(
    r"^\W*(?:हाँ|हां|हा|हाँजी|जी|जी\s*हाँ|ठीक|ओके|ओ\s*के|बिल्कुल|बिलकुल|ज़रूर|जरूर|"
    r"सही|कर\s*(?:दो|दीजिए|दीजिये|दें)|हम्म|"
    r"yes|yeah|yep|yup|ok|okay|sure|please|right|correct|fine|"
    r"haan?|ha|ji|theek|thik|bilkul|zaroor|kar\s*d\w+|"
    r"હા|હાં|જી|ઠીક|ચોક્કસ)(?![\w\u0900-\u097F\u0A80-\u0AFF])",
    re.IGNORECASE)
_NO_LEAD_RE = re.compile(
    r"^\W*(?:नहीं|नही|ना|न|मत|नो|no|nope|nah|nahi+n?|na|mat|ના|નહીં|નથી)"
    r"(?![\w\u0900-\u097F\u0A80-\u0AFF])",
    re.IGNORECASE)


def yes_no_lead(text: str) -> str:
    """'yes' | 'no' | '' from how a short reply BEGINS."""
    t = (text or "").strip()
    if not t or "?" in t:
        return ""
    if _NO_LEAD_RE.search(t):
        return "no"
    if _YES_LEAD_RE.search(t):
        return "yes"
    return ""


def removal_answer(text: str) -> str:
    """'yes' (remove me) | 'no' (keep me) | '' (neither) — the caller's
    reply to an offer to take them off the contact list. An explicit
    keep/remove phrase outranks a leading yes/no ("नहीं, हटा दो" is a yes),
    and repeating the complaint ("I never showed interest") is a yes."""
    t = (text or "").strip()
    if not t:
        return ""
    if _KEEP_CUE_RE.search(t):
        return "no"
    if _REMOVE_CUE_RE.search(t) or denies_interest(t):
        return "yes"
    if "?" in t:
        return ""
    if _YES_LEAD_RE.search(t):
        return "yes"
    if _NO_LEAD_RE.search(t):
        return "no"
    return ""


# ── 3. A refusal of ANY further call ──────────────────────────────────────
# "करना ही नहीं है कॉल" / "कॉल मत करना" with no time word beside it. The
# platform DND patterns need never/again/stop; these callers said neither
# and were asked "when should I call?" three more times (call 1137).
_TIME_WORD_RE = re.compile(
    r"अभी|आज|कल|परसों|बाद|शाम|सुबह|दोपहर|रात|बजे|मिनट|घंटे|घंटा|हफ्ते|हफ़्ते|"
    r"वीक|महीने|दिन" + _NA + r"|इस\s*समय|इस\s*टाइम|फिलहाल|फ़िलहाल"
    r"|\b(?:abhi|aaj|kal|baad|later|now|today|tomorrow|evening|morning|"
    r"week|month|minute|hour|filhal)\b",
    re.IGNORECASE)
_NO_CALL_RE = re.compile(
    r"(?:कॉल|फोन|फ़ोन|call)\s*(?:ही\s*)?(?:मत|ना" + _NA + r"|नहीं|नही)\s*"
    r"(?:कर(?:ना|ो|िए|िये|ें)?|कीजिए|कीजिये)"
    r"|(?:मत|नहीं|नही)\s*(?:कर(?:ना|ो|िए|िये|ें)|कीजिए)\s*(?:कॉल|फोन|फ़ोन|call)"
    r"|कर(?:ना|नी)\s*ही\s*(?:नहीं|नही)\s*(?:है\s*)?(?:कॉल|फोन|फ़ोन|बात|call)"
    r"|(?:कॉल|फोन|फ़ोन|बात|call)\s*कर(?:ना|नी)\s*ही\s*(?:नहीं|नही)"
    r"|बात\s*ही\s*(?:नहीं|नही)\s*कर(?:ना|नी)"
    r"|\bcall\s*(?:hi\s*)?(?:mat|nahi+n?)\s*kar"
    r"|(?<!we )(?<!i )(?<!they )(?<!you )\b(?:do\s*n[o']?t|never)\s+"
    r"(?:call|phone|ring)\b(?!\s*(?:it|them|him|her|that|or))",
    re.IGNORECASE)
_NEVER_ANSWER_RE = re.compile(
    r"^\W*(?:कभी\s*(?:भी\s*)?(?:नहीं|नही|मत)|never|kabhi\s*(?:bhi\s*)?(?:nahi+n?|mat))"
    r"\W*(?:कर(?:ना|ो|िए)?|कीजिए|कॉल|फोन|call)?\W*$",
    re.IGNORECASE)
_WHEN_ASK_RE = re.compile(
    r"कब|कितने\s*बजे|किस\s*दिन|कौन-?\s*सा\s*समय|available|बाद\s*में\s*कॉल"
    r"|\bwhen\b|\bwhat\s*time\b|\bcall\s*(?:you\s*)?(?:back|later)\b|ક્યારે",
    re.IGNORECASE)


def asks_when(agent_line: str) -> bool:
    """Does OUR line ask when the caller is available / should be called?"""
    return bool(_WHEN_ASK_RE.search(agent_line or ""))


def refuses_any_call(text: str, last_agent_line: str = "") -> bool:
    """The caller refuses to be called at all (not "not now")."""
    t = (text or "").strip()
    if not t or "?" in t:
        return False
    if _NEVER_ANSWER_RE.match(t) and _WHEN_ASK_RE.search(last_agent_line or ""):
        return True
    return bool(_NO_CALL_RE.search(t)) and not _TIME_WORD_RE.search(t)


# ── 4. A flat "no need / nothing" (not interested, no time flavour) ───────
_FLAT_REFUSAL_RE = re.compile(
    r"(?:कोई|कुछ)\s*(?:भी\s*)?(?:ज़रूरत|जरूरत|जरुरत|आवश्यकता)\s*(?:नहीं|नही)"
    r"|(?:ज़रूरत|जरूरत|जरुरत)\s*(?:ही\s*)?(?:नहीं|नही)\s*है"
    r"|कुछ\s*(?:भी\s*)?(?:नहीं|नही)\s*(?:कर(?:ना|नी)|चाहिए|लेना|खरीदना|ख़रीदना)"
    r"|^\W*(?:(?:नहीं|नही|ना|जी|सॉरी)\W+)*(?:मुझे\s*)?(?:कुछ\s*)?(?:भी\s*)?"
    r"(?:नहीं|नही)\s*चाहिए(?:\s*(?:कुछ|मुझे|जी|सर|भाई))*\W*$"
    r"|(?<!में )(?<!मे )(?<!में तो )(?<!में भी )"
    r"(?:इंटरेस्ट|इन्ट्रेस्ट|इंटरेस्टेड|दिलचस्पी|रुचि)\s*(?:ही\s*)?"
    r"(?:नहीं|नही(?!ं))(?!\s*(?:था|थी|थे|दिखा))"
    r"|\b(?:not\s*interested|no\s*need|nothing\s*needed)\b"
    r"|\b(?:koi|kuch)\s*(?:zaroorat|zarurat|jarurat)\s*nahi+n?"
    r"|\bkuch\s*nahi+n?\s*(?:karna|chahiye|lena)"
    r"|(?:કોઈ|કંઈ)\s*જરૂર\s*નથી|કંઈ\s*નથી\s*(?:કરવું|જોઈતું)|રસ\s*નથી",
    re.IGNORECASE)


def flat_refusal(text: str) -> bool:
    t = text or ""
    return "?" not in t and bool(_FLAT_REFUSAL_RE.search(t))


# ── 5. "Not a good time" — a no to the good-time question ─────────────────
_GOOD_TIME_ASK_RE = re.compile(
    r"बात\s*करना\s*ठीक|ठीक\s*रहेगा|ठीक\s*समय|सही\s*समय|दो\s*मिनट"
    r"|\b(?:okay|good|right|convenient)\s*time\b|\btwo\s*minutes\b|"
    r"\bquick\s*(?:chat|word)\b|વાત\s*કરવી\s*ઠીક|બે\s*મિનિટ",
    re.IGNORECASE)
_NOT_OK_RE = re.compile(
    r"ठीक\s*(?:ही\s*)?(?:नहीं|नही)|(?:नहीं|नही)\s*(?:ठीक|हो\s*पाएगा|हो\s*पायेगा)"
    r"|बात\s*(?:करना\s*)?(?:ही\s*)?(?:ठीक\s*)?(?:नहीं|नही)"
    r"|\bnot\s*(?:a\s*)?(?:good|okay|right|great)\b|\bbad\s*time\b"
    r"|ઠીક\s*નથી|અનુકૂળ\s*નથી",
    re.IGNORECASE)
_BUSY_RE = re.compile(
    _NB + r"(?:बिज़ी|बिजी|बीजी|व्यस्त|बिझी)" + _NA
    + r"|\bbusy\b|मीटिंग\s*में|गाड़ी\s*चला|ड्राइव\s*कर|ड्राइविंग|काम\s*में\s*हूँ"
    + r"|काम\s*(?:पर|में)\s*(?:हूँ|हूं|हु)|\b(?:in\s*a\s*meeting|driving|at\s*work)\b"
    + r"|વ્યસ્ત|બિઝી|મીટિંગમાં",
    re.IGNORECASE)
_NOT_BUSY_RE = re.compile(
    r"(?:बिज़ी|बिजी|व्यस्त|busy)\s*(?:नहीं|नही|nahi+n?|था|थी|थे)|\bnot\s*busy\b"
    r"|\b(?:was|were|are|is)\s*(?:all\s*)?(?:busy|driving)\b|representatives?"
    r"|फ्री\s*(?:ही\s*)?(?:हूँ|हूं|हु|रहता|रहती)|\bfree\s*(?:hi\s*)?(?:hu|hoon|hun)\b",
    re.IGNORECASE)


def says_busy(text: str) -> bool:
    """"I'm (a bit) busy" in any word order — "अभी मैं थोड़ा बिज़ी हूँ"."""
    t = text or ""
    return bool(_BUSY_RE.search(t)) and not _NOT_BUSY_RE.search(t)


def says_not_ok(text: str) -> bool:
    """"ठीक नहीं है" / "not a good time" on its own — for a context that
    already asked about timing."""
    t = text or ""
    return "?" not in t and bool(_NOT_OK_RE.search(t))


def not_a_good_time(text: str, last_agent_line: str) -> bool:
    """A no to "is now an okay time?" — busy, however it is worded."""
    t = (text or "").strip()
    if not t or "?" in t:
        return False
    if says_busy(t):
        return True
    if not _GOOD_TIME_ASK_RE.search(last_agent_line or ""):
        return False
    if _NOT_OK_RE.search(t):
        return True
    toks = _tokens(t)
    return bool(toks) and len(toks) <= 3 and all(
        k in {"नहीं", "नही", "ना", "no", "nahi", "nahin", "nope", "जी", "ji",
              "अभी", "abhi", "नो", "sorry", "सॉरी"} for k in toks) and any(
        k in {"नहीं", "नही", "ना", "no", "nahi", "nahin", "nope", "नो"}
        for k in toks)


# ── 6. "I'm free any time" is not a callback request ──────────────────────
_ANYTIME_RE = re.compile(
    r"कभी\s*भी\s*(?:कॉल|फोन|फ़ोन|बात|कर)|(?:ऑलवेज|हमेशा|always)\s*(?:फ्री|free)"
    r"|फ्री\s*ही\s*(?:हूँ|हूं|हु|रहता|रहती)|सुबह\s*से\s*शाम\s*तक"
    r"|\b(?:any\s*time|anytime)\b|\bkabhi\s*bhi\s*(?:call|phone|baat)"
    r"|ગમે\s*ત્યારે",
    re.IGNORECASE)


def available_anytime(text: str) -> bool:
    return bool(_ANYTIME_RE.search(text or ""))


# ── 7. "Why are you so slow / why the breaks?" ────────────────────────────
# A complaint about OUR delivery. "इतना टाइम नहीं रहता किसी के पास … इतना
# ब्रेक क्यों ले रहे हैं" carries "टाइम नहीं" and was answered as a busy
# objection (call 1193).
_SPEED_COMPLAINT_RE = re.compile(
    r"(?:स्लो|धीरे|धीमे|slow|slowly)\s*(?:\S+\s*){0,2}?(?:क्यों|kyu|kyon|why|बोल|bol)"
    r"|(?:इतना|इतनी|बहुत)\s*(?:स्लो|धीरे|धीमा|देर|लेट|late|slow|ब्रेक|break|gap|गैप)"
    r"|(?:ब्रेक|break|gap|गैप|pause|पॉज़|पॉज)\s*(?:\S+\s*){0,3}?"
    r"(?:क्यों|ले\s*रह|पड़\s*रह|नहीं\s*(?:पड़ना|करना|होना)|आ\s*रह)"
    r"|रुक\s*-?\s*रुक\s*(?:के|कर)|देर\s*से\s*(?:बोल|जवाब|reply)"
    r"|\bwhy\s*(?:are\s*you\s*)?(?:so\s*)?(?:slow|late|delayed)\b|\btoo\s*slow\b"
    r"|\b(?:long|so\s*many)\s*(?:pauses?|breaks?|gaps?|delays?)\b"
    r"|ધીમે\s*(?:કેમ|બોલ)|આટલું\s*ધીમું",
    re.IGNORECASE)


def speed_complaint(text: str) -> bool:
    return bool(_SPEED_COMPLAINT_RE.search(text or ""))


# ── 8. "Share your cost sheet / send me the details" ──────────────────────
_SHARE_ASSET_RE = re.compile(
    r"(?:कॉस्ट|कोस्ट|कोर्स|कास्ट|cost)\s*(?:शीट|sheet)|प्राइस\s*(?:लिस्ट|शीट)|"
    r"price\s*(?:list|sheet)|ब्रोशर|ब्रॉशर|ब्रोसर|brochure|डिटेल|details?|"
    r"फ्लोर\s*प्लान|floor\s*plan|लोकेशन|location|जानकारी|फोटो|photos?|पीडीएफ|pdf"
    r"|કોસ્ટ\s*શીટ|બ્રોશર|ડિટેલ|વિગત",
    re.IGNORECASE)
_SHARE_VERB_RE = re.compile(
    r"(?:शेयर|share)\s*(?:तो\s*)?(?:कर|कीजिए|करिए|करो|कर\s*द)|भेज|सेंड|send"
    r"|व्हाट्सएप|व्हाट्सऐप|वॉट्सऐप|वाट्सअप|व्हाट्सअप|whats\s*app|डाल\s*द"
    r"|\b(?:bhej|share\s*kar|send\s*kar|daal\s*d)\w*|મોકલ|શેર\s*કર",
    re.IGNORECASE)
_SHARE_PAST_RE = re.compile(
    r"भेज(?:ा|ी|े)\s*(?:था|थी|थे|है|हैं)|भेज\s*(?:दिया|दी(?!जि)|चुके)|मिल\s*(?:गया|गई|चुका)"
    r"|\b(?:already|got|received)\b", re.IGNORECASE)


def share_request(text: str) -> bool:
    """An imperative to send something over ("आप शेयर तो करिए अपना कोर्स
    शीट") — not a report that it was sent, not a question about it."""
    t = text or ""
    if _SHARE_PAST_RE.search(t):
        return False
    return bool(_SHARE_ASSET_RE.search(t)) and bool(_SHARE_VERB_RE.search(t))


_SHARE_QUESTION_RE = re.compile(
    r"[?？]|किस\s*चीज़|किस\s*चीज|क्या\s*(?:भेज|सेंड|शेयर)|कौन\s*स[ाीे]|कब\s*(?:भेज|तक)"
    r"|कैसे|\b(?:what|which|when|how|kya|kaun|kab|kaise)\b|શું|ક્યારે",
    re.IGNORECASE)


def bare_share_request(text: str) -> bool:
    """The request and nothing else — short, not a question about what will
    be sent. Call 1224: "शाम को समय ठीक रहेगा और ब्रोशर भी भेज देना" also
    answered the visit-time question, and "डिटेल्स किस चीज़ की भेजोगे?" is a
    question; both got the canned line and a stale question."""
    t = (text or "").strip()
    if not share_request(t) or _SHARE_QUESTION_RE.search(t):
        return False
    return len(t.split()) <= 9


# ── 9. Callback offsets beyond hours: days / weeks / months ───────────────
_NUM_WORDS = {
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "छह": 6,
    "छः": 6, "सात": 7, "दस": 10, "पंद्रह": 15,
    "ek": 1, "do": 2, "teen": 3, "tin": 3, "char": 4, "chaar": 4,
    "paanch": 5, "panch": 5, "das": 10, "dus": 10, "pandrah": 15,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "ten": 10, "fifteen": 15, "a": 1, "an": 1,
    "એક": 1, "બે": 2, "ત્રણ": 3, "ચાર": 4, "પાંચ": 5,
}
_NUM = r"(\d{1,2}|" + "|".join(sorted(_NUM_WORDS, key=len, reverse=True)) + r")"
_UNIT_DAY = r"(?:दिन|दिनों|days?|din|દિવસ)"
_UNIT_WEEK = r"(?:हफ्ते|हफ़्ते|हफ्ता|हफ़्ता|सप्ताह|वीक|weeks?|hafte|hafta|saptah|અઠવાડિય\S*)"
_UNIT_MONTH = r"(?:महीने|महीना|मंथ|months?|mahine|mahina|મહિન\S*)"
_LONG_OFFSET_RE = re.compile(
    r"(?<![0-9a-z" + _IND + r"])" + _NUM + r"\s*(?:-|se|से)?\s*(?:\d{1,2}\s*)?"
    r"(" + _UNIT_DAY + "|" + _UNIT_WEEK + "|" + _UNIT_MONTH + r")"
    r"(?![a-z" + _IND + r"])",
    re.IGNORECASE)
_NEXT_UNIT_RE = re.compile(
    r"(?:अगले|अगला|नेक्स्ट|next|agle|agla|આવતા)\s*(" + _UNIT_WEEK + "|"
    + _UNIT_MONTH + r")", re.IGNORECASE)


def long_offset_days(text: str, explicit_only: bool = False) -> int:
    """Days until the requested callback for "एक वीक के बाद", "2 din baad",
    "next week", "अगले महीने" — 0 when the phrase has none. `explicit_only`
    leaves "next week" (no count: which day?) to the day question."""
    t = text or ""
    m = _LONG_OFFSET_RE.search(t)
    if m:
        raw = m.group(1).lower()
        n = int(raw) if raw.isdigit() else _NUM_WORDS.get(raw, 0)
        unit = m.group(2).lower()
        if n:
            if re.fullmatch(_UNIT_DAY, unit, re.IGNORECASE):
                return min(n, 90)
            if re.fullmatch(_UNIT_WEEK, unit, re.IGNORECASE):
                return min(n * 7, 90)
            return min(n * 30, 180)
    m = None if explicit_only else _NEXT_UNIT_RE.search(t)
    if m:
        return 7 if re.fullmatch(_UNIT_WEEK, m.group(1), re.IGNORECASE) else 30
    return 0


def days_phrase(days: int, lang: str) -> str:
    """Spoken form: 7 → "एक हफ़्ते बाद" / "in a week"."""
    lang = (lang or "en")[:2]
    if days % 30 == 0 and days >= 30:
        n, unit = days // 30, "month"
    elif days % 7 == 0:
        n, unit = days // 7, "week"
    else:
        n, unit = days, "day"
    if lang == "hi":
        u = {"day": "दिन", "week": "हफ़्ते", "month": "महीने"}[unit]
        return f"{'एक' if n == 1 else n} {u} बाद"
    if lang == "gu":
        u = {"day": "દિવસ", "week": "અઠવાડિયા", "month": "મહિના"}[unit]
        return f"{'એક' if n == 1 else n} {u} પછી"
    return f"in {'a' if n == 1 else n} {unit}{'' if n == 1 else 's'}"


# ── 10. Does an AGENT line read as a goodbye? ─────────────────────────────
# A scripted stage's floor appended its next question to the model's own
# goodbye: "…मैं आपको कभी कॉल नहीं करूँगा। आपका दिन शुभ हो। May I know, आप
# Vapi में property self-use के लिए…" (call 1137; three more on 1136).
_AGENT_GOODBYE_RE = re.compile(
    r"दिन\s*शुभ|शुभ\s*दिन|अपना\s*ख़?याल\s*रख|फिर\s*मिलते|नमस्ते\s*[।!.]?\s*$"
    r"|(?:कभी|दोबारा|फिर)\s*(?:भी\s*)?कॉल\s*(?:नहीं|नही)\s*कर"
    r"|(?:नंबर|number)\s*(?:list|लिस्ट)\s*से\s*हटा"
    r"|(?:बाद\s*में|तब|तभी|कल|फिर)\s*(?:\S+\s*){0,3}?कॉल\s*कर(?:ता|ती|ूँगा|ूंगा|ूँगी|ूंगी|ेंगे)"
    r"|\bhave\s*a\s*(?:good|great|nice|lovely)\s*(?:day|evening|one)\b"
    r"|\btake\s*care\b|\bgood\s*bye\b|\bgoodbye\b"
    r"|\bwon'?t\s*call\s*(?:you\s*)?again\b|\bcall\s*you\s*(?:back\s*)?(?:later|then)\b"
    r"|દિવસ\s*(?:શુભ|સરસ)|સાચવજો|આવજો",
    re.IGNORECASE)


def agent_line_is_goodbye(said: str) -> bool:
    s = (said or "").strip()
    if not s or s.endswith("?"):
        return False
    return bool(_AGENT_GOODBYE_RE.search(s))


# ── 11. A leading acknowledgement on an authored question ────────────────
# Slot questions are authored with their acknowledgement ("Understood. आपका
# approximate budget…", "Sure Sir. आप specifically…"). Re-asked after a
# price answer the caller heard "…cost sheet share कर देता हूँ। Understood.
# आपका approximate budget…" (call 1131).
_LEAD_ACK_RE = re.compile(
    r"^\s*(?:understood|sure|certainly|perfect|great|okay|ok|alright|right|"
    r"got\s*it|noted|समझ\s*गय[ाी]|ठीक\s*है|जी|बिल्कुल|ज़रूर|अच्छा)"
    r"(?:\s+(?:sir|ma'?am|madam|सर|मैडम|जी))?\s*[.!,।—-]+\s*",
    re.IGNORECASE)


_LEAD_APOLOGY_RE = re.compile(
    r"^\s*(?:माफ़|माफ)\s*कीजिए(?:गा)?[^—\-?।]{0,60}?(?:सुनाई|आवाज़|आवाज|समझ)"
    r"[^—\-?।]{0,30}[—\-]+\s*"
    r"|^\s*sorry[^—\-?.]{0,60}?(?:hear|catch|miss)[^—\-?.]{0,30}[—\-]+\s*",
    re.IGNORECASE)


def strip_leading_apology(question: str) -> str:
    """"माफ़ कीजिए, ठीक से सुनाई नहीं दिया — आप Vapi से ही…?" → the question
    alone. A re-ask line said after "I can hear you" must not open by
    saying we could not."""
    q = question or ""
    out = _LEAD_APOLOGY_RE.sub("", q, count=1)
    return out if len(out) >= 10 else q


# "When did I say that?" — a denial, never a request.
_DENIAL_RE = re.compile(
    r"कब\s*(?:बोला|कहा|बोली|कही)|मैंने\s*(?:नहीं|नही)\s*(?:बोला|कहा)"
    r"|\bwhen\s+did\s+i\s+(?:say|ask|tell)|\bi\s+(?:never|did\s*n[o']?t)\s+"
    r"(?:say|ask|said|asked)|\bkab\s+(?:bola|kaha)",
    re.IGNORECASE)


def denies_having_said(text: str) -> bool:
    return bool(_DENIAL_RE.search(text or ""))


def strip_leading_ack(question: str) -> str:
    q = question or ""
    out = _LEAD_ACK_RE.sub("", q, count=1)
    return out if len(out) >= 10 else q


# ── 12. A money slot never holds a flat size ──────────────────────────────
_MONEY_CUE_RE = re.compile(
    r"\d|लाख|करोड़|करोड|हज़ार|हजार|lakh|lac|crore|cr\b|thousand|k\b|₹|rs|रुपय"
    r"|લાખ|કરોડ|budget\s*(?:नहीं|nahi)|कोई\s*budget\s*नहीं|open|flexible",
    re.IGNORECASE)
_SIZE_ONLY_RE = re.compile(
    r"^\W*(?:\d(?:\.\d)?|one|two|three|four|five|एक|दो|तीन|चार|पांच|पाँच)?\s*"
    r"(?:bhk|बीएचके|बी\s*एच\s*के|bedroom|rk)\W*$", re.IGNORECASE)


def plausible_money(value: str) -> bool:
    """False for a budget of "4 BHK" (call 1131's replay: the repeated size
    answer was stored as the budget and killed the budget-fit check)."""
    v = str(value or "").strip()
    if not v:
        return False
    if _SIZE_ONLY_RE.match(v):
        return False
    if re.search(r"bhk|बीएचके", v, re.IGNORECASE) and not re.search(
            r"लाख|करोड़|करोड|lakh|lac|crore|cr\b|₹|rs", v, re.IGNORECASE):
        return False
    return bool(_MONEY_CUE_RE.search(v))
