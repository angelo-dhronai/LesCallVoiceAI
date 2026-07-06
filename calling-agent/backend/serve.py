"""
Agent Builder — standalone platform server.

Starts the web UI at http://127.0.0.1:8000 independently of the voice pipeline.
The pipeline can then be started, stopped, and switched from the Runtime Control page.

Usage:
    cd calling-agent/backend
    ..\venv\Scripts\python serve.py

The server stays up permanently. Use the Runtime Control page to manage the pipeline.
"""
from dotenv import load_dotenv
load_dotenv()

import asyncio
import logging
import sys
from pathlib import Path

_AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    _setup_logging()
    log = logging.getLogger(__name__)

    from app.agent_definition.registry import agent_registry
    agent_registry.start_watcher(_AGENTS_DIR)

    from app.voice.event_hub import EventHub
    hub = EventHub()

    from app.web.server import make_app
    app = make_app(hub, pipeline=None, standalone=True)

    import uvicorn
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=8000,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)

    log.info("[platform] Agent Builder UI → http://127.0.0.1:8000")
    log.info("[platform] Runtime Control → http://127.0.0.1:8000 (click Runtime Control in sidebar)")
    log.info("[platform] Press Ctrl+C to stop")

    try:
        asyncio.run(server.serve())
    except KeyboardInterrupt:
        pass
    finally:
        agent_registry.stop_watcher()
        log.info("[platform] Stopped")


if __name__ == "__main__":
    main()
