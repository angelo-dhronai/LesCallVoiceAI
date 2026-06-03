"""Entrypoint — run the voice pipeline."""
from dotenv import load_dotenv
load_dotenv()

import asyncio
import logging
import os
import sys


def _validate_env() -> None:
    """Fail fast if required API keys are missing. Logs a masked key on success."""
    key = os.environ.get("SARVAM_API_KEY", "").strip()
    if not key:
        print(
            "FATAL: SARVAM_API_KEY is not set. "
            "Add it to calling-agent/.env and restart.",
            file=sys.stderr,
        )
        sys.exit(1)
    masked = key[:6] + "…" + key[-4:]
    print(f"[startup] SARVAM_API_KEY loaded: {masked}", flush=True)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    _validate_env()

    if "--test" in sys.argv:
        from app.voice.main_loop import _run_test
        asyncio.run(_run_test())
    elif "--legacy" in sys.argv:
        from app.voice.main_loop import _run
        asyncio.run(_run())
    else:
        from app.voice.pipeline import VoicePipeline
        from app.voice.conversation import ConversationRouter

        web_enabled = "--web" in sys.argv

        async def run() -> None:
            hub = None
            if web_enabled:
                from app.voice.event_hub import EventHub
                hub = EventHub()

            pipeline = VoicePipeline(
                conversation=ConversationRouter(),
                event_hub=hub,
                mic_enabled=not web_enabled,  # web UI gates mic; CLI mode keeps it on
            )

            if not web_enabled:
                await pipeline.run_until_shutdown()
                return

            import uvicorn
            from app.web.server import make_app

            app = make_app(hub, pipeline)
            config = uvicorn.Config(
                app,
                host="127.0.0.1",
                port=8000,
                log_level="warning",
                access_log=False,
            )
            server = uvicorn.Server(config)

            logging.getLogger(__name__).info(
                "[WEB] Console at http://127.0.0.1:8000 — mic starts OFF, tap to enable"
            )

            pipeline_task = asyncio.create_task(
                pipeline.run_until_shutdown(), name="pipeline"
            )
            server_task = asyncio.create_task(server.serve(), name="web")

            done, pending = await asyncio.wait(
                [pipeline_task, server_task], return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

        try:
            asyncio.run(run())
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
