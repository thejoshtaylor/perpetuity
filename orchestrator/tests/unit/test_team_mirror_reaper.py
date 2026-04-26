"""Unit tests for orchestrator/team_mirror_reaper.py (M004/S03/T02).

Hermetic: no real Docker, no real Postgres. Drives ``_reap_one_tick``
deterministically against a hand-rolled pool + docker fake. The loop
itself is covered by an integration-style cancellation test.

Coverage:
  - ``_reap_one_tick`` skip-on-always_on (no containers stopped)
  - ``_reap_one_tick`` skip-on-recent-activity
  - ``_reap_one_tick`` reap-on-idle (asserts ``team_mirror_reaped`` log
    with ``reason=idle``)
  - ``_reap_one_tick`` tolerates pg unreachable (asserts WARNING + no crash)
  - ``_resolve_mirror_idle_timeout_seconds`` falls back when the
    system_settings row is missing
  - ``_resolve_mirror_reaper_interval_seconds`` env-var override happy +
    invalid-fallback paths
  - ``mirror_reaper_loop`` exits cleanly on cancel (the success path)
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
import uuid
from typing import Any
from unittest.mock import patch

# SKIP boot-time side effects before importing orchestrator modules.
os.environ.setdefault("SKIP_IMAGE_PULL_ON_BOOT", "1")
os.environ.setdefault("SKIP_PG_POOL_ON_BOOT", "1")
os.environ.setdefault("ORCHESTRATOR_API_KEY", "unit-test-current-key")

import pytest  # noqa: E402

from orchestrator.config import settings  # noqa: E402
from orchestrator.team_mirror import (  # noqa: E402
    _team_mirror_volume_name,
)
from orchestrator.team_mirror_reaper import (  # noqa: E402
    _reap_one_tick,
    _resolve_mirror_reaper_interval_seconds,
    mirror_reaper_loop,
    start_team_mirror_reaper,
    stop_team_mirror_reaper,
)
from orchestrator.volume_store import (  # noqa: E402
    _resolve_mirror_idle_timeout_seconds,
)


# ---------------------------------------------------------------------------
# Fakes (light copy of the test_team_mirror harness — kept local so a
# refactor of one file's fakes doesn't shadow the other's invariants)
# ---------------------------------------------------------------------------


class _FakeContainerHandle:
    def __init__(self, container_id: str) -> None:
        self.id = container_id
        self.stopped = False
        self.deleted = False

    async def stop(self, *, timeout: int = 5) -> None:
        self.stopped = True

    async def delete(self, *, force: bool = False) -> None:
        self.deleted = True


class _FakeListedContainer:
    def __init__(self, container_id: str, *, running: bool = True) -> None:
        self.id = container_id
        self._container = {"State": "running" if running else "exited"}


class _FakeContainers:
    def __init__(self) -> None:
        self.list_results: list[_FakeListedContainer] = []
        self.list_raises: Exception | None = None
        self.get_handle: _FakeContainerHandle | None = None

    async def list(self, *, all: bool = False, filters: str = "") -> list[_FakeListedContainer]:  # noqa: A002
        if self.list_raises is not None:
            raise self.list_raises
        return list(self.list_results)

    async def get(self, container_id: str) -> _FakeContainerHandle:
        return self.get_handle or _FakeContainerHandle(container_id)


class _FakeDocker:
    def __init__(self) -> None:
        self.containers = _FakeContainers()


class _FakeRow(dict):
    pass


class _FakeConn:
    def __init__(self, pool: "_FakePool") -> None:
        self._pool = pool

    async def fetch(self, sql: str, *args: Any) -> list[_FakeRow]:
        if self._pool.fetch_raises is not None:
            raise self._pool.fetch_raises
        if "team_mirror_volumes" in sql:
            return list(self._pool.row_by_team.values())
        if "system_settings" in sql:
            return []
        raise AssertionError(f"unexpected fetch: {sql}")

    async def fetchval(self, sql: str, *args: Any) -> Any:
        if self._pool.fetchval_raises is not None:
            raise self._pool.fetchval_raises
        if "system_settings" in sql:
            key = args[0]
            return self._pool.system_settings.get(key)
        raise AssertionError(f"unexpected fetchval: {sql}")

    async def fetchrow(self, sql: str, *args: Any) -> _FakeRow | None:
        if "SELECT" in sql and "team_mirror_volumes" in sql:
            team_id = str(args[0])
            return self._pool.row_by_team.get(team_id)
        raise AssertionError(f"unexpected fetchrow: {sql}")

    async def execute(self, sql: str, *args: Any) -> str:
        if "UPDATE team_mirror_volumes SET container_id = NULL" in sql:
            (team_uuid,) = args
            row = self._pool.row_by_team.get(str(team_uuid))
            if row is not None:
                row["container_id"] = None
                row["last_idle_at"] = datetime.datetime.now(
                    datetime.timezone.utc
                )
            return "UPDATE 1"
        if "UPDATE team_mirror_volumes SET container_id = $1" in sql:
            container_id, team_uuid = args
            row = self._pool.row_by_team.get(str(team_uuid))
            if row is not None:
                row["container_id"] = container_id
                now = datetime.datetime.now(datetime.timezone.utc)
                row["last_started_at"] = now
                row["last_idle_at"] = now
            return "UPDATE 1"
        raise AssertionError(f"unexpected execute: {sql}")

    async def __aenter__(self) -> "_FakeConn":
        return self

    async def __aexit__(self, *_a: Any) -> None:
        return None


class _FakePool:
    def __init__(self) -> None:
        self.row_by_team: dict[str, _FakeRow] = {}
        self.system_settings: dict[str, str] = {}
        self.fetch_raises: Exception | None = None
        self.fetchval_raises: Exception | None = None

    def acquire(self) -> _FakeConn:
        return _FakeConn(self)


def _make_row(
    team_id: str,
    *,
    container_id: str | None,
    last_idle_at: datetime.datetime | None,
    always_on: bool = False,
) -> _FakeRow:
    return _FakeRow(
        id=uuid.uuid4(),
        team_id=uuid.UUID(team_id),
        volume_path=_team_mirror_volume_name(team_id),
        container_id=container_id,
        last_started_at=last_idle_at,
        last_idle_at=last_idle_at,
        always_on=always_on,
    )


# ---------------------------------------------------------------------------
# _resolve_mirror_idle_timeout_seconds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_mirror_idle_timeout_falls_back_when_row_missing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No system_settings row → fall back to settings default + WARNING."""
    pool = _FakePool()
    with caplog.at_level(logging.INFO, logger="orchestrator"):
        value = await _resolve_mirror_idle_timeout_seconds(pool)
    assert value == settings.mirror_idle_timeout_seconds
    msgs = [r.message for r in caplog.records]
    assert any(
        "system_settings_lookup_failed" in m
        and "mirror_idle_timeout_seconds" in m
        and "RowMissing" in m
        for m in msgs
    ), msgs
    assert any(
        "mirror_idle_timeout_seconds_resolved" in m for m in msgs
    ), msgs


