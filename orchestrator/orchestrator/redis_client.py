"""Redis session registry wrapper (D013).

Stores per-session activity state — `session_id → {container_id, tmux_session,
user_id, team_id, last_activity}` — in Redis. Per D013 there is no in-memory
fallback: an unreachable Redis surfaces as `RedisUnavailable`, which the
FastAPI exception handler maps to 503. The reasoning is in DECISIONS.md
D013: a fallback that lies about persistence (session marked alive in memory
but missing from Redis on the next orchestrator restart) defeats the
tmux-durability guarantee S01 ships.

Key shape:
  - `session:{session_id}` → JSON blob (the session record)
  - `user_sessions:{user_id}:{team_id}` → SET of session_ids (for list ops)

Why a JSON blob and not a Redis hash: hash fields would force every read to
deserialize per-field, and the registry is always read whole-record. JSON
blob keeps reads atomic (one GET) and writes atomic (one SET) without
multi-key transactions.

Last-activity is stored as float epoch seconds (UTC). The reaper (S04)
compares against `time.time()` directly.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import redis.asyncio as redis_async
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from orchestrator.config import settings
from orchestrator.errors import RedisUnavailable

logger = logging.getLogger("orchestrator")


def _session_key(session_id: str) -> str:
    return f"session:{session_id}"


def _user_sessions_key(user_id: str, team_id: str) -> str:
    return f"user_sessions:{user_id}:{team_id}"


class RedisSessionRegistry:
    """Async wrapper around `redis.asyncio` for the orchestrator session map.

    One instance per process. The underlying connection pool handles
    multiplexing across concurrent requests. `close()` is called from the
    lifespan shutdown.
    """

    def __init__(self, client: redis_async.Redis | None = None) -> None:
        if client is not None:
            self._client = client
        else:
            self._client = redis_async.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                password=settings.redis_password or None,
                decode_responses=True,
                socket_connect_timeout=2.0,
                socket_timeout=5.0,
            )

    async def close(self) -> None:
        await self._client.aclose()

    async def ping(self) -> bool:
        """Liveness check used by health endpoint (T04+)."""
        try:
            return bool(await self._client.ping())
        except (RedisConnectionError, RedisTimeoutError, OSError) as exc:
            logger.warning("redis_unreachable op=ping reason=%s", type(exc).__name__)
            return False

    async def set_session(self, session_id: str, data: dict[str, Any]) -> None:
        """Create or replace a session record.

        Always stamps `last_activity` to now. Adds the session_id to the
        user_sessions index for list_sessions().
        """
        payload = dict(data)
        payload["last_activity"] = time.time()
        user_id = payload.get("user_id")
        team_id = payload.get("team_id")
        if not user_id or not team_id:
            raise ValueError("session record must include user_id and team_id")
        try:
            async with self._client.pipeline(transaction=True) as pipe:
                pipe.set(_session_key(session_id), json.dumps(payload))
                pipe.sadd(_user_sessions_key(user_id, team_id), session_id)
                await pipe.execute()
        except (RedisConnectionError, RedisTimeoutError, OSError) as exc:
            logger.warning("redis_unreachable op=set_session reason=%s", type(exc).__name__)
            raise RedisUnavailable("redis unreachable on set_session") from exc
        except RedisError as exc:
            logger.warning("redis_error op=set_session reason=%s", type(exc).__name__)
            raise RedisUnavailable("redis error on set_session") from exc

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        try:
            raw = await self._client.get(_session_key(session_id))
        except (RedisConnectionError, RedisTimeoutError, OSError) as exc:
            logger.warning("redis_unreachable op=get_session reason=%s", type(exc).__name__)
            raise RedisUnavailable("redis unreachable on get_session") from exc
        except RedisError as exc:
            logger.warning("redis_error op=get_session reason=%s", type(exc).__name__)
            raise RedisUnavailable("redis error on get_session") from exc
        if raw is None:
            return None
        return json.loads(raw)

    async def update_last_activity(self, session_id: str) -> None:
        """Heartbeat called by the WS bridge on every input frame (T04).

        Returns silently if the session record has been deleted out from
        under us (race with DELETE) — the next read will see None and the
        caller can decide what to do.
        """
        try:
            raw = await self._client.get(_session_key(session_id))
            if raw is None:
                return
            data = json.loads(raw)
            data["last_activity"] = time.time()
            await self._client.set(_session_key(session_id), json.dumps(data))
        except (RedisConnectionError, RedisTimeoutError, OSError) as exc:
            logger.warning(
                "redis_unreachable op=update_last_activity reason=%s",
                type(exc).__name__,
            )
            raise RedisUnavailable("redis unreachable on update_last_activity") from exc
        except RedisError as exc:
            logger.warning(
                "redis_error op=update_last_activity reason=%s", type(exc).__name__
            )
            raise RedisUnavailable("redis error on update_last_activity") from exc

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session record. Returns True if it existed.

        Removes the session_id from the user_sessions index. We re-fetch the
        record first to know which user_sessions set to scrub — the alternative
        (SREM against every possible set) would require knowing the user/team
        ahead of time, which the caller may not have.
        """
        try:
            raw = await self._client.get(_session_key(session_id))
            if raw is None:
                return False
            data = json.loads(raw)
            async with self._client.pipeline(transaction=True) as pipe:
                pipe.delete(_session_key(session_id))
                pipe.srem(
                    _user_sessions_key(data["user_id"], data["team_id"]),
                    session_id,
                )
                await pipe.execute()
            return True
        except (RedisConnectionError, RedisTimeoutError, OSError) as exc:
            logger.warning(
                "redis_unreachable op=delete_session reason=%s", type(exc).__name__
            )
            raise RedisUnavailable("redis unreachable on delete_session") from exc
        except RedisError as exc:
            logger.warning("redis_error op=delete_session reason=%s", type(exc).__name__)
            raise RedisUnavailable("redis error on delete_session") from exc

    async def scan_session_keys(
        self, *, count_hint: int = 100
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Yield `(session_id, record)` for every `session:*` key in Redis.

        Uses `SCAN MATCH session:*` (cursor-based, non-blocking) rather
        than `KEYS session:*` because `KEYS` is O(N) and blocks the
        single-threaded redis loop — production-hostile when the session
        count grows. The reaper (S04) is the canonical consumer.

        `count_hint` is forwarded to SCAN's COUNT argument as a per-batch
        size hint; redis treats it as guidance only. 100 keeps each batch
        small enough to stay under the 5s socket_timeout.

        Stale-id handling: if a session_key surfaces in the SCAN cursor
        but the value is missing by the time we GET it (raced with
        delete_session), we silently skip the entry — the cursor is a
        snapshot hint, not a transactional view.
        """
        try:
            async for key in self._client.scan_iter(
                match="session:*", count=count_hint
            ):
                # decode_responses=True on the client returns str, but a
                # future bytes-mode swap would break this — guard.
                key_str = key.decode("utf-8") if isinstance(key, bytes) else key
                if not key_str.startswith("session:"):
                    continue
                session_id = key_str[len("session:"):]
                raw = await self._client.get(key_str)
                if raw is None:
                    # Raced with a delete; nothing to yield.
                    continue
                try:
                    record = json.loads(raw)
                except (TypeError, ValueError):
                    logger.warning(
                        "redis_record_corrupt session_id=%s reason=json_decode",
                        session_id,
                    )
                    continue
                yield session_id, record
        except (RedisConnectionError, RedisTimeoutError, OSError) as exc:
            logger.warning(
                "redis_unreachable op=scan_session_keys reason=%s",
                type(exc).__name__,
            )
            raise RedisUnavailable(
                "redis unreachable on scan_session_keys"
            ) from exc
        except RedisError as exc:
            logger.warning(
                "redis_error op=scan_session_keys reason=%s", type(exc).__name__
            )
            raise RedisUnavailable("redis error on scan_session_keys") from exc

    async def list_sessions(self, user_id: str, team_id: str) -> list[dict[str, Any]]:
        """Return all session records owned by (user_id, team_id).

        Ignores stale ids in the index whose JSON blob has been deleted —
        the index is a hint, not a source of truth.
        """
        try:
            ids = await self._client.smembers(_user_sessions_key(user_id, team_id))
            if not ids:
                return []
            keys = [_session_key(sid) for sid in ids]
            raws = await self._client.mget(keys)
        except (RedisConnectionError, RedisTimeoutError, OSError) as exc:
            logger.warning(
                "redis_unreachable op=list_sessions reason=%s", type(exc).__name__
            )
            raise RedisUnavailable("redis unreachable on list_sessions") from exc
        except RedisError as exc:
            logger.warning("redis_error op=list_sessions reason=%s", type(exc).__name__)
            raise RedisUnavailable("redis error on list_sessions") from exc
        out: list[dict[str, Any]] = []
        stale: list[str] = []
        for sid, raw in zip(ids, raws, strict=True):
            if raw is None:
                stale.append(sid)
                continue
            out.append(json.loads(raw))
        if stale:
            try:
                await self._client.srem(_user_sessions_key(user_id, team_id), *stale)
            except RedisError:
                # Best-effort cleanup; not fatal.
                pass
        return out


# Module-level singleton bound at lifespan startup. Tests instantiate their
# own RedisSessionRegistry against a fresh client and skip the global.
_registry: RedisSessionRegistry | None = None


def get_registry() -> RedisSessionRegistry:
    if _registry is None:
        raise RuntimeError("redis registry not initialized — was lifespan run?")
    return _registry


def set_registry(registry: RedisSessionRegistry | None) -> None:
    global _registry
    _registry = registry
