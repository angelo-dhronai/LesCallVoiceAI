"""angelo@dhronai.com's Ravi copy → Hindi only (RUNBOOK §70). The customer's
live Ravi (outbound_lead_qualifier-*) is NOT touched.
    docker exec -i lescall-api python - < deploy/update_ravi_copy_hindi_only.py
"""
import importlib.util, sys
sys.path.insert(0, "/home/angelo/lescall"); sys.path.insert(0, "/home/angelo/lescall/calling-agent")
from backend.app.agent_definition import store
spec = importlib.util.spec_from_file_location("bpp", "/home/angelo/lescall/deploy/build_pramukh_projects.py")
bpp = importlib.util.module_from_spec(spec); spec.loader.exec_module(bpp)
for slug in ("outbound_lead_qualifier-2-inbound", "outbound_lead_qualifier-2-outbound"):
    cur, _ = store.read_agent(slug)
    cur["languages"] = bpp.hindi_only(cur["languages"])
    for k in ("revision", "updated_at", "updated_by"):
        cur.pop(k, None)
    res = store.write_agent(slug, cur, actor="angelo@dhronai.com:hindi-only-2026-10-07",
                            reason="Hindi only (§70)", owner="angelo@dhronai.com", is_admin=True)
    print(slug, "revision", res["revision"])
