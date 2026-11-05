"""Ravi's Swarit price → the 2026-10-07 owner sheet (RUNBOOK §68).

4 BHK about ₹2.15 Cr; 5 BHK ₹2.35–3.70 Cr (was ₹2.50–3.50 Cr). Applied to
angelo@dhronai.com's Ravi copy (outbound_lead_qualifier-2-*) ONLY — the
customer's live Ravi (outbound_lead_qualifier-*) is not touched.
    docker exec -i lescall-api python - < deploy/update_ravi_swarit_price.py
"""
import json, sys
from pathlib import Path
sys.path.insert(0, "/home/angelo/lescall"); sys.path.insert(0, "/home/angelo/lescall/calling-agent")
import yaml
from backend.app.agent_definition import store

SLUGS = ["outbound_lead_qualifier-2-inbound", "outbound_lead_qualifier-2-outbound"]
OWNER = "angelo@dhronai.com"
SUBS = [  # most specific first
    ("Swarit runs from about two and a half to three and a half crore",
     "Swarit's 4 BHK is about two crore fifteen lakh and its 5 BHK runs from "
     "about two crore thirty-five lakh to three crore seventy lakh"),
    ("₹2.50 Cr – ₹3.50 Cr", "about ₹2.15 Cr (4 BHK); ₹2.35–3.70 Cr (5 BHK)"),
    ("₹2.50 Cr to ₹3.50 Cr depending on the bungalow",
     "about ₹2.15 Cr for the 4 BHK and ₹2.35–3.70 Cr for the 5 BHK, depending on the bungalow"),
    ("From ₹2.50 Cr to ₹3.50 Cr depending on the bungalow",
     "About ₹2.15 Cr for a 4 BHK and ₹2.35–3.70 Cr for a 5 BHK, depending on the bungalow"),
    ("the ₹2.50–3.50 Cr range", "the ₹2.15–3.70 Cr range"),
    ("Swarit starts at about two and a half crore",
     "Swarit starts at about two crore fifteen lakh"),
    ("two and a half to three and a half crore and what that buys",
     "about two crore fifteen lakh for a 4 BHK, two crore thirty-five lakh to "
     "three crore seventy lakh for a 5 BHK, and what that buys"),
    ("two and a half to three and a half crore",
     "about two crore fifteen lakh for the 4 BHK and two crore thirty-five "
     "lakh to three crore seventy lakh for the 5 BHK"),
]


def walk(o):
    if isinstance(o, str):
        for a, b in SUBS:
            o = o.replace(a, b)
        return o
    if isinstance(o, list):
        return [walk(x) for x in o]
    if isinstance(o, dict):
        return {k: walk(v) for k, v in o.items()}
    return o


for slug in SLUGS:
    cur, _ = store.read_agent(slug)
    new = walk(cur)
    blob = json.dumps(new, ensure_ascii=False)
    for bad in ("2.50", "3.50", "two and a half", "three and a half"):
        assert bad not in blob, (slug, bad)
    for k in ("revision", "updated_at", "updated_by"):
        new.pop(k, None)
    res = store.write_agent(slug, new, actor="angelo@dhronai.com:ravi-price-2026-10-07",
                            reason="Swarit price → 2026-10-07 sheet (§68)",
                            owner=OWNER, is_admin=True)
    print(slug, "revision", res["revision"], "changed", len(res["changed_paths"]))
