"""Swarit test — a byte-for-byte copy of the Pramukh Swarit twins (Akshay).

The owner asked (2026-10-14) for a second Swarit agent, "same name and
details, copy everything as it is", to carry the changes planned for the
Swarit pitch without touching the agent the main family runs on. This
script does ONLY the copy: `pramukh_swarit-{inbound,outbound}` →
`pramukh_swarit_test-{inbound,outbound}` on the same account
(angelo@dhronai.com), inactive, revision 1. The only field that differs is
`agent_id` (`pramukh_swarit_test-v1` — an identifier, never shared between
two agents); every spoken line, slot, stage, guard and the shared seven-
project knowledge base are the source's.

Run INSIDE lescall-api (the agents index + registry live there):
    docker exec -i lescall-api python - --apply < deploy/clone_pramukh_swarit_test.py
Dry run from the host venv (scratch dir, no DB writes):
    calling-agent/venv/bin/python deploy/clone_pramukh_swarit_test.py --dry-run
"""
from __future__ import annotations

import argparse
import copy
import shutil
import sys
from pathlib import Path

REPO = Path("/home/angelo/lescall")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "calling-agent"))

import yaml  # noqa: E402

from backend.app.agent_definition import store  # noqa: E402
from backend.app.agent_definition.schema import AgentDefinition  # noqa: E402

LIVE_AGENTS = REPO / "calling-agent" / "agents"
SRC = "pramukh_swarit-{direction}"
DST = "pramukh_swarit_test-{direction}"
DISPLAY = "Swarit test"
OWNER = "angelo@dhronai.com"
ACTOR = "angelo@dhronai.com:swarit-test-clone-2026-10-14"
REASON = "Swarit test twin: exact copy of pramukh_swarit (owner request 2026-10-14)"


def _register(slug: str) -> None:
    from shared.db.database import get_db
    with get_db() as db:
        # is_active=0: an active row becomes the account's fallback agent for
        # calls that match no DID binding — a test agent must never be that.
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

    agents_dir = (Path(__file__).resolve().parent / "agents_dry_swarit_test"
                  if args.dry_run else LIVE_AGENTS)
    if args.dry_run:
        shutil.rmtree(agents_dir, ignore_errors=True)
        agents_dir.mkdir()

    for direction in ("inbound", "outbound"):
        src_slug, dst_slug = SRC.format(direction=direction), DST.format(direction=direction)
        src = yaml.safe_load((LIVE_AGENTS / src_slug / "agent.yaml")
                             .read_text(encoding="utf-8"))
        assert src["identity"]["agent_name"] == "Akshay", src_slug
        assert src["knowledge_base"]["primary_project"]["id"] == "pramukh-swarit"
        new = copy.deepcopy(src)
        new["agent_id"] = "pramukh_swarit_test-v1"
        store.assert_direction_matches_slug(dst_slug, new)
        path = agents_dir / dst_slug / "agent.yaml"
        if path.exists():
            rev = store.write_agent(dst_slug, new, actor=ACTOR, reason=REASON,
                                    owner=OWNER if args.apply else "",
                                    is_admin=True, agents_dir=agents_dir)["revision"]
        else:
            new.update(revision=1, updated_at=store._now_iso(), updated_by=ACTOR)
            path.parent.mkdir(parents=True, exist_ok=True)
            AgentDefinition.from_dict(new).to_yaml(path)
            rev = 1
        if args.apply:
            _register(dst_slug)
            try:
                from backend.app.agent_definition import registry
                registry.invalidate(dst_slug)
            except Exception:
                pass
        # Prove the copy: reload both and diff every leaf except the
        # identifier and the write metadata.
        back = yaml.safe_load(path.read_text(encoding="utf-8"))
        skip = {"agent_id", "revision", "updated_at", "updated_by"}
        a = {k: v for k, v in src.items() if k not in skip}
        b = {k: v for k, v in back.items() if k not in skip}
        diff = [k for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]
        print(f"{dst_slug}: revision {rev}  (copy of {src_slug}; "
              f"differing top-level keys besides agent_id: {diff or 'none'})")


if __name__ == "__main__":
    main()
