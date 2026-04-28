"""Redis sliding-window rate limiter for bounded backend side effects."""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass

from redis.asyncio import Redis


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after: int
    remaining: int


class RedisSlidingWindowRateLimiter:
    """Redis sorted-set sliding-window limiter.

    One key per actor. Each accepted request writes one timestamped member,
    trims old entries, and sets key TTL to the window. Tests can inject an
    alternate object exposing ``check(key, limit, window_seconds)``.
    """

    def __init__(self, redis: Redis | None = None) -> None:
        self._redis = redis

    def _client(self) -> Redis:
        if self._redis is not None:
            return self._redis
        host = os.environ.get("REDIS_HOST", "redis")
        port = int(os.environ.get("REDIS_PORT", "6379"))
        password = os.environ.get("REDIS_PASSWORD")
        self._redis = Redis(host=host, port=port, password=password, decode_responses=True)
        return self._redis

    async def check(
        self, key: str, *, limit: int, window_seconds: int
    ) -> RateLimitDecision:
        now_ms = int(time.time() * 1000)
        cutoff_ms = now_ms - (window_seconds * 1000)
        redis = self._client()

        async with redis.pipeline(transaction=True) as pipe:
            await pipe.zremrangebyscore(key, 0, cutoff_ms)
            await pipe.zcard(key)
            results = await pipe.execute()
        count = int(results[1])
        if count >= limit:
            oldest = await redis.zrange(key, 0, 0, withscores=True)
            if oldest:
                oldest_ms = int(oldest[0][1])
                retry_after = max(1, int((oldest_ms + window_seconds * 1000 - now_ms + 999) / 1000))
            else:
                retry_after = 1
            return RateLimitDecision(allowed=False, retry_after=retry_after, remaining=0)

        member = f"{now_ms}:{uuid.uuid4()}"
        async with redis.pipeline(transaction=True) as pipe:
            await pipe.zadd(key, {member: now_ms})
            await pipe.expire(key, window_seconds)
            await pipe.execute()
        return RateLimitDecision(
            allowed=True,
            retry_after=0,
            remaining=max(0, limit - count - 1),
        )
