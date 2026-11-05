#!/usr/bin/env python3
"""Numbers spoken in English on every Pramukh per-project agent (RUNBOOK §82).

Owner, 2026-10-15: "55 lakh is spoken out as paanch paanch lakh — I just want
the numbers spoken in English, fifty five lakhs; decimal → 'point'; nothing
for '-'; the language otherwise unchanged." Applies to every agent with the
`Pramukh <project>` naming convention across angelo@, arisha@ and
shubhangi@: the base families, their share-imported `-2` / `-3` copies and
the `_test` twins. The two legacy Pramukh Group agents (`pramukh`,
`pramukh_agent`) are NOT per-project agents and are listed, not touched.

It sets ONE leaf — `languages.tts_numbers_english: true` — through the store
(a revision per agent, rollback-safe). The engine reads it at TTS time
(`pipeline._spoken_numbers_text`); the builder's `hindi_only()` carries the
same flag so a rebuild keeps it.

    calling-agent/venv/bin/python deploy/set_pramukh_tts_numbers.py --dry-run
    docker exec -i lescall-api python - --apply < deploy/set_pramukh_tts_numbers.py

The engine change needs `docker restart lescall-runtime` (and lescall-api for
the Builder's schema) — the flag alone changes nothing until then.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path("/home/angelo/lescall")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "calling-agent"))

import yaml  # noqa: E402

from backend.app.agent_definition import store  # noqa: E402

LIVE_AGENTS = REPO / "calling-agent" / "agents"
# pramukh_<project>[-<n>]-<direction>, with the `_test` twins; never the
# legacy `pramukh` / `pramukh_agent` rows.
SLUG_RE = re.compile(r"^pramukh_[a-z][a-z_]*(?:-\d+)?-(?:inbound|outbound)$")
FLAG = "tts_numbers_english"
ACTOR = "tech@dhronai.com:pramukh-tts-numbers-2026-10-15"
REASON = ("Numbers spoken in English — fifty five lakh, two point one five "
          "crore (owner request 2026-10-15, RUNBOOK §82)")


def _owners() -> dict[str, tuple[str, bool]]:
    from shared.db.database import get_db
    with get_db() as db:
        rows = db.execute(
            "SELECT slug, owner, archived_at FROM agents WHERE slug LIKE ?",
            ("pramukh%",)).fetchall()
    return {r["slug"]: (r["owner"] or "", r["archived_at"] is not None)
            for r in rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only", default="",
                    help="comma-separated slugs (default: every match)")
    args = ap.parse_args()
    if not (args.dry_run or args.apply):
        ap.error("--dry-run or --apply")

    owners = _owners()
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    dirs = sorted(p.name for p in LIVE_AGENTS.iterdir() if p.is_dir())
    targets = [s for s in dirs if SLUG_RE.match(s)
               and (LIVE_AGENTS / s / "agent.yaml").exists()
               and (not only or s in only)]
    legacy = [s for s in dirs if s.startswith("pramukh") and s not in targets
              and not only]

    done = skipped = already = 0
    for slug in targets:
        owner, archived = owners.get(slug, ("", False))
        path = LIVE_AGENTS / slug / "agent.yaml"
        d = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        langs = d.get("languages")
        cur = bool(isinstance(langs, dict) and langs.get(FLAG))
        line = (f"{slug:38s} owner={owner or '-':24s} "
                f"rev={d.get('revision')!s:4s} {FLAG}={'on' if cur else 'off'}")
        if archived or not owner:
            print(line, "— no live owner row, skipped")
            skipped += 1
            continue
        if cur:
            print(line, "— already on")
            already += 1
            continue
        if not args.apply:
            print(line, "— would set on")
            continue
        d.setdefault("languages", {})[FLAG] = True
        res = store.write_agent(slug, d, actor=ACTOR, reason=REASON,
                                owner=owner, is_admin=True)
        try:
            from backend.app.agent_definition import registry
            registry.invalidate(slug)
        except Exception:
            pass
        print(line, f"— set on, revision {res['revision']}")
        done += 1

    print(f"\n{len(targets)} per-project agents: {done} written, "
          f"{already} already on, {skipped} skipped"
          + ("" if args.apply else " (dry run — nothing written)"))
    if legacy:
        print("not per-project, untouched:", ", ".join(legacy))


if __name__ == "__main__":
    main()
