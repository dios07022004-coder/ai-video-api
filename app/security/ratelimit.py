"""Distributed rate limiting (fixed-window counter in Redis).

Keyed per partner (falls back to client IP for unauthenticated routes). Uses an
atomic INCR + EXPIRE so it works correctly across multiple API replicas. If Redis
is unavailable the limiter fails **open** (never blocks legitimate traffic on
infra failure) but logs the degradation.
"""

from __future__ import annotations

import time

from redis.asyncio import Redis

from app.logging import get_logger

logger = get_logger("security.ratelimit")


class RateLimiter:
    def __init__(self, redis: Redis, *, limit_per_minute: int) -> None:
        self._redis = redis
        self._limit = limit_per_minute

    async def check(self, identity: str) -> tuple[bool, int]:
        """Return (allowed, remaining). Fails open on Redis errors."""
        if self._limit <= 0:
            return True, -1
        window = int(time.time() // 60)
        key = f"rl:{identity}:{window}"
        try:
            pipe = self._redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, 70)
            count, _ = await pipe.execute()
        except Exception as exc:  # noqa: BLE001 — infra failure must not block
            logger.warning("ratelimit_degraded", error=str(exc))
            return True, -1
        remaining = max(0, self._limit - int(count))
        return int(count) <= self._limit, remaining
