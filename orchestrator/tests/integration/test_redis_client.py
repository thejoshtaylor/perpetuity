"""Integration tests for RedisSessionRegistry against a real redis container.

These exercise the actual redis-py async client and the live compose redis,
not a mock. Per the slice's "Proof Level: Integration" requirement, mocking
the redis boundary would silently pass even if the real client failed — the
tests must run against the real thing.

Run from inside the compose network:
    docker compose exec orchestrator uv run pytest tests/integration/test_redis_client.py
"""

from __future__ import annotations

import os
import uuid

import pytest
import redis.asyncio as redis_async

from orchestrator.errors import RedisUnavailable
from orchestrator.redis_client import RedisSessionRegistry


@pytest.fixture
async def registry(redis_endpoint: tuple[str, int]) -> RedisSessionRegistry:
    """Fresh registry pointed at the live redis. Cleans up on teardown."""
    host, port = redis_endpoint
    client = redis_async.Redis(
        host=host,
        port=port,
        password=os.environ.get("REDIS_PASSWORD") or None,
        decode_responses=True,
        socket_connect_timeout=2.0,
        socket_timeout=5.0,
    )
    reg = RedisSessionRegistry(client=client)
    yield reg
    # Best-effort flush of test-created keys; do NOT call FLUSHDB to avoid
    # nuking other test runs sharing the redis. Each test uses unique uuids.
    await reg.close()


def _record(user_id: str, team_id: str, **extra: object) -> dict[str, object]:
    return {
        "container_id": "ctr-" + uuid.uuid4().hex[:12],
        "tmux_session": "tmux-" + uuid.uuid4().hex[:8],
        "user_id": user_id,
        "team_id": team_id,
        **extra,
    }


@pytest.mark.asyncio
async def test_set_get_round_trip(registry: RedisSessionRegistry) -> None:
    sid = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    team_id = str(uuid.uuid4())
    rec = _record(user_id, team_id)

    await registry.set_session(sid, rec)
    got = await registry.get_session(sid)
    assert got is not None
    assert got["container_id"] == rec["container_id"]
    assert got["tmux_session"] == rec["tmux_session"]
    assert got["user_id"] == user_id
    assert got["team_id"] == team_id
    # set_session always stamps last_activity.
    assert isinstance(got["last_activity"], (int, float))


@pytest.mark.asyncio
async def test_get_missing_returns_none(registry: RedisSessionRegistry) -> None:
    got = await registry.get_session(str(uuid.uuid4()))
    assert got is None


@pytest.mark.asyncio
async def test_update_last_activity_advances(registry: RedisSessionRegistry) -> None:
    sid = str(uuid.uuid4())
    await registry.set_session(
        sid, _record(str(uuid.uuid4()), str(uuid.uuid4()))
    )
    initial = await registry.get_session(sid)
    assert initial is not None
    initial_ts = initial["last_activity"]

    # Tiny sleep so the timestamp delta is detectable.
    import asyncio

    await asyncio.sleep(0.05)
    await registry.update_last_activity(sid)
    later = await registry.get_session(sid)
    assert later is not None
    assert later["last_activity"] > initial_ts


@pytest.mark.asyncio
async def test_update_last_activity_missing_session_is_silent(
    registry: RedisSessionRegistry,
) -> None:
    # Should not raise — race with DELETE is expected; caller checks via get.
    await registry.update_last_activity(str(uuid.uuid4()))


@pytest.mark.asyncio
async def test_delete_removes_record_and_index(
    registry: RedisSessionRegistry,
) -> None:
    sid = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    team_id = str(uuid.uuid4())
    await registry.set_session(sid, _record(user_id, team_id))
    assert await registry.delete_session(sid) is True
    assert await registry.get_session(sid) is None
    # Index entry gone too — list_sessions returns nothing.
    assert await registry.list_sessions(user_id, team_id) == []
    # Second delete returns False (already gone).
    assert await registry.delete_session(sid) is False


@pytest.mark.asyncio
async def test_list_sessions_filters_by_user_team(
    registry: RedisSessionRegistry,
) -> None:
    user_a = str(uuid.uuid4())
    team_a = str(uuid.uuid4())
    user_b = str(uuid.uuid4())
    team_b = str(uuid.uuid4())

    sid1 = str(uuid.uuid4())
    sid2 = str(uuid.uuid4())
    sid3 = str(uuid.uuid4())
    await registry.set_session(sid1, _record(user_a, team_a))
    await registry.set_session(sid2, _record(user_a, team_a))
    await registry.set_session(sid3, _record(user_b, team_b))

    a_sessions = await registry.list_sessions(user_a, team_a)
    assert len(a_sessions) == 2
    a_ids = {s["container_id"] for s in a_sessions}
    assert len(a_ids) == 2  # both records came back distinct

    b_sessions = await registry.list_sessions(user_b, team_b)
    assert len(b_sessions) == 1


@pytest.mark.asyncio
async def test_set_session_rejects_missing_user_or_team() -> None:
    """Schema guard: registry refuses partial records — saves a future
    debugging session where a downstream caller forgot to populate user_id.
    """
    reg = RedisSessionRegistry()
    with pytest.raises(ValueError):
        await reg.set_session("sid", {"container_id": "x"})
    await reg.close()


@pytest.mark.asyncio
async def test_redis_unreachable_raises(registry: RedisSessionRegistry) -> None:
    """Point the registry at a black-hole port; assert RedisUnavailable.

    127.0.0.1:1 is reserved/unused on every sane host. We do NOT use the live
    redis on a wrong port here because we don't want the test to depend on
    the redis container being killable; pointing at an unreachable port
    achieves the same coverage without disturbing other tests.
    """
    bad_client = redis_async.Redis(
        host="127.0.0.1",
        port=1,
        decode_responses=True,
        socket_connect_timeout=0.5,
        socket_timeout=0.5,
    )
    bad_reg = RedisSessionRegistry(client=bad_client)
    try:
        with pytest.raises(RedisUnavailable):
            await bad_reg.set_session(
                str(uuid.uuid4()), _record(str(uuid.uuid4()), str(uuid.uuid4()))
            )
        with pytest.raises(RedisUnavailable):
            await bad_reg.get_session(str(uuid.uuid4()))
        with pytest.raises(RedisUnavailable):
            await bad_reg.update_last_activity(str(uuid.uuid4()))
        with pytest.raises(RedisUnavailable):
            await bad_reg.list_sessions(str(uuid.uuid4()), str(uuid.uuid4()))
    finally:
        await bad_reg.close()
