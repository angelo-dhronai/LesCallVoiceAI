"""DB-backed agent configuration for the live pipeline.

Resolves WHICH agent persona a call should use and loads its fields from the
shared DB (agents / phone_numbers / campaigns). Selection precedence:

    explicit agent_id  >  campaign's agent_id (outbound)  >  DID's inbound_agent_id (inbound)  >  DEFAULT

Every path is best-effort: if the DB/shared package isn't importable (e.g. the
CLI `run.py` mode) or no matching agent exists, we return DEFAULT_AGENT, which
mirrors today's hardcoded Vishal Patel / Pramukh behaviour exactly. The live
pipeline therefore never breaks when the DB is absent.
"""
from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("agent_config")


@dataclass
class AgentConfig:
    id: int | None = None
    name: str = "Vishal Patel"
    company: str = "Pramukh Group"
    persona: str = ""
    system_prompt: str = ""
    pitch: str = ""
    language: str = "hi"          # default spoken language code (hi/gu/en or BCP-47)
    tts_speaker: str = "shubh"
    greeting: str = ""            # empty -> pipeline uses its built-in _OPENING_GREETING
    stages: str = ""              # JSON overrides for the stage machine (goals/conditions)

    @property
    def is_default(self) -> bool:
        return self.id is None


# Mirrors the legacy hardcoded identity so behaviour is unchanged when no DB agent.
DEFAULT_AGENT = AgentConfig()


def _short_lang(code: str | None) -> str:
    """Map a stored language ('hi-IN', 'auto', 'en') to the pipeline's short code."""
    if not code or code == "auto":
        return DEFAULT_AGENT.language
    return code.split("-")[0].lower()


def _ensure_repo_on_path() -> None:
    """Allow `import shared.db` even when started from calling-agent/backend."""
    # agent_config.py -> voice -> app -> backend -> calling-agent -> repo root
    repo_root = Path(__file__).resolve().parents[4]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))


def _row_to_config(row: dict) -> AgentConfig:
    return AgentConfig(
        id=row.get("id"),
        name=row.get("name") or DEFAULT_AGENT.name,
        company=row.get("company") or DEFAULT_AGENT.company,
        persona=row.get("persona") or "",
        system_prompt=row.get("system_prompt") or "",
        pitch=row.get("pitch") or "",
        language=_short_lang(row.get("language")),
        tts_speaker=row.get("tts_speaker") or DEFAULT_AGENT.tts_speaker,
        greeting=row.get("greeting") or "",
        stages=row.get("stages") or "",
    )


def load_agent(agent_id: int | None = None, did: str | None = None,
               campaign_id: int | None = None) -> AgentConfig:
    """Resolve and load the agent for a call. Never raises — returns DEFAULT on any issue."""
    if os.environ.get("DISABLE_DB_AGENTS") == "1":
        return DEFAULT_AGENT
    try:
        _ensure_repo_on_path()
        from shared.db.database import get_db  # noqa: WPS433 (lazy by design)

        with get_db() as db:
            resolved_id = agent_id
            if resolved_id is None and campaign_id is not None:
                r = db.execute("SELECT agent_id FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
                resolved_id = r["agent_id"] if r else None
            if resolved_id is None and did:
                digits = "".join(ch for ch in did if ch.isdigit())
                # Match stored numbers with or without leading + / country digits.
                r = db.execute(
                    "SELECT inbound_agent_id FROM phone_numbers "
                    "WHERE replace(replace(number,'+',''),' ','') LIKE ?",
                    (f"%{digits}",),
                ).fetchone()
                resolved_id = r["inbound_agent_id"] if r else None
            if resolved_id is None:
                return DEFAULT_AGENT

            row = db.execute("SELECT * FROM agents WHERE id=? AND is_active=1", (resolved_id,)).fetchone()
            if not row:
                return DEFAULT_AGENT
            cfg = _row_to_config(dict(row))
            logger.info("[AGENT] loaded id=%s name=%r (did=%s campaign=%s)",
                        cfg.id, cfg.name, did, campaign_id)
            return cfg
    except Exception as e:  # DB missing, shared not importable, etc.
        logger.info("[AGENT] DB agent load skipped (%s); using default", e)
        return DEFAULT_AGENT
