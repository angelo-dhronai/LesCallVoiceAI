"""Phonetic matching of a caller's answer against the options of the
question just asked (RUNBOOK §85.12, call 1232).

STT under a forced language renders a short English answer as whatever Hindi
syllables sound nearest: "3 BHK" came back "पी वी एच के" and "पीडीएचके", and
the agent re-asked the same question three times. Text matching cannot see
that; sound can. Every option is expanded into its spoken forms (digits →
"three/तीन", capital letters → letter names, its synonyms), both sides are
reduced to one coarse phoneme alphabet (Devanagari, Gujarati and Latin alike)
and compared with a weighted edit distance in which a vowel is worth more
than a consonant — STT keeps the vowels and swaps the consonants.

`best_match` returns (value, tier): "accept" for a clear winner, "confirm"
for a leading candidate the agent should check with the caller, "" for
nothing. Pure functions, no engine state; the router decides WHEN to call
(only an unanswered option question, only after exact matching failed).
"""
from __future__ import annotations

import re
from functools import lru_cache

ACCEPT_SIM = 0.84
ACCEPT_MARGIN = 0.10
CONFIRM_SIM = 0.76
CONFIRM_MARGIN = 0.03
MIN_FORM_PHONEMES = 7

_VOWELS = set("aeiou")

# ── script → coarse phonemes ───────────────────────────────────────────────
_DEV_CONS = {
    "क": "k", "ख": "k", "ग": "g", "घ": "g", "ङ": "n",
    "च": "c", "छ": "c", "ज": "j", "झ": "j", "ञ": "n",
    "ट": "t", "ठ": "t", "ड": "d", "ढ": "d", "ण": "n",
    "त": "t", "थ": "t", "द": "d", "ध": "d", "न": "n",
    "प": "p", "फ": "f", "ब": "b", "भ": "b", "म": "m",
    "य": "y", "र": "r", "ल": "l", "व": "v", "ळ": "l",
    "श": "s", "ष": "s", "स": "s", "ह": "h",
}
_DEV_VOWEL = {"अ": "a", "आ": "a", "इ": "i", "ई": "i", "उ": "u", "ऊ": "u",
              "ए": "e", "ऐ": "e", "ओ": "o", "औ": "o", "ऑ": "o", "ऍ": "e",
              "ऋ": "ri"}
_DEV_SIGN = {"ा": "a", "ि": "i", "ी": "i", "ु": "u", "ू": "u", "े": "e",
             "ै": "e", "ो": "o", "ौ": "o", "ॉ": "o", "ॅ": "e", "ृ": "ri"}
_DEV_NASAL = {"ं", "ँ"}
_VIRAMA = "्"
_NUKTA = "़"
_GUJ_OFFSET = 0x0A80 - 0x0900      # Gujarati block mirrors Devanagari


def _to_devanagari(ch: str) -> str:
    o = ord(ch)
    if 0x0A80 <= o <= 0x0AFF:
        return chr(o - _GUJ_OFFSET)
    return ch


def _indic_word(word: str) -> str:
    out: list[str] = []
    chars = [_to_devanagari(c) for c in word]
    n = len(chars)
    i = 0
    while i < n:
        c = chars[i]
        nxt = chars[i + 1] if i + 1 < n else ""
        if c in _DEV_CONS:
            ph = _DEV_CONS[c]
            if nxt == _NUKTA:
                ph = {"ज": "z", "फ": "f", "ड": "r", "ढ": "r"}.get(c, ph)
                i += 1
                nxt = chars[i + 1] if i + 1 < n else ""
            out.append(ph)
            if nxt in _DEV_SIGN or nxt == _VIRAMA:
                pass
            elif i + 1 < n and (nxt in _DEV_CONS or nxt in _DEV_NASAL):
                out.append("a")           # inherent vowel, not word-final
        elif c in _DEV_SIGN:
            out.append(_DEV_SIGN[c])
        elif c in _DEV_VOWEL:
            out.append(_DEV_VOWEL[c])
        elif c in _DEV_NASAL:
            out.append("n")
        i += 1
    return "".join(out)


