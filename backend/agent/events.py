from __future__ import annotations
import json
from collections.abc import AsyncGenerator
from redis_client import get_redis

CHANNEL_PREFIX = "agent:events:"
BUFFER_PREFIX = "agent:eventbuf:"
BUFFER_TTL = 600  # 10 min：足够前端 switchTab 后建立 WS


async def publish(thread_id: str, event: dict) -> None:
    """Publish via pub/sub AND append to a buffer list, so a WS that connects
    after run() has already finished can still replay everything."""
    redis = await get_redis()
    payload = json.dumps(event)
    buf_key = f"{BUFFER_PREFIX}{thread_id}"
    # 用 pipeline 保证 buffer+publish 原子追加
    pipe = redis.pipeline()
    pipe.rpush(buf_key, payload)
    pipe.expire(buf_key, BUFFER_TTL)
    pipe.publish(f"{CHANNEL_PREFIX}{thread_id}", payload)
    await pipe.execute()


async def subscribe(thread_id: str) -> AsyncGenerator[dict, None]:
    """Async generator yielding event dicts.
    Replays the buffered events first (history), then switches to pub/sub for
    future events. This avoids the race where workflow finishes before the WS
    subscriber connects."""
    import redis.asyncio as aioredis
    from config import settings

    redis_main = await get_redis()
    buf_key = f"{BUFFER_PREFIX}{thread_id}"

    # 1) 先回放已缓存的事件
    history = await redis_main.lrange(buf_key, 0, -1)
    replayed_count = len(history)
    history_terminated = False
    for raw in history:
        if raw is None:
            continue
        try:
            event = json.loads(raw)
        except Exception:
            continue
        yield event
        if event.get("type") in ("done", "error"):
            history_terminated = True
            break
    if history_terminated:
        return

    # 2) 订阅 pub/sub 继续接未来的事件；同时再次拉一遍 list 防止 race
    conn = aioredis.from_url(settings.redis_url, decode_responses=True)
    pubsub = conn.pubsub()
    await pubsub.subscribe(f"{CHANNEL_PREFIX}{thread_id}")
    try:
        # subscribe 之后再次 LRANGE，补回放期间新追加的事件
        post = await redis_main.lrange(buf_key, replayed_count, -1)
        for raw in post:
            try:
                event = json.loads(raw)
            except Exception:
                continue
            yield event
            if event.get("type") in ("done", "error"):
                return

        async for message in pubsub.listen():
            if message["type"] == "message":
                event = json.loads(message["data"])
                yield event
                if event.get("type") in ("done", "error"):
                    break
    finally:
        await pubsub.unsubscribe(f"{CHANNEL_PREFIX}{thread_id}")
        await conn.aclose()
