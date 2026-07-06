"""
Agent registry — per-agent lazy cache with optional live-reload watcher.

Usage
-----
    from app.agent_definition.registry import agent_registry

    agent = agent_registry.get("pramukh")        # cached after first load
    agent_registry.invalidate("pramukh")          # drop cache entry
    agent_registry.start_watcher(agents_dir)      # begin FS watch
    agent_registry.stop_watcher()                 # clean up on shutdown
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

from .schema import AgentDefinition

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Thread-safe cache of AgentDefinition objects keyed by folder name."""

    def __init__(self) -> None:
        self._cache: dict[str, AgentDefinition] = {}
        self._lock = threading.Lock()
        self._observer: Optional[object] = None  # watchdog observer when running

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, name: str, agents_dir: Optional[Path] = None) -> AgentDefinition:
        """Return the cached AgentDefinition for *name*, loading it on first access.

        agents_dir defaults to the canonical agents/ folder beside calling-agent/.
        """
        with self._lock:
            if name not in self._cache:
                path = self._yaml_path(name, agents_dir)
                self._cache[name] = AgentDefinition.from_yaml(path)
                logger.info("[registry] loaded agent '%s' from %s", name, path)
            return self._cache[name]

    def put(self, name: str, agent: AgentDefinition) -> None:
        """Store (or replace) a pre-built AgentDefinition in the cache."""
        with self._lock:
            self._cache[name] = agent

    def invalidate(self, name: str) -> None:
        """Drop the cached entry for *name* so the next get() re-reads the YAML."""
        with self._lock:
            dropped = self._cache.pop(name, None)
        if dropped is not None:
            logger.info("[registry] invalidated cache for agent '%s'", name)

    def invalidate_all(self) -> None:
        with self._lock:
            self._cache.clear()
        logger.info("[registry] all agent caches invalidated")

    def known_names(self) -> list[str]:
        """Names currently in the cache (may not reflect all agents on disk)."""
        with self._lock:
            return list(self._cache.keys())

    # ------------------------------------------------------------------
    # Live-reload watcher (uses watchfiles, already in requirements.txt)
    # ------------------------------------------------------------------

    def start_watcher(self, agents_dir: Path) -> None:
        """Start a background thread that invalidates cache when agent.yaml files change."""
        try:
            from watchfiles import watch as wf_watch
        except ImportError:
            logger.warning("[registry] watchfiles not installed — live reload disabled")
            return

        stop_event = threading.Event()
        self._stop_event = stop_event
        registry = self

        def _run() -> None:
            try:
                for changes in wf_watch(str(agents_dir), stop_event=stop_event):
                    for _change_type, path in changes:
                        p = Path(path)
                        if p.name == "agent.yaml":
                            registry.invalidate(p.parent.name)
            except Exception as exc:
                logger.debug("[registry] watcher exited: %s", exc)

        t = threading.Thread(target=_run, daemon=True, name="agent-registry-watcher")
        t.start()
        self._observer = t
        logger.info("[registry] live-reload watcher started on %s", agents_dir)

    def stop_watcher(self) -> None:
        if hasattr(self, "_stop_event"):
            self._stop_event.set()
        self._observer = None
        logger.info("[registry] live-reload watcher stopped")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _yaml_path(name: str, agents_dir: Optional[Path]) -> Path:
        base = agents_dir or Path(__file__).resolve().parents[3] / "agents"
        path = base / name / "agent.yaml"
        if not path.exists():
            raise FileNotFoundError(f"No agent.yaml found for '{name}' at {path}")
        return path


# Module-level singleton used by default throughout the platform.
agent_registry = AgentRegistry()