_LATIN_DIGRAPHS = (("tion", "sn"), ("ph", "f"), ("th", "t"), ("sh", "s"),
                   ("ch", "c"), ("kh", "k"), ("gh", "g"), ("bh", "b"),
                   ("dh", "d"), ("ck", "k"), ("ee", "i"), ("ea", "i"),
                   ("oo", "u"), ("ou", "o"), ("ai", "e"), ("ay", "e"),
                   ("au", "o"), ("aa", "a"), ("qu", "kv"))


def _latin_word(word: str) -> str:
    w = word.lower()
    for a, b in _LATIN_DIGRAPHS:
        w = w.replace(a, b)
    out: list[str] = []
    for i, c in enumerate(w):
        nxt = w[i + 1] if i + 1 < len(w) else ""
        if c == "c":
            out.append("s" if nxt in "eiy" else "k")
        elif c == "w":
            out.append("v")
        elif c == "q":
            out.append("k")
        elif c == "x":
            out.append("ks")
        elif c == "y":
            out.append("i" if i > 0 else "y")
        elif c == "e" and i == len(w) - 1 and len(w) > 3:
            continue                       # silent final e
        elif c.isalpha():
            out.append(c)
    s = "".join(out)
    return re.sub(r"(.)\1+", r"\1", s)     # doubled letters


# Spoken names — English letters and the numbers callers say in either tongue.
_LETTER = {"a": "e", "b": "bi", "c": "si", "d": "di", "e": "i", "f": "ef",
           "g": "ji", "h": "ec", "i": "ai", "j": "je", "k": "ke", "l": "el",
           "m": "em", "n": "en", "o": "o", "p": "pi", "q": "kyu", "r": "ar",
           "s": "es", "t": "ti", "u": "yu", "v": "vi", "w": "dablyu",
           "x": "eks", "y": "vai", "z": "zed"}
_NUM_EN = {"0": "ziro", "1": "van", "2": "tu", "3": "tri", "4": "for",
           "5": "faiv", "6": "siks", "7": "sevan", "8": "et", "9": "nain",
           "10": "ten", "1.5": "vanendahaf"}
_NUM_HI = {"0": "suny", "1": "ek", "2": "do", "3": "tin", "4": "car",
           "5": "panc", "6": "ce", "7": "sat", "8": "at", "9": "no",
           "10": "das", "1.5": "der"}

_TOKEN_RE = re.compile(r"\d+(?:\.\d+)?|[A-Za-z]+|[ऀ-ॿ઀-૿]+")


def _token_forms(tok: str) -> list[str]:
    """Phoneme renderings of one written token (several for a number)."""
    if tok[0].isdigit():
        forms = [f for f in (_NUM_EN.get(tok), _NUM_HI.get(tok)) if f]
        return forms or ["".join(_NUM_EN.get(d, "") for d in tok if d.isdigit())]
    if tok.isascii():
        _spelt = (tok.isupper() and len(tok) <= 4) or (
            len(tok) <= 5 and not any(c in "aeiouyAEIOUY" for c in tok))
        if _spelt:                          # BHK / bhk — said letter by letter
            return ["".join(_LETTER[c] for c in tok.lower())]
        return [_latin_word(tok)]
    return [_indic_word(tok)]


@lru_cache(maxsize=4096)
def spoken_forms(text: str) -> tuple[str, ...]:
    """Every phoneme string an option may be SAID as (≤ 8)."""
    toks = _TOKEN_RE.findall(text or "")
    forms = [""]
    for t in toks:
        alts = _token_forms(t)
        forms = [f + a for f in forms for a in alts][:8]
    return tuple(dict.fromkeys(f for f in forms if f))


def phonemes(text: str) -> list[str]:
    """One phoneme string per token of a transcript (first rendering)."""
    return [_token_forms(t)[0] for t in _TOKEN_RE.findall(text or "")]


# ── weighted edit distance ─────────────────────────────────────────────────
_CLASS = {}
for _grp in ("pbfvm", "td", "kg", "scjz", "nm", "rl", "yhv"):
    for _c in _grp:
        _CLASS.setdefault(_c, set()).update(_grp)


def _sub(a: str, b: str) -> float:
    if a == b:
        return 0.0
    av, bv = a in _VOWELS, b in _VOWELS
    if av and bv:
        return 0.9
    if av != bv:
        return 1.0
    return 0.3 if b in _CLASS.get(a, ()) else 0.6


