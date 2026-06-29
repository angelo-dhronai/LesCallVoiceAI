"""CLI entry point for (re)building an agent's knowledge index.

Invoked by the dashboard as a subprocess in the calling-agent venv, so the heavy
embedding deps (sentence-transformers/torch) live only in that venv:

    python -m app.agent.ingest_cli <agent_id>

Prints a JSON summary line on stdout.
"""

from __future__ import annotations

import json
import sys

from . import knowledge


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(json.dumps({"ok": False, "error": "usage: ingest_cli <agent_id>"}))
        return 2
    agent_id = argv[1]
    try:
        summary = knowledge.build_index(agent_id)
        print(json.dumps({"ok": True, **summary}))
        return 0
    except Exception as exc:  # pragma: no cover
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
