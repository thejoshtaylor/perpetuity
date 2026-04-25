"""Unit tests for orchestrator/attach_map.py (S04/T01).

Hermetic — no Redis, no Docker, no FastAPI lifespan. Each test instantiates
its own AttachMap so refcount state never leaks across tests.

Coverage:
  - register/unregister increment+decrement
  - extra unregister floors at zero (no negatives)
  - concurrent register+unregister under asyncio.gather preserves invariant
  - live_session_ids returns only keys with count > 0
  - is_attached returns False for never-registered ids
  - get_attach_map / set_attach_map round-trip + lazy init
"""

from __future__ import annotations

import asyncio

import pytest

from orchestrator.attach_map import (
    AttachMap,
    get_attach_map,
    set_attach_map,
)


async def test_register_increments_count() -> None:
    am = AttachMap()
    assert await am.register("sid-a") == 1
    assert await am.register("sid-a") == 2
    assert await am.register("sid-a") == 3
    assert await am.is_attached("sid-a") is True


async def test_unregister_decrements_count() -> None:
    am = AttachMap()
    await am.register("sid-a")
    await am.register("sid-a")
    assert await am.unregister("sid-a") == 1
    assert await am.is_attached("sid-a") is True
    assert await am.unregister("sid-a") == 0
    assert await am.is_attached("sid-a") is False


async def test_unregister_floors_at_zero() -> None:
    """Extra unregister must NEVER drop below zero — the reaper treats any
    positive count as live, so a negative count would be both nonsensical
    and a footgun (a future is_attached check might compare > 0 vs != 0).
    """
    am = AttachMap()
    assert await am.unregister("never-registered") == 0
    assert await am.unregister("never-registered") == 0
    await am.register("sid-a")
    await am.unregister("sid-a")
    assert await am.unregister("sid-a") == 0
    assert await am.unregister("sid-a") == 0
    assert await am.is_attached("sid-a") is False


async def test_is_attached_false_for_never_registered() -> None:
    am = AttachMap()
    assert await am.is_attached("nope") is False


async def test_live_session_ids_returns_only_positive() -> None:
    am = AttachMap()
    await am.register("a")
    await am.register("b")
    await am.register("b")
    await am.register("c")
    await am.unregister("c")  # back to 0 — should drop out of the set

    live = await am.live_session_ids()
    assert live == {"a", "b"}


async def test_live_session_ids_returns_snapshot_copy() -> None:
    """Mutating the returned set must not corrupt the map's internal state."""
    am = AttachMap()
    await am.register("a")
    snapshot = await am.live_session_ids()
    snapshot.clear()
    snapshot.add("ghost")
    # Internal state is untouched.
    assert await am.is_attached("a") is True
    assert await am.is_attached("ghost") is False


async def test_concurrent_register_unregister_preserves_invariant() -> None:
    """N concurrent register tasks then N concurrent unregister tasks must
    leave the count at exactly 0 — proves the asyncio.Lock serialization.
    """
    am = AttachMap()
    sid = "concurrent-sid"
    n = 200

    async def reg() -> None:
        await am.register(sid)

    async def unreg() -> None:
        await am.unregister(sid)

    await asyncio.gather(*(reg() for _ in range(n)))
    assert await am.is_attached(sid) is True
    live = await am.live_session_ids()
    assert sid in live

    await asyncio.gather(*(unreg() for _ in range(n)))
    assert await am.is_attached(sid) is False
    assert await am.live_session_ids() == set()


async def test_concurrent_interleaved_register_unregister() -> None:
    """Interleaved register/unregister under gather: every register has a
    matching unregister, so end state must be empty even though ordering is
    nondeterministic. Probes the lock under contention.
    """
    am = AttachMap()
    sid = "interleaved-sid"
    n = 150

    async def pair() -> None:
        await am.register(sid)
        # Yield to let other tasks interleave.
        await asyncio.sleep(0)
        await am.unregister(sid)

    await asyncio.gather(*(pair() for _ in range(n)))
    assert await am.is_attached(sid) is False
    assert await am.live_session_ids() == set()


async def test_multiple_session_ids_isolated() -> None:
    am = AttachMap()
    await am.register("a")
    await am.register("a")
    await am.register("b")
    assert await am.is_attached("a") is True
    assert await am.is_attached("b") is True
    await am.unregister("a")
    await am.unregister("a")
    assert await am.is_attached("a") is False
    assert await am.is_attached("b") is True


@pytest.fixture(autouse=True)
def _reset_attach_map_singleton() -> None:
    """Each test owns its module-level singleton state."""
    set_attach_map(None)
    yield
    set_attach_map(None)


def test_get_attach_map_lazy_inits() -> None:
    set_attach_map(None)
    am = get_attach_map()
    assert isinstance(am, AttachMap)
    # Subsequent gets return the same instance.
    assert get_attach_map() is am


def test_set_attach_map_replaces_singleton() -> None:
    fresh = AttachMap()
    set_attach_map(fresh)
    assert get_attach_map() is fresh
    set_attach_map(None)
    am = get_attach_map()
    assert am is not fresh
