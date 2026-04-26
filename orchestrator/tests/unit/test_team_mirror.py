"""Unit tests for orchestrator/team_mirror.py (M004/S03/T02).

Hermetic: no real Docker, no real Postgres. We stand up:

  - ``_FakeDockerContainers`` — exposes ``list``, ``create_or_replace``,
    ``get`` with controllable behavior so tests can drive the cold-start,
    warm-reuse, 409-race, and stop+remove paths.
  - ``_FakePool`` — asyncpg.Pool stand-in with an in-memory dict shaped
    like a single team_mirror_volumes row.

Coverage:
  - ``ensure_team_mirror`` cold-start: row INSERT + container create with
    correct labels/cmd/volume mount; ``team_mirror_started`` log emitted.
  - ``ensure_team_mirror`` warm reuse: existing-by-label container is
    returned without create; ``team_mirror_reused`` log emitted.
  - ``ensure_team_mirror`` 409 race: create returns 409; falls back to
    label-list lookup and returns the winner's id.
  - ``reap_team_mirror`` happy path: container is stopped+deleted, row
    NULLs container_id; ``team_mirror_reaped`` log emitted.
  - ``reap_team_mirror`` no-container: returns False without raising.
  - ``reap_team_mirror`` 404 race: container disappears between list and
    get; treated as benign no-op.
  - ``is_row_reapable`` boundary table: always_on, no_container,
    no_last_idle_at, recent_activity, idle.
  - ``_team_mirror_container_name`` / ``_team_mirror_volume_name`` /
    ``_network_addr`` are stable + dash-tolerant.
  - ``_build_team_mirror_container_config`` carries the right labels,
    cmd, port, and named-volume mount.
"""

from __future__ import annotations

import datetime
import logging
import os
import uuid
from typing import Any

# SKIP boot-time side effects before importing orchestrator modules.
os.environ.setdefault("SKIP_IMAGE_PULL_ON_BOOT", "1")
os.environ.setdefault("SKIP_PG_POOL_ON_BOOT", "1")
os.environ.setdefault("ORCHESTRATOR_API_KEY", "unit-test-current-key")

import pytest  # noqa: E402
from aiodocker.exceptions import DockerError  # noqa: E402

