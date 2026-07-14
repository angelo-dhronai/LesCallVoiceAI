"""
catalog — pure functions over knowledge_base.catalog / match_rules (D16).

An empty catalog means everything falls back to the legacy
portfolio_prompt_text verbatim — byte-identical to pre-v2 behaviour.
"""

from __future__ import annotations

import re
from typing import Any


def render_catalog_prompt(kb) -> str:
    """Knowledge paragraph for the system prompt.

    catalog empty → portfolio_prompt_text VERBATIM (today's behaviour).
    catalog present → one structured line per item + the portfolio text
    appended (extra colour the owner wrote is never lost).
    """
    catalog = getattr(kb, "catalog", None) or []
    if not catalog:
        return kb.portfolio_prompt_text
    lines: list[str] = []
    for item in catalog:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        bits: list[str] = []
        if item.get("location"):
            bits.append(f"in {item['location']}")
        if item.get("configurations"):
            cfgs = item["configurations"]
            bits.append("/".join(str(c) for c in cfgs) if isinstance(cfgs, list) else str(cfgs))
        if item.get("price_range"):
            bits.append(str(item["price_range"]))
        if item.get("availability"):
            bits.append(str(item["availability"]))
        if item.get("positioning_tag"):
            bits.append(f"({item['positioning_tag']})")
        line = f"▸ {name}" + (f" — {', '.join(bits)}" if bits else "")
        pitch = item.get("pitch_points")
        if isinstance(pitch, list) and pitch:
            line += ". " + " ".join(str(p).rstrip(".") + "." for p in pitch)
        facts = item.get("facts")
        if isinstance(facts, dict) and facts:
            line += " " + " ".join(f"{k}: {v}." for k, v in facts.items())
        lines.append(line)
    if kb.portfolio_prompt_text:
        lines.append("")
        lines.append(kb.portfolio_prompt_text)
    return "\n".join(lines)


def derive_known_projects(kb) -> dict[str, tuple[str, str]]:
    """known_projects extended with catalog names/aliases (extractor map).

    Existing explicit known_projects entries always win.
    """
    out: dict[str, tuple[str, str]] = {}
    for item in (getattr(kb, "catalog", None) or []):
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        location = str(item.get("location") or "").strip()
        keys = [name.lower()]
        aliases = item.get("aliases")
        if isinstance(aliases, list):
            keys += [str(a).strip().lower() for a in aliases if str(a).strip()]
        for k in keys:
            out.setdefault(k, (name, location))
    out.update(kb.known_projects or {})
    return out


_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)")


def _budget_to_lakhs(value: str) -> float | None:
    """'50 Lakhs' / '1.2 Crore' / '₹22-29L' → representative number in lakhs."""
    s = str(value or "").lower()
    nums = [float(n) for n in _NUM_RE.findall(s)]
    if not nums:
        return None
    n = max(nums)  # for ranges use the upper bound (most permissive match)
    if "cr" in s or "करोड" in s or "crore" in s:
        n *= 100
    return n


def _cond_matches(cond: str, actual: Any) -> bool:
    """Evaluate one match_rules condition against a slot value.

    Supports '<=40L' / '>=1Cr' style budget comparisons and
    case-insensitive equality for everything else.
    """
    if actual is None:
        return False
    cond_s = str(cond).strip()
    m = re.match(r"^(<=|>=|<|>)\s*(.+)$", cond_s)
    if m:
        op, rhs = m.group(1), m.group(2)
        left = _budget_to_lakhs(str(actual))
        right = _budget_to_lakhs(rhs)
        if left is None or right is None:
            return False
        return {"<=": left <= right, ">=": left >= right,
                "<": left < right, ">": left > right}[op]
    return str(actual).strip().lower() == cond_s.lower()


def evaluate_match_rules(kb, slots: dict[str, Any]) -> list[dict]:
    """Deterministic recommendation candidates for the current slot state.

    Returns [{recommend: [ids], reason: str}] for every rule whose `when`
    conditions ALL match a filled slot value. Empty rules/slots → [].
    """
    rules = getattr(kb, "match_rules", None) or []
    hits: list[dict] = []
    for rule in rules:
        when = rule.get("when")
        if not isinstance(when, dict) or not when:
            continue
        if all(_cond_matches(cond, slots.get(key)) for key, cond in when.items()):
            hits.append({
                "recommend": [str(r) for r in (rule.get("recommend") or [])],
                "reason": str(rule.get("reason") or ""),
            })
    return hits


def catalog_item_names(kb, ids: list[str]) -> list[str]:
    """Resolve catalog item ids to display names (id itself as fallback)."""
    by_id = {str(i.get("id") or "").lower(): str(i.get("name") or i.get("id"))
             for i in (getattr(kb, "catalog", None) or [])}
    return [by_id.get(str(x).lower(), str(x)) for x in ids]
