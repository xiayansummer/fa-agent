from __future__ import annotations
import json
from collections.abc import AsyncGenerator
from redis_client import get_redis

CHANNEL_PREFIX = "agent:events:"


async def publish(thread_id: str, event: dict) -> None:
    redis = await get_redis()
    await redis.publish(f"{CHANNEL_PREFIX}{thread_id}", json.dumps(event))


async def subscribe(thread_id: str) -> AsyncGenerator[dict, None]:
    """Async generator yielding event dicts. Uses a dedicated connection for pub/sub."""
    import redis.asyncio as aioredis
    from config import settings

    conn = aioredis.from_url(settings.redis_url, decode_responses=True)
    pubsub = conn.pubsub()
    await pubsub.subscribe(f"{CHANNEL_PREFIX}{thread_id}")
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                event = json.loads(message["data"])
                yield event
                if event.get("type") in ("done", "error"):
                    break
    finally:
        await pubsub.unsubscribe(f"{CHANNEL_PREFIX}{thread_id}")
        await conn.aclose()
