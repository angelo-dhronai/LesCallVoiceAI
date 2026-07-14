"""Test bootstrap — make `app.*` importable and load calling-agent/.env.

Run with the calling-agent venv:
    cd calling-agent/backend && ../venv/bin/python -m pytest tests/
"""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]      # calling-agent/backend
CALLING_AGENT_DIR = BACKEND_DIR.parent                 # calling-agent
REPO_ROOT = CALLING_AGENT_DIR.parent                   # lescall

for p in (str(BACKEND_DIR), str(CALLING_AGENT_DIR), str(REPO_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

AGENTS_DIR = CALLING_AGENT_DIR / "agents"