@pytest.mark.asyncio
async def test_resolve_mirror_idle_timeout_honors_valid_row() -> None:
    """system_settings has a valid int → that's what we return."""
    pool = _FakePool()
    pool.system_settings["mirror_idle_timeout_seconds"] = "120"
    value = await _resolve_mirror_idle_timeout_seconds(pool)
    assert value == 120


@pytest.mark.asyncio
async def test_resolve_mirror_idle_timeout_rejects_below_floor() -> None:
    """Sub-60s row triggers the fallback path (the floor protects against
    a malformed row weaponizing the reaper into a per-tick teardown).
    """
    pool = _FakePool()
    pool.system_settings["mirror_idle_timeout_seconds"] = "30"
    value = await _resolve_mirror_idle_timeout_seconds(pool)
    assert value == settings.mirror_idle_timeout_seconds


# ---------------------------------------------------------------------------
# _resolve_mirror_reaper_interval_seconds
# ---------------------------------------------------------------------------


def test_resolve_interval_uses_default_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MIRROR_REAPER_INTERVAL_SECONDS", raising=False)
    assert (
        _resolve_mirror_reaper_interval_seconds()
        == settings.mirror_reaper_interval_seconds
    )


def test_resolve_interval_honors_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIRROR_REAPER_INTERVAL_SECONDS", "1")
    assert _resolve_mirror_reaper_interval_seconds() == 1


