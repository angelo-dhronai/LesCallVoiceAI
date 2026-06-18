"""Cross-process live-event bus over Redis pub/sub.

Why: in a multi-node deployment a call is handled by whichever media node took
it, but the dashboard runs elsewhere. Pipelines PUBLISH per-call events (stage
changes, transcript turns, status) to a Redis channel; the dashboard SUBSCRIBES
and streams them to the browser — so the live view sees every call regardless of
which node served it.

Degrades gracefully: if REDIS_URL is unset or Redis is unreachable, publish() is a
no-op and the dashboard falls back to local log tailing. The live call path is
never blocked by Redis being down.

Env: REDIS_URL (e.g. redis://127.0.0.1:6379/0). Channel: lescall:live
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger("events")

REDIS_URL = os.environ.get("REDIS_URL", "").strip()
CHANNEL = os.environ.get("LESCALL_EVENT_CHANNEL", "lescall:live")

_sync_client = None
_publish_disabled = not REDIS_URL


def _get_sync_client():
    global _sync_client, _publish_disabled
    if _publish_disabled:
        return None
    if _sync_client is None:
        try:
            import redis
            _sync_client = redis.from_url(REDIS_URL, socket_connect_timeout=1.0,
                                          socket_timeout=1.0)
        except Exception as e:
            logger.info("[EVENTS] redis unavailable (%s); publishing disabled", e)
            _publish_disabled = True
            return None
    return _sync_client


def publish(event: dict) -> None:
    """Fire-and-forget publish of one event. Never raises into the caller."""
    client = _get_sync_client()
    if client is None:
        return
    try:
        client.publish(CHANNEL, json.dumps(event, default=str))
    except Exception as e:  # broker hiccup must not affect a live call
        logger.debug("[EVENTS] publish failed: %s", e)


def publish_call_event(conn_id: str, kind: str, **fields) -> None:
    """Convenience: publish a structured per-call event."""
    publish({"conn_id": conn_id, "kind": kind, **fields})


async def subscribe():
    """Async generator yielding parsed events from the channel.

    Raises if REDIS_URL is unset / Redis unreachable — the caller (dashboard live
    endpoint) catches this and falls back to log tailing.
    """
    if not REDIS_URL:
        raise RuntimeError("REDIS_URL not set")
    import redis.asyncio as aioredis

    client = aioredis.from_url(REDIS_URL)
    pubsub = client.pubsub()
    await pubsub.subscribe(CHANNEL)
    try:
        async for msg in pubsub.listen():
            if msg.get("type") != "message":
                continue
            data = msg.get("data")
            if isinstance(data, (bytes, bytearray)):
                data = data.decode("utf-8", "replace")
            try:
                yield json.loads(data)
            except (ValueError, TypeError):
                yield {"raw": data}
    finally:
        await pubsub.unsubscribe(CHANNEL)
        await pubsub.aclose()
        await client.aclose()
