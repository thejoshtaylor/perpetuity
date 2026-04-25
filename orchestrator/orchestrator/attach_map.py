"""Process-local live-attach refcount map for the S04 idle reaper (T01).

The reaper performs a two-phase liveness check (D018) on every candidate
session: (1) Redis `last_activity` is older than `idle_timeout_seconds`,
AND (2) no live WS attach is currently bridged. Redis cannot answer (2)
because the orchestrator process is the sole owner of the live WS attach;
a Redis hop would just reflect what the orchestrator already knows. So
we keep this map in-process.

Refcount, not bool: per MEM097 the locked WS frame protocol is unchanged
in S04, but the cooperative-attach behavior of `tmux attach-session`
allows two simultaneous WS clients to share one tmux session. The reaper
must see "live" if any client is attached, so we track an integer count
per session_id and treat count > 0 as "attached".

Process-local on purpose: an orchestrator restart drops every WS attach
because the docker exec stream is owned by the orchestrator process
(D012/MEM092 — tmux survives, the exec attach does not). After a restart
the truth is "zero attaches" and an empty map is exactly that truth, so
we don't persist the map anywhere.

Public surface:
  - `register(session_id) -> int`     new count after increment
  - `unregister(session_id) -> int`   new count after decrement (floor 0)
  - `is_attached(session_id) -> bool` count > 0
  - `live_session_ids() -> set[str]`  snapshot of keys with count > 0

Module-level singleton mirrors `redis_client.get_registry` and
`volume_store.get_pool`: tests inject a fresh map per test via
`set_attach_map`.
"""

from __future__ import annotations

import asyncio


class AttachMap:
    """Async-safe refcount keyed by session_id.

    Internally a `dict[str, int]` guarded by a single `asyncio.Lock`.
    The hot path (register/unregister) is O(1) under the lock; the reaper
    snapshot path (`live_session_ids`) takes a copy under the lock so
    callers iterate without races.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._counts: dict[str, int] = {}

    async def register(self, session_id: str) -> int:
        async with self._lock:
            new = self._counts.get(session_id, 0) + 1
            self._counts[session_id] = new
            return new

    async def unregister(self, session_id: str) -> int:
        async with self._lock:
            current = self._counts.get(session_id, 0)
            if current <= 1:
                # Floor at zero. Drop the key entirely so the map size
                # tracks live attach count, not lifetime attach count —
                # the reaper's snapshot stays cheap even after many
                # connect/disconnect cycles.
                self._counts.pop(session_id, None)
                return 0
            new = current - 1
            self._counts[session_id] = new
            return new

    async def is_attached(self, session_id: str) -> bool:
        async with self._lock:
            return self._counts.get(session_id, 0) > 0

    async def live_session_ids(self) -> set[str]:
        async with self._lock:
            return {sid for sid, n in self._counts.items() if n > 0}


_ATTACH_MAP: AttachMap | None = None


def get_attach_map() -> AttachMap:
    """Return the module-level AttachMap, creating it on first access.

    Lazy-init (rather than lifespan-bound like the Redis registry) because
    the map has no external resource to open or close — it's pure
    in-process state. This also means importers of `routes_ws.py` get a
    valid map even in the unit suite where the lifespan never runs.
    """
    global _ATTACH_MAP
    if _ATTACH_MAP is None:
        _ATTACH_MAP = AttachMap()
    return _ATTACH_MAP


def set_attach_map(attach_map: AttachMap | None) -> None:
    """Replace the module-level AttachMap (or clear it).

    Mirrors `set_registry`/`set_pool`. Tests inject a fresh map per test
    so refcount state from a prior test can never leak across test
    boundaries.
    """
    global _ATTACH_MAP
    _ATTACH_MAP = attach_map


__all__ = [
    "AttachMap",
    "get_attach_map",
    "set_attach_map",
]