from orchestrator.errors import (  # noqa: E402
    DockerUnavailable,
)
from orchestrator.team_mirror import (  # noqa: E402
    _build_team_mirror_container_config,
    _MIRROR_PORT,
    _network_addr,
    _team_mirror_container_name,
    _team_mirror_volume_name,
    ensure_team_mirror,
    is_row_reapable,
    reap_team_mirror,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeContainerHandle:
    """Stand-in for ``docker.containers.get(...)`` return value."""

    def __init__(
        self, container_id: str, *, stop_raises: Exception | None = None
    ) -> None:
        self.id = container_id
        self.stopped = False
        self.deleted = False
        self.started = False
        self._stop_raises = stop_raises

    async def start(self) -> None:
        self.started = True

    async def stop(self, *, timeout: int = 5) -> None:
        if self._stop_raises is not None:
            raise self._stop_raises
        self.stopped = True

    async def delete(self, *, force: bool = False) -> None:
        self.deleted = True


class _FakeListedContainer:
    """Stand-in for entries in ``docker.containers.list(...)`` output.

    The real aiodocker DockerContainer carries a ``_container`` dict with
    the inspect-style record; ``_find_team_mirror_container`` reads
    ``_container["State"]`` to detect running. We mirror that shape.
    """

    def __init__(self, container_id: str, *, running: bool = True) -> None:
        self.id = container_id
        self._container = {"State": "running" if running else "exited"}


class _FakeContainers:
    """Programmable stand-in for ``docker.containers``."""

    def __init__(self) -> None:
        self.list_results: list[_FakeListedContainer] = []
        self.list_raises: Exception | None = None
        # create_or_replace returns the handle. By default we mint a
        # fresh id; tests can override.
        self.create_id: str = "newcontainer1234567890abcdef"
        self.create_raises: Exception | None = None
        self.get_handle: _FakeContainerHandle | None = None
        self.get_raises: Exception | None = None
        # Recorded for assertions:
        self.list_calls: list[dict[str, Any]] = []
        self.create_calls: list[dict[str, Any]] = []
        self.get_calls: list[str] = []

    async def list(self, *, all: bool = False, filters: str = "") -> list[_FakeListedContainer]:  # noqa: A002
        self.list_calls.append({"all": all, "filters": filters})
        if self.list_raises is not None:
            raise self.list_raises
        return list(self.list_results)

    async def create_or_replace(
        self, *, name: str, config: dict[str, Any]
    ) -> _FakeContainerHandle:
        self.create_calls.append({"name": name, "config": config})
        if self.create_raises is not None:
            raise self.create_raises
        return _FakeContainerHandle(self.create_id)

    async def get(self, container_id: str) -> _FakeContainerHandle:
        self.get_calls.append(container_id)
        if self.get_raises is not None:
            raise self.get_raises
        return self.get_handle or _FakeContainerHandle(container_id)


class _FakeDocker:
    def __init__(self) -> None:
        self.containers = _FakeContainers()


class _FakeRow(dict):
    """Dict-shaped row matching what asyncpg.Record exposes for our SELECTs."""


class _FakeConn:
    def __init__(self, pool: "_FakePool") -> None:
        self._pool = pool

    async def fetchrow(self, sql: str, *args: Any) -> _FakeRow | None:
        if "SELECT" in sql and "team_mirror_volumes" in sql:
            team_id = str(args[0])
            return self._pool.row_by_team.get(team_id)
        if "INSERT INTO team_mirror_volumes" in sql:
            new_id, team_uuid, volume_path = args
            team_id = str(team_uuid)
            if team_id in self._pool.row_by_team:
                # Simulate the unique-violation race.
                import asyncpg

                raise asyncpg.UniqueViolationError(
                    "duplicate key value violates unique constraint "
                    "uq_team_mirror_volumes_team_id"
                )
            row = _FakeRow(
                id=new_id,
                team_id=team_uuid,
                volume_path=volume_path,
                container_id=None,
                last_started_at=None,
                last_idle_at=None,
                always_on=False,
            )
            self._pool.row_by_team[team_id] = row
            return row
        raise AssertionError(f"unexpected fetchrow sql: {sql}")

    async def execute(self, sql: str, *args: Any) -> str:
        if "UPDATE team_mirror_volumes SET container_id = $1" in sql:
            container_id, team_uuid = args
            team_id = str(team_uuid)
            row = self._pool.row_by_team.get(team_id)
            if row is not None:
                row["container_id"] = container_id
                now = datetime.datetime.now(datetime.timezone.utc)
                row["last_started_at"] = now
                row["last_idle_at"] = now
            return "UPDATE 1"
        if (
            "UPDATE team_mirror_volumes SET container_id = NULL" in sql
        ):
            (team_uuid,) = args
            team_id = str(team_uuid)
            row = self._pool.row_by_team.get(team_id)
            if row is not None:
                row["container_id"] = None
                row["last_idle_at"] = datetime.datetime.now(
                    datetime.timezone.utc
                )
            return "UPDATE 1"
        raise AssertionError(f"unexpected execute sql: {sql}")

    async def fetch(self, sql: str, *args: Any) -> list[_FakeRow]:
        if "SELECT" in sql and "team_mirror_volumes" in sql:
            return list(self._pool.row_by_team.values())
        raise AssertionError(f"unexpected fetch sql: {sql}")

    async def __aenter__(self) -> "_FakeConn":
        return self

    async def __aexit__(self, *_a: Any) -> None:
        return None


class _FakePool:
    def __init__(self) -> None:
        self.row_by_team: dict[str, _FakeRow] = {}

    def acquire(self) -> _FakeConn:
        return _FakeConn(self)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_container_name_dash_tolerant() -> None:
    """The first 8 chars after stripping dashes is the docker name suffix."""
    canonical = "abcdef12-3456-7890-abcd-ef0123456789"
    assert _team_mirror_container_name(canonical) == "team-mirror-abcdef12"
    # Non-canonical / extra dashes is fine — stripped first.
    assert (
        _team_mirror_container_name("abcdef--12-3456-7890-abcd")
        == "team-mirror-abcdef12"
    )


def test_volume_name_uses_first8() -> None:
    canonical = "abcdef12-3456-7890-abcd-ef0123456789"
    assert (
        _team_mirror_volume_name(canonical)
        == "perpetuity-team-mirror-abcdef12"
    )


def test_network_addr_includes_port() -> None:
    canonical = "abcdef12-3456-7890-abcd-ef0123456789"
    assert _network_addr(canonical) == f"team-mirror-abcdef12:{_MIRROR_PORT}"


def test_build_container_config_carries_labels_cmd_and_volume() -> None:
    team_id = "abcdef12-3456-7890-abcd-ef0123456789"
    volume = _team_mirror_volume_name(team_id)
    config = _build_team_mirror_container_config(team_id, volume)
    # Labels: scoped enough that the reaper's filter cannot collide with
    # user-session containers.
    labels = config["Labels"]
    assert labels["team_id"] == team_id
    assert labels["perpetuity.managed"] == "true"
    assert labels["perpetuity.team_mirror"] == "true"
    # Cmd: git daemon with the documented flags.
    cmd = config["Cmd"]
    assert cmd[0] == "git"
    assert cmd[1] == "daemon"
    assert "--base-path=/repos" in cmd
    assert "--export-all" in cmd
    assert "--reuseaddr" in cmd
    assert "--enable=receive-pack" in cmd
    assert f"--port={_MIRROR_PORT}" in cmd
    # Mount: named docker volume at /repos.
    mounts = config["HostConfig"]["Mounts"]
    assert mounts == [
        {"Type": "volume", "Source": volume, "Target": "/repos"}
    ]
    # Network: attaches to the compose network so user containers can
    # resolve us by name.
    assert config["HostConfig"]["NetworkMode"] == "perpetuity_default"
    # No restart policy; the reaper owns lifecycle.
    assert config["HostConfig"]["RestartPolicy"] == {"Name": "no"}
    # Port declared.
    assert config["ExposedPorts"] == {f"{_MIRROR_PORT}/tcp": {}}


# ---------------------------------------------------------------------------
# is_row_reapable boundary
# ---------------------------------------------------------------------------


def _row(
    *,
    container_id: str | None = "abc1234567890",
    last_idle_at: datetime.datetime | None = None,
    always_on: bool = False,
) -> dict[str, Any]:
    return {
        "container_id": container_id,
        "last_idle_at": last_idle_at,
        "always_on": always_on,
    }


def test_is_row_reapable_always_on_wins() -> None:
    """always_on is the admin opt-out; even an idle row is not reapable."""
    long_ago = datetime.datetime(
        2020, 1, 1, tzinfo=datetime.timezone.utc
    )
    reapable, reason = is_row_reapable(
        _row(last_idle_at=long_ago, always_on=True),
        idle_timeout_seconds=60,
        now_epoch=long_ago.timestamp() + 999_999,
    )
    assert reapable is False
    assert reason == "always_on"


def test_is_row_reapable_no_container() -> None:
    reapable, reason = is_row_reapable(
        _row(container_id=None), idle_timeout_seconds=60
    )
    assert reapable is False
    assert reason == "no_container"


def test_is_row_reapable_no_last_idle_at() -> None:
    reapable, reason = is_row_reapable(
        _row(last_idle_at=None), idle_timeout_seconds=60
    )
    assert reapable is False
    assert reason == "no_last_idle_at"


def test_is_row_reapable_recent_activity() -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    reapable, reason = is_row_reapable(
        _row(last_idle_at=now),
        idle_timeout_seconds=60,
        now_epoch=now.timestamp() + 30,  # 30s < 60s threshold
    )
    assert reapable is False
    assert reason == "recent_activity"


def test_is_row_reapable_idle_passes_threshold() -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    reapable, reason = is_row_reapable(
        _row(last_idle_at=now),
        idle_timeout_seconds=60,
        now_epoch=now.timestamp() + 120,  # 120s > 60s threshold
    )
    assert reapable is True
    assert reason == "idle"


# ---------------------------------------------------------------------------
# ensure_team_mirror
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_cold_start_inserts_row_and_creates_container(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Cold-start: no existing row, no existing container.

    Asserts:
      - The row gets INSERTed with the volume name we expect.
      - container_create_or_replace gets called with the right name + config.
      - The row's container_id + last_started_at + last_idle_at are stamped.
      - INFO ``team_mirror_started`` is emitted with team_id + container_id
        + network_addr + trigger.
    """
    pool = _FakePool()
    docker = _FakeDocker()
    docker.containers.create_id = "freshcontainerid1234567890ab"
    team_id = str(uuid.uuid4())
    expected_name = _team_mirror_container_name(team_id)
    expected_volume = _team_mirror_volume_name(team_id)

    with caplog.at_level(logging.INFO, logger="orchestrator"):
        result = await ensure_team_mirror(pool, docker, team_id)

    assert result["container_id"] == "freshcontainerid1234567890ab"
    assert result["network_addr"] == _network_addr(team_id)
    assert result["reused"] is False

    # Row inserted with the right volume name + container_id stamped.
    row = pool.row_by_team[team_id]
    assert row["volume_path"] == expected_volume
    assert row["container_id"] == "freshcontainerid1234567890ab"
    assert row["last_started_at"] is not None
    assert row["last_idle_at"] is not None

    # create_or_replace was called with the container name + config.
    assert len(docker.containers.create_calls) == 1
    call = docker.containers.create_calls[0]
    assert call["name"] == expected_name
    assert call["config"]["Labels"]["team_id"] == team_id
    assert call["config"]["HostConfig"]["Mounts"] == [
        {"Type": "volume", "Source": expected_volume, "Target": "/repos"}
    ]

    # Log: team_mirror_started fired.
    msgs = [r.message for r in caplog.records]
    assert any(
        "team_mirror_started" in m and team_id in m for m in msgs
    ), msgs


@pytest.mark.asyncio
async def test_ensure_warm_path_reuses_existing_container(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Warm path: a running container with matching labels exists.

    Asserts:
      - No second create_or_replace call.
      - Row's container_id matches the existing id.
      - INFO ``team_mirror_reused`` emitted.
    """
    pool = _FakePool()
    team_id = str(uuid.uuid4())
    # Pre-seed the row as if a prior ensure had landed.
    pool.row_by_team[team_id] = _FakeRow(
        id=uuid.uuid4(),
        team_id=uuid.UUID(team_id),
        volume_path=_team_mirror_volume_name(team_id),
        container_id="oldcontainerid12345",
        last_started_at=datetime.datetime.now(datetime.timezone.utc),
        last_idle_at=datetime.datetime.now(datetime.timezone.utc),
        always_on=False,
    )
    docker = _FakeDocker()
    docker.containers.list_results = [
        _FakeListedContainer(
            "warmcontainerid1234567890abcdef", running=True
        )
    ]

    with caplog.at_level(logging.INFO, logger="orchestrator"):
        result = await ensure_team_mirror(pool, docker, team_id)

    assert result["container_id"] == "warmcontainerid1234567890abcdef"
    assert result["reused"] is True
    # No create call on the warm path.
    assert docker.containers.create_calls == []

    # Row container_id was UPDATEd to the warm container.
    assert (
        pool.row_by_team[team_id]["container_id"]
        == "warmcontainerid1234567890abcdef"
    )

    msgs = [r.message for r in caplog.records]
    assert any("team_mirror_reused" in m for m in msgs), msgs


@pytest.mark.asyncio
async def test_ensure_409_race_falls_back_to_filter_lookup() -> None:
    """Concurrent ensure: create raises 409, fall back to filter list."""
    pool = _FakePool()
    docker = _FakeDocker()
    team_id = str(uuid.uuid4())
    # First list (warmth check) returns nothing; second list (after 409)
    # returns the winner. We model that with a counter.
    state: dict[str, int] = {"calls": 0}
    winner_id = "winnercontainer1234567890abcd"

    real_list = docker.containers.list

    async def _listing(*, all: bool = False, filters: str = "") -> list[_FakeListedContainer]:  # noqa: A002
        state["calls"] += 1
        if state["calls"] == 1:
            return await real_list(all=all, filters=filters)
        return [_FakeListedContainer(winner_id, running=True)]

    docker.containers.list = _listing  # type: ignore[assignment]
    docker.containers.create_raises = DockerError(409, "name in use")

    result = await ensure_team_mirror(pool, docker, team_id)

    assert result["container_id"] == winner_id
    assert result["reused"] is True
    # Row should be stamped to the winner.
    assert pool.row_by_team[team_id]["container_id"] == winner_id


@pytest.mark.asyncio
async def test_ensure_docker_unreachable_raises_503_class() -> None:
    """List-by-labels failure surfaces as DockerUnavailable (→ 503)."""
    pool = _FakePool()
    docker = _FakeDocker()
    team_id = str(uuid.uuid4())
    docker.containers.list_raises = OSError("connection refused")

    with pytest.raises(DockerUnavailable):
        await ensure_team_mirror(pool, docker, team_id)


# ---------------------------------------------------------------------------
# reap_team_mirror
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reap_happy_path_stops_and_nulls_container_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Existing container → stop+delete; row's container_id NULLed."""
    pool = _FakePool()
    team_id = str(uuid.uuid4())
    pool.row_by_team[team_id] = _FakeRow(
        id=uuid.uuid4(),
        team_id=uuid.UUID(team_id),
        volume_path=_team_mirror_volume_name(team_id),
        container_id="liveid1234567890",
        last_started_at=datetime.datetime.now(datetime.timezone.utc),
        last_idle_at=datetime.datetime.now(datetime.timezone.utc),
        always_on=False,
    )
    docker = _FakeDocker()
    docker.containers.list_results = [
        _FakeListedContainer("liveid1234567890abcdef", running=True)
    ]
    handle = _FakeContainerHandle("liveid1234567890abcdef")
    docker.containers.get_handle = handle

    with caplog.at_level(logging.INFO, logger="orchestrator"):
        removed = await reap_team_mirror(
            pool, docker, team_id, reason="idle"
        )

    assert removed is True
    assert handle.stopped is True
    assert handle.deleted is True
    assert pool.row_by_team[team_id]["container_id"] is None

    msgs = [r.message for r in caplog.records]
    assert any(
        "team_mirror_reaped" in m and "reason=idle" in m for m in msgs
    ), msgs


@pytest.mark.asyncio
async def test_reap_no_container_returns_false_no_raise() -> None:
    """No running container for the team — idempotent no-op."""
    pool = _FakePool()
    team_id = str(uuid.uuid4())
    pool.row_by_team[team_id] = _FakeRow(
        id=uuid.uuid4(),
        team_id=uuid.UUID(team_id),
        volume_path=_team_mirror_volume_name(team_id),
        container_id=None,
        last_started_at=None,
        last_idle_at=None,
        always_on=False,
    )
    docker = _FakeDocker()
    # No containers in list — nothing to reap.
    docker.containers.list_results = []

    removed = await reap_team_mirror(
        pool, docker, team_id, reason="admin"
    )
    assert removed is False


@pytest.mark.asyncio
async def test_reap_404_race_treated_as_already_gone() -> None:
    """Container disappears between list and get — benign."""
    pool = _FakePool()
    team_id = str(uuid.uuid4())
    pool.row_by_team[team_id] = _FakeRow(
        id=uuid.uuid4(),
        team_id=uuid.UUID(team_id),
        volume_path=_team_mirror_volume_name(team_id),
        container_id="liveid",
        last_started_at=datetime.datetime.now(datetime.timezone.utc),
        last_idle_at=datetime.datetime.now(datetime.timezone.utc),
        always_on=False,
    )
    docker = _FakeDocker()
    docker.containers.list_results = [
        _FakeListedContainer("liveid", running=True)
    ]
    docker.containers.get_raises = DockerError(404, "no such container")

    removed = await reap_team_mirror(
        pool, docker, team_id, reason="idle"
    )
    assert removed is False
    # Row container_id NULLed even on the race path.
    assert pool.row_by_team[team_id]["container_id"] is None