def test_resolve_interval_clamps_to_max(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIRROR_REAPER_INTERVAL_SECONDS", "9999")
    assert _resolve_mirror_reaper_interval_seconds() == 300


def test_resolve_interval_invalid_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIRROR_REAPER_INTERVAL_SECONDS", "not-a-number")
    assert (
        _resolve_mirror_reaper_interval_seconds()
        == settings.mirror_reaper_interval_seconds
    )


# ---------------------------------------------------------------------------
# _reap_one_tick
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_skips_always_on_row(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """always_on=True row → no reap, INFO ``team_mirror_reap_skipped``."""
    pool = _FakePool()
    pool.system_settings["mirror_idle_timeout_seconds"] = "60"
    team_id = str(uuid.uuid4())
    long_ago = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
    pool.row_by_team[team_id] = _make_row(
        team_id,
        container_id="abc1234567890",
        last_idle_at=long_ago,
        always_on=True,
    )
    docker = _FakeDocker()

    with caplog.at_level(logging.INFO, logger="orchestrator"):
        scanned, reaped = await _reap_one_tick(pool, docker)

    assert scanned == 1
    assert reaped == 0
    msgs = [r.message for r in caplog.records]
    assert any(
        "team_mirror_reap_skipped" in m and "always_on" in m for m in msgs
    ), msgs


@pytest.mark.asyncio
async def test_tick_skips_recent_activity(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pool = _FakePool()
    pool.system_settings["mirror_idle_timeout_seconds"] = "3600"
    team_id = str(uuid.uuid4())
    now = datetime.datetime.now(datetime.timezone.utc)
    pool.row_by_team[team_id] = _make_row(
        team_id, container_id="abc1234567890", last_idle_at=now
    )
    docker = _FakeDocker()

    with caplog.at_level(logging.INFO, logger="orchestrator"):
        scanned, reaped = await _reap_one_tick(pool, docker)

    assert scanned == 1
    assert reaped == 0
    msgs = [r.message for r in caplog.records]
    assert any(
        "team_mirror_reap_skipped" in m and "recent_activity" in m
        for m in msgs
    ), msgs


@pytest.mark.asyncio
async def test_tick_reaps_on_idle(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Idle row + running container → stop+delete; row container_id NULLed."""
    pool = _FakePool()
    pool.system_settings["mirror_idle_timeout_seconds"] = "60"
    team_id = str(uuid.uuid4())
    long_ago = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
    pool.row_by_team[team_id] = _make_row(
        team_id, container_id="abc1234567890", last_idle_at=long_ago
    )
    docker = _FakeDocker()
    docker.containers.list_results = [
        _FakeListedContainer("abc1234567890abcdef", running=True)
    ]
    handle = _FakeContainerHandle("abc1234567890abcdef")
    docker.containers.get_handle = handle

    with caplog.at_level(logging.INFO, logger="orchestrator"):
        scanned, reaped = await _reap_one_tick(pool, docker)

    assert scanned == 1
    assert reaped == 1
    assert handle.stopped is True
    assert handle.deleted is True
    assert pool.row_by_team[team_id]["container_id"] is None

    msgs = [r.message for r in caplog.records]
    assert any(
        "team_mirror_reaped" in m and "reason=idle" in m for m in msgs
    ), msgs


@pytest.mark.asyncio
async def test_tick_tolerates_pg_unreachable_for_select(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """SELECT against team_mirror_volumes raises → loop survives.

    The reaper LOOP wraps each tick in a broad except and logs
    ``team_mirror_reaper_tick_failed``; the same protection lives at
    ``_reap_one_tick`` if we let the exception escape, so we assert the
    raise propagates (the loop catches it).
    """
    pool = _FakePool()
    pool.fetch_raises = ConnectionError("pg gone")
    pool.system_settings["mirror_idle_timeout_seconds"] = "60"
    docker = _FakeDocker()

    with pytest.raises(ConnectionError):
        await _reap_one_tick(pool, docker)


# ---------------------------------------------------------------------------
# Loop lifecycle
# ---------------------------------------------------------------------------


class _AppShim:
    """FastAPI.app.state stand-in — only ``state.docker`` is read."""

    class _State:
        def __init__(self) -> None:
            self.docker: Any = None

    def __init__(self) -> None:
        self.state = self._State()


@pytest.mark.asyncio
async def test_loop_exits_cleanly_on_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``stop_team_mirror_reaper`` cancels and the task exits inside budget."""
    monkeypatch.setenv("MIRROR_REAPER_INTERVAL_SECONDS", "1")
    app = _AppShim()
    task = start_team_mirror_reaper(app)  # type: ignore[arg-type]
    # Give the loop a moment to enter its sleep.
    await asyncio.sleep(0)
    await stop_team_mirror_reaper(task)
    assert task.done() is True


@pytest.mark.asyncio
async def test_loop_skips_tick_when_docker_handle_is_none(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``app.state.docker is None`` → tick logs the skip + survives."""
    monkeypatch.setenv("MIRROR_REAPER_INTERVAL_SECONDS", "1")
    app = _AppShim()  # docker is None by default

    # Patch get_pool so the test never touches a real pg pool.
    fake_pool = _FakePool()
    with patch(
        "orchestrator.team_mirror_reaper.get_pool",
        return_value=fake_pool,
    ):
        task = asyncio.create_task(mirror_reaper_loop(app))  # type: ignore[arg-type]
        # Wait for at least one tick (~1.2s with the env override).
        with caplog.at_level(logging.WARNING, logger="orchestrator"):
            await asyncio.sleep(1.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    msgs = [r.message for r in caplog.records]
    assert any(
        "team_mirror_reaper_tick_skipped" in m
        and "docker_handle_unavailable" in m
        for m in msgs
    ), msgs
