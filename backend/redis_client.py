from __future__ import annotations
import asyncio
import redis.asyncio as redis
from config import settings

_redis: redis.Redis | None = None
_lock = asyncio.Lock()

async def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        async with _lock:
            if _redis is None:
                _redis = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis
