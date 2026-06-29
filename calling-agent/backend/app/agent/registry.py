"""Multi-agent registry.

Each agent has a persona (the overridable identity/brand/portfolio sections of
the system prompt) and its own knowledge base. One agent is marked active and is
used by the live pipeline. Stdlib-only so it imports cleanly in BOTH the
pipeline venv and the dashboard's Python — the two processes share the same
on-disk registry under ``AGENTS_ROOT``.

Backward compatibility: when the registry doesn't exist yet, callers fall back
to ``DEFAULT_PERSONA`` (the exact strings previously hardcoded in
conversation.py), so the single-agent behaviour is unchanged until someone
creates/edits agents from the dashboard.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone

# Repo layout: this file is calling-agent/backend/app/agent/registry.py
# AGENTS_ROOT → calling-agent/agents/
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
AGENTS_ROOT = os.environ.get(
    "AGENTS_ROOT", os.path.join(os.path.dirname(_BACKEND_DIR), "agents")
)
_REGISTRY_FILE = os.path.join(AGENTS_ROOT, "registry.json")

_LOCK = threading.Lock()

# ---------------------------------------------------------------------------
# DEFAULT_PERSONA — the seed for the migrated "Vishal Patel" agent. These are
# the SAME strings hardcoded in conversation.py:_build_system_prompt(). Keep in
# sync if those literals ever change. This is the single source of truth used
# when no registry exists yet (backward-compatible default).
# ---------------------------------------------------------------------------
DEFAULT_PERSONA: dict = {
    "agent_name": "Vishal Patel",
    "role_line": "a warm, genuine, experienced inbound sales agent for Pramukh Group.",
    "identity": [
        "You are NOT reading a script. You are a real person having a real conversation.",
        "Your job: make every caller feel genuinely heard and understood.",
    ],
    "brand": [
        "Pramukh Group · 30 years · 60+ completed projects · ZERO stalled projects · RERA compliant",
        "Surat · Vapi · Silvassa · Contact: +91 87583 70000",
    ],
    "portfolio": [
        "SURAT: Revanta (3BHK ₹1.11Cr+), Vivanta (3BHK ₹1.66Cr+), Satva (3BHK ₹1.26Cr+ NEAR-READY Dec 2025),",
        "Agastya (4BHK ultra-premium), Central Park (3+4BHK ₹1.3Cr+), Aranya (3+4BHK luxury near airport),",
        "Aranya II (3BHK ₹70L+ IT corridor).",
        "VAPI: Aura (2+3BHK ₹34-40L READY), Sahaj (1+2BHK ₹22-29L completed), Vedanta (RERA).",
        "SILVASSA: Sangath (2+3BHK), Yogi Woods (pool+gym), Shivanta (near schools), Swagat (ready).",
        "COMMERCIAL: Orbit Series (Surat/Vapi shops, offices).",
        "MATCH: Budget+Vapi→Sahaj/Aura | Mid+Surat→Aranya II | Premium+Surat→Revanta/Satva | Luxury→Vivanta/Agastya | Ready→Satva/Aura/Sahaj",
    ],
    "default_language": "hi",
    "kb_enabled": True,
}

# Persona keys an operator may edit from the dashboard.
PERSONA_FIELDS = ("agent_name", "role_line", "identity", "brand", "portfolio",
                  "default_language", "kb_enabled")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (name or "agent").lower()).strip("_")
    return slug or "agent"


def _atomic_write(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Per-agent paths
# ---------------------------------------------------------------------------

def agent_dir(agent_id: str) -> str:
    return os.path.join(AGENTS_ROOT, agent_id)


def persona_path(agent_id: str) -> str:
    return os.path.join(agent_dir(agent_id), "persona.json")


def knowledge_dir(agent_id: str) -> str:
    return os.path.join(agent_dir(agent_id), "knowledge")


def raw_dir(agent_id: str) -> str:
    return os.path.join(knowledge_dir(agent_id), "raw")


def docs_path(agent_id: str) -> str:
    return os.path.join(knowledge_dir(agent_id), "docs.json")


def index_dir(agent_id: str) -> str:
    return os.path.join(agent_dir(agent_id), "index")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def registry_exists() -> bool:
    return os.path.exists(_REGISTRY_FILE)


def load_registry() -> dict:
    if not os.path.exists(_REGISTRY_FILE):
        return {"active_agent_id": None, "agents": []}
    try:
        with open(_REGISTRY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"active_agent_id": None, "agents": []}


def _save_registry(reg: dict) -> None:
    _atomic_write(_REGISTRY_FILE, reg)


def _seed_dir_structure(agent_id: str) -> None:
    os.makedirs(raw_dir(agent_id), exist_ok=True)
    os.makedirs(index_dir(agent_id), exist_ok=True)
    if not os.path.exists(docs_path(agent_id)):
        _atomic_write(docs_path(agent_id), {"docs": []})


def ensure_default_agent() -> str:
    """Create the migrated default agent ('Vishal Patel') if the registry is
    empty, and return the active agent id. Idempotent."""
    with _LOCK:
        reg = load_registry()
        if reg.get("agents"):
            return reg.get("active_agent_id") or reg["agents"][0]["id"]
        agent_id = "vishal_default"
        reg = {
            "active_agent_id": agent_id,
            "agents": [{
                "id": agent_id,
                "name": "Vishal Patel (Pramukh Group)",
                "active": True,
                "default_language": DEFAULT_PERSONA["default_language"],
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
            }],
        }
        _seed_dir_structure(agent_id)
        persona = {"id": agent_id, "name": "Vishal Patel (Pramukh Group)", **DEFAULT_PERSONA}
        _atomic_write(persona_path(agent_id), persona)
        _save_registry(reg)
        return agent_id


def list_agents() -> list[dict]:
    return load_registry().get("agents", [])


def get_agent(agent_id: str) -> dict | None:
    return next((a for a in list_agents() if a["id"] == agent_id), None)


def get_active_agent_id() -> str | None:
    reg = load_registry()
    return reg.get("active_agent_id") or (reg["agents"][0]["id"] if reg.get("agents") else None)


def load_persona(agent_id: str | None) -> dict:
    """Load an agent's persona, falling back to DEFAULT_PERSONA for any missing
    keys (so partial personas still render a complete prompt)."""
    persona = dict(DEFAULT_PERSONA)
    if agent_id:
        try:
            with open(persona_path(agent_id), "r", encoding="utf-8") as f:
                stored = json.load(f)
            for k in PERSONA_FIELDS + ("id", "name"):
                if k in stored and stored[k] is not None:
                    persona[k] = stored[k]
        except Exception:
            pass
    return persona


def create_agent(name: str, persona_overrides: dict | None = None) -> dict:
    with _LOCK:
        reg = load_registry()
        base = _slugify(name)
        existing = {a["id"] for a in reg.get("agents", [])}
        agent_id = base
        n = 2
        while agent_id in existing:
            agent_id = f"{base}_{n}"
            n += 1
        _seed_dir_structure(agent_id)
        persona = {"id": agent_id, "name": name, **DEFAULT_PERSONA}
        for k, v in (persona_overrides or {}).items():
            if k in PERSONA_FIELDS:
                persona[k] = v
        _atomic_write(persona_path(agent_id), persona)
        entry = {
            "id": agent_id, "name": name, "active": False,
            "default_language": persona.get("default_language", "hi"),
            "created_at": _now_iso(), "updated_at": _now_iso(),
        }
        reg.setdefault("agents", []).append(entry)
        if not reg.get("active_agent_id"):
            reg["active_agent_id"] = agent_id
            entry["active"] = True
        _save_registry(reg)
        return entry


def update_persona(agent_id: str, fields: dict) -> dict | None:
    with _LOCK:
        agent = get_agent(agent_id)
        if not agent:
            return None
        persona = load_persona(agent_id)
        for k, v in fields.items():
            if k in PERSONA_FIELDS:
                persona[k] = v
        if "name" in fields:
            persona["name"] = fields["name"]
        persona["id"] = agent_id
        _atomic_write(persona_path(agent_id), persona)
        # Mirror name/language into the registry entry.
        reg = load_registry()
        for a in reg.get("agents", []):
            if a["id"] == agent_id:
                if "name" in fields:
                    a["name"] = fields["name"]
                if "default_language" in fields:
                    a["default_language"] = fields["default_language"]
                a["updated_at"] = _now_iso()
        _save_registry(reg)
        return persona


def set_active(agent_id: str) -> bool:
    with _LOCK:
        reg = load_registry()
        if not any(a["id"] == agent_id for a in reg.get("agents", [])):
            return False
        reg["active_agent_id"] = agent_id
        for a in reg["agents"]:
            a["active"] = (a["id"] == agent_id)
        _save_registry(reg)
        return True


def delete_agent(agent_id: str) -> tuple[bool, str]:
    with _LOCK:
        reg = load_registry()
        agents = reg.get("agents", [])
        if not any(a["id"] == agent_id for a in agents):
            return False, "not found"
        if len(agents) <= 1:
            return False, "cannot delete the only agent"
        if reg.get("active_agent_id") == agent_id:
            return False, "cannot delete the active agent — activate another first"
        reg["agents"] = [a for a in agents if a["id"] != agent_id]
        _save_registry(reg)
        # Best-effort remove the agent's files.
        try:
            import shutil
            shutil.rmtree(agent_dir(agent_id), ignore_errors=True)
        except Exception:
            pass
        return True, "deleted"