def _indel(c: str) -> float:
    if c == "a":
        return 0.3
    if c in _VOWELS:
        return 0.6
    return 0.4 if c in "rhy" else 0.7


def distance(a: str, b: str) -> float:
    prev = [0.0]
    for ch in b:
        prev.append(prev[-1] + _indel(ch))
    for ca in a:
        cur = [prev[0] + _indel(ca)]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + _indel(ca), cur[j - 1] + _indel(cb),
                           prev[j - 1] + _sub(ca, cb)))
        prev = cur
    return prev[-1]


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return max(0.0, 1.0 - distance(a, b) / max(len(a), len(b)))


_COLLAPSE = str.maketrans("bfvmdgcjzlyh", "pppptksssrii")


def _overlap(a: str, b: str) -> float:
    """Cheap upper-bound filter: shared sound classes, order ignored."""
    ca, cb = a.translate(_COLLAPSE), b.translate(_COLLAPSE)
    n = 0
    pool = list(cb)
    for ch in ca:
        if ch in pool:
            pool.remove(ch)
            n += 1
    return n / max(len(a), len(b))


def _best_window(tokens: list[str], form: str, floor: float) -> float:
    best = floor
    n = len(tokens)
    for i in range(n):
        acc = ""
        for j in range(i, min(n, i + 6)):
            acc += tokens[j]
            if len(acc) > len(form) * 1.6 + 2:
                break
            if len(acc) * 1.6 + 2 < len(form):
                continue
            if _overlap(acc, form) < 0.55:
                continue
            s = similarity(acc, form)
            if s > best:
                best = s
    return best


def score_options(text: str, options: dict[str, list[str]]) -> list[tuple[float, str]]:
    """[(similarity, value)] best first. `options` maps each canonical value
    to the written forms it may be said as (itself + synonyms)."""
    toks = phonemes(text)
    out: list[tuple[float, str]] = []
    for value, written in options.items():
        forms = {f for w in written for f in spoken_forms(w)
                 if len(f) >= MIN_FORM_PHONEMES}
        best = 0.0
        for form in forms:
            best = _best_window(toks, form, best)
        out.append((best, value))
    out.sort(key=lambda x: -x[0])
    return out


def best_match(text: str, options: dict[str, list[str]]) -> tuple[str, str, float]:
    """(value, tier, similarity) — tier 'accept' | 'confirm' | ''."""
    if not text or len(options) < 2:
        return "", "", 0.0
    ranked = score_options(text, options)
    (s1, v1), (s2, _v2) = ranked[0], ranked[1]
    if s1 >= ACCEPT_SIM and s1 - s2 >= ACCEPT_MARGIN:
        return v1, "accept", s1
    if s1 >= CONFIRM_SIM and s1 - s2 >= CONFIRM_MARGIN:
        return v1, "confirm", s1
    return "", "", s1


# ── one word against a short list of names (cities) ────────────────────────
NAME_SIM = 0.80
# Everyday words one sound away from a city ("वही से" is "from there").
_NOT_A_NAME = frozenset({
    "वही", "वहीं", "वहाँ", "वहां", "यही", "यहीं", "यहाँ", "यहां", "भाई",
    "अभी", "बाकी", "काफी", "सही", "नहीं", "वापस", "vahi", "wahi", "yahi",
    "bhai", "abhi", "sahi", "happy", "हैप्पी", "sure", "श्योर"})
NAME_MARGIN = 0.08


def name_match(token: str, names: list[str]) -> str:
    """The name a single heard word most probably is ("भाभी" → "Vapi"), or
    "". Names are short, so the bar is one same-class consonant swap or two —
    and a clear lead over every other name."""
    if (token or "").strip().lower() in _NOT_A_NAME:
        return ""
    forms = _token_forms(token) if _TOKEN_RE.fullmatch(token or "") else []
    if not forms or len(forms[0]) < 3:
        return ""
    heard = forms[0]
    scored = []
    for n in names:
        best = max((similarity(heard, f) for f in spoken_forms(n)
                    if abs(len(f) - len(heard)) <= 1), default=0.0)
        scored.append((best, n))
    scored.sort(key=lambda x: -x[0])
    if not scored or scored[0][0] < NAME_SIM:
        return ""
    if len(scored) > 1 and scored[0][0] - scored[1][0] < NAME_MARGIN:
        return ""
    return scored[0][1]
