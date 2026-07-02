"""Redis connections + RQ queue factory.

Two Redis clients:
  * sync  (redis.Redis)          — required by RQ for enqueue/worker bookkeeping.
  * async (redis.asyncio.Redis)  — used by rate limiting and async progress cache.
Both are process-cached singletons.
"""

from __future__ import annotations

from functools import lru_cache

import redis as redis_sync
from redis.asyncio import Redis as AsyncRedis
from rq import Queue

from app.config.settings import get_settings


@lru_cache(maxsize=1)
def get_redis() -> redis_sync.Redis:
    """Synchronous Redis client (for RQ)."""
    return redis_sync.Redis.from_url(get_settings().redis_url)


@lru_cache(maxsize=1)
def get_async_redis() -> AsyncRedis:
    """Asyncio Redis client (rate limiting, progress cache)."""
    return AsyncRedis.from_url(get_settings().redis_url, decode_responses=True)


@lru_cache(maxsize=1)
def get_queue() -> Queue:
    settings = get_settings()
    return Queue(
        settings.queue_name,
        connection=get_redis(),
        default_timeout=settings.job_timeout_seconds,
    )
