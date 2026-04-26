"""Per-team mirror container provisioning + reap (M004/S03/T02).

One mirror container per team. Same workspace image as user containers (D022)
running ``git daemon --base-path=/repos --export-all --reuseaddr
--enable=receive-pack`` on port 9418 (D023). User containers clone/push by
``git://team-mirror-<first8-team>:9418/<project_id>.git`` over the compose
network — credential-free, name-resolved by Docker DNS.

The orchestrator's ensure path is idempotent on (team_id):

  - SELECT/INSERT a ``team_mirror_volumes`` row (T01 schema; UNIQUE on
    team_id, FK CASCADE on team delete)
  - look up an existing container by labels; reuse if running
  - otherwise create_or_replace + start the container; on the 409
    name-collision race, fall back to filter-list lookup (mirrors
    sessions.provision_container)
  - update the row's ``container_id`` + ``last_started_at`` + bump
    ``last_idle_at`` so the reaper has a fresh activity baseline

Reap mirrors the user-session reaper's container-remove path: stop with a
short timeout, force-delete, NULL ``container_id``, set ``last_idle_at`` —
the row + the per-team Docker volume persist (the volume is named, so the
next ensure remounts the same /repos).

Failure surface (mirrors sessions.py exactly):

  - DockerError + OSError → DockerUnavailable (existing 503 handler)
  - asyncpg errors → WorkspaceVolumeStoreUnavailable (existing 503 handler)
  - 409 on create → benign, fall through to filter-list lookup

Logging discipline (MEM134): UUIDs only. Container ids truncated to the
conventional first-12. No GitHub tokens flow through this module — those
land in S04's clone/push paths.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

import aiodocker
import asyncpg
from aiodocker.exceptions import DockerError

from orchestrator.config import settings
from orchestrator.errors import (
    DockerUnavailable,
    WorkspaceVolumeStoreUnavailable,
)

logger = logging.getLogger("orchestrator")


# Docker network the mirror container attaches to so user containers can
# resolve it by name. Compose builds the network as ``<project>_default``;
# under ``docker-compose -p perpetuity`` that becomes ``perpetuity_default``.
# Override via env if a deployment uses a different compose project name.
_MIRROR_NETWORK = "perpetuity_default"

# Container port the in-container ``git daemon`` listens on (D023). Not
# published to the host; intra-network only.
_MIRROR_PORT = 9418

# Mountpoint inside the mirror container — git daemon's ``--base-path``.
_MIRROR_REPOS_DIR = "/repos"

# Per-team Docker volume name prefix. Volumes survive container reaps so the
# bare-repo state persists across restarts; uuid-keyed by construction.
_MIRROR_VOLUME_PREFIX = "perpetuity-team-mirror-"


def _team_mirror_container_name(team_id: str) -> str:
    """``team-mirror-<first8-team>`` — DNS alias on the compose network.

    Docker name validation: 2-255 chars matching ``[a-zA-Z0-9][a-zA-Z0-9_.-]+``.
    Team UUIDs are hex with dashes; first 8 chars after stripping dashes are
    pure hex so we're safe.
    """
    clean = team_id.replace("-", "")
    return f"team-mirror-{clean[:8]}"


def _team_mirror_volume_name(team_id: str) -> str:
    """``perpetuity-team-mirror-<first8-team>`` — named docker volume.

    Named (not bind-mount) so the orchestrator host doesn't need a per-team
    bind path provisioned up-front, and so ``docker volume rm`` is the
    operator's one-handle teardown when a team is decommissioned.
    """
    clean = team_id.replace("-", "")
    return f"{_MIRROR_VOLUME_PREFIX}{clean[:8]}"


def _network_addr(team_id: str) -> str:
    """``team-mirror-<first8>:9418`` — what user containers will dial.

    Returned to the API caller verbatim so the backend can stash it on the
    team row (or hand it to the user container's ``.git/config``) without
    knowing the construction rule.
    """
    return f"{_team_mirror_container_name(team_id)}:{_MIRROR_PORT}"


def _build_team_mirror_container_config(
    team_id: str, volume_name: str
) -> dict[str, Any]:
    """Compose the JSON config for ``containers/create``.

    Differences from ``sessions._build_container_config``:
      - ``Cmd`` runs ``git daemon`` directly (D023) — no tmux, no sleep.
      - ``ExposedPorts`` declares 9418/tcp so other compose services can
        ``docker network inspect`` and see the route. Not published.
      - ``Mounts`` uses a named docker volume mounted at ``/repos`` rather
        than a host bind-mount. Survives reaps, uuid-keyed.
      - Labels add ``perpetuity.team_mirror=true`` so list-by-label can
        target mirror containers without dragging in user-session ones.
      - ``HostConfig.NetworkMode`` attaches to the compose network so user
        containers can resolve us by name (DNS alias = container name).
      - Same workspace image as user containers (D022) so any image the
        team's user containers can run, the mirror can run too.

    The ``--reuseaddr`` flag lets git daemon rebind 9418 immediately after
    a process restart inside the same container — defensive for the
    nascent-restart case (we never restart inside the container today,
    but the flag is cheap insurance).
    """
    return {
        "Image": settings.workspace_image,
        # `git daemon` foreground + bind to all interfaces in the
        # container so the docker bridge can route to it. --export-all
        # exports every repo under --base-path without needing a per-repo
        # `git-daemon-export-ok` marker; --enable=receive-pack permits
        # `git push` from user containers (S04 will exercise that).
        "Cmd": [
            "git",
            "daemon",
            "--base-path=/repos",
            "--export-all",
            "--reuseaddr",
            "--enable=receive-pack",
            f"--port={_MIRROR_PORT}",
            "--listen=0.0.0.0",
        ],
        "ExposedPorts": {f"{_MIRROR_PORT}/tcp": {}},
        "Labels": {
            "team_id": team_id,
            "perpetuity.managed": "true",
            "perpetuity.team_mirror": "true",
        },
        "HostConfig": {
            "Memory": _parse_mem_limit(settings.container_mem_limit),
            "PidsLimit": settings.container_pids_limit,
            "NanoCpus": settings.container_nano_cpus,
            # Named volume — survives container reap. Docker auto-creates
            # the volume on container create if it doesn't exist.
            "Mounts": [
                {
                    "Type": "volume",
                    "Source": volume_name,
                    "Target": _MIRROR_REPOS_DIR,
                }
            ],
            # No restart policy — the mirror reaper controls lifecycle.
            "RestartPolicy": {"Name": "no"},
            # Attach to the compose network so user containers resolve
            # us by name.
            "NetworkMode": _MIRROR_NETWORK,
        },
        "Tty": False,
        "OpenStdin": False,
    }


def _parse_mem_limit(value: str) -> int:
    """Convert "2g"/"512m"/"123" into bytes for HostConfig.Memory.

    Duplicate of sessions._parse_mem_limit kept inline so this module has
    no cross-dependency on sessions.py beyond the label-search helper.
    """
    s = value.strip().lower()
    if not s:
        raise ValueError("empty memory limit")
    multiplier = 1
    if s.endswith("k"):
        multiplier, s = 1024, s[:-1]
    elif s.endswith("m"):
        multiplier, s = 1024 * 1024, s[:-1]
    elif s.endswith("g"):
        multiplier, s = 1024 * 1024 * 1024, s[:-1]
    return int(s) * multiplier


async def _find_team_mirror_container(
    docker: aiodocker.Docker, team_id: str
) -> str | None:
    """Return the running mirror container id for ``team_id``, or None.

    Filters by both ``team_id`` and ``perpetuity.team_mirror=true`` so a
    label-collision with a user-session container (which carries the
    same ``team_id`` but NOT ``perpetuity.team_mirror=true``) cannot
    return a wrong container. Includes stopped containers in the listing
    so we can see them, but only running ones are returned — a stopped
    container is treated as "create a fresh one" by the ensure path
    (matches sessions._find_container_by_labels).
    """
    filters = json.dumps(
        {
            "label": [
                f"team_id={team_id}",
                "perpetuity.team_mirror=true",
                "perpetuity.managed=true",
            ]
        }
    )
    try:
        results = await docker.containers.list(all=True, filters=filters)
    except DockerError as exc:
        raise DockerUnavailable(
            f"docker_list_failed:{exc.status}:{exc.message}"
        ) from exc
    except OSError as exc:
        raise DockerUnavailable(
            f"docker_unreachable:{type(exc).__name__}"
        ) from exc
    for container in results:
        state = container._container.get("State")
        if isinstance(state, str):
            running = state == "running"
        elif isinstance(state, dict):
            running = state.get("Running", False)
        else:
            running = False
        if running:
            return container.id
    return None


async def _get_or_insert_team_mirror_row(
    pool: asyncpg.Pool, team_id: str, volume_name: str
) -> dict[str, Any]:
    """Find-or-create the team_mirror_volumes row for ``team_id``.

    Returns the row as a dict so the caller can treat the new-row and
    existing-row paths identically. The row's ``volume_path`` is set on
    INSERT to the docker volume name (uuid-keyed by construction; safe
    to log) — a future schema iteration may switch to a real host path
    if we drop named-volumes for bind-mounts, but the column shape is
    stable today.

    Concurrent ensure-spinup race: two simultaneous calls for the same
    team can both miss the SELECT and both attempt INSERT. The UNIQUE
    constraint on ``team_id`` makes the loser raise UniqueViolationError;
    we catch it and refetch (mirrors volume_store.create_volume).
    """
    sql_select = (
        "SELECT id, team_id, volume_path, container_id, last_started_at, "
        "last_idle_at, always_on FROM team_mirror_volumes WHERE team_id = $1"
    )
    sql_insert = (
        "INSERT INTO team_mirror_volumes "
        "(id, team_id, volume_path, created_at) "
        "VALUES ($1, $2, $3, NOW()) "
        "RETURNING id, team_id, volume_path, container_id, last_started_at, "
        "last_idle_at, always_on"
    )
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(sql_select, uuid.UUID(team_id))
            if row is not None:
                return dict(row)
            new_id = uuid.uuid4()
            try:
                row = await conn.fetchrow(
                    sql_insert, new_id, uuid.UUID(team_id), volume_name
                )
            except asyncpg.UniqueViolationError:
                # Concurrent-ensure tie-break: refetch the winner's row.
                logger.info(
                    "team_mirror_row_create_race_detected team_id=%s",
                    team_id,
                )
                row = await conn.fetchrow(sql_select, uuid.UUID(team_id))
                if row is None:
                    raise WorkspaceVolumeStoreUnavailable(
                        "team_mirror_row_race_no_winner_found"
                    )
    except (
        OSError,
        asyncpg.PostgresError,
        asyncpg.InterfaceError,
    ) as exc:
        logger.warning(
            "pg_unreachable op=team_mirror_get_or_insert reason=%s",
            type(exc).__name__,
        )
        raise WorkspaceVolumeStoreUnavailable(
            f"team_mirror_row_failed:{type(exc).__name__}"
        ) from exc
    if row is None:
        raise WorkspaceVolumeStoreUnavailable(
            "team_mirror_row_returning_none"
        )
    return dict(row)


async def _update_team_mirror_after_start(
    pool: asyncpg.Pool, team_id: str, container_id: str
) -> None:
    """Stamp ``container_id`` + ``last_started_at`` + ``last_idle_at`` on
    the row after the container is up.

    ``last_idle_at`` gets bumped to NOW() on start so the reaper has a
    fresh activity baseline — without this, the very first reaper tick
    after a cold-start could find ``last_idle_at IS NULL`` and reap an
    actively-warming mirror.
    """
    sql = (
        "UPDATE team_mirror_volumes SET container_id = $1, "
        "last_started_at = NOW(), last_idle_at = NOW() "
        "WHERE team_id = $2"
    )
    try:
        async with pool.acquire() as conn:
            await conn.execute(sql, container_id, uuid.UUID(team_id))
    except (
        OSError,
        asyncpg.PostgresError,
        asyncpg.InterfaceError,
    ) as exc:
        # The container is up; the row is stale. Surface as 503 so the
        # caller retries — a stale row would otherwise leave the reaper
        # blind to the live container.
        logger.warning(
            "pg_unreachable op=team_mirror_update_after_start reason=%s",
            type(exc).__name__,
        )
        raise WorkspaceVolumeStoreUnavailable(
            f"team_mirror_update_after_start_failed:{type(exc).__name__}"
        ) from exc


async def _update_team_mirror_after_reap(
    pool: asyncpg.Pool, team_id: str
) -> None:
    """NULL ``container_id`` + bump ``last_idle_at`` after a successful reap.

    Volume_path stays put — the volume itself is not removed by reap, so
    the next ensure-spinup can remount the same /repos. ``always_on`` is
    untouched (admin-owned).
    """
    sql = (
        "UPDATE team_mirror_volumes SET container_id = NULL, "
        "last_idle_at = NOW() "
        "WHERE team_id = $1"
    )
    try:
        async with pool.acquire() as conn:
            await conn.execute(sql, uuid.UUID(team_id))
    except (
        OSError,
        asyncpg.PostgresError,
        asyncpg.InterfaceError,
    ) as exc:
        # Mirror update failure on reap is logged-and-swallowed by the
        # reaper itself; the route handler raises so the admin sees it.
        logger.warning(
            "pg_unreachable op=team_mirror_update_after_reap reason=%s",
            type(exc).__name__,
        )
        raise WorkspaceVolumeStoreUnavailable(
            f"team_mirror_update_after_reap_failed:{type(exc).__name__}"
        ) from exc


async def ensure_team_mirror(
    pool: asyncpg.Pool,
    docker: aiodocker.Docker,
    team_id: str,
    *,
    trigger: str = "ensure",
) -> dict[str, Any]:
    """Look-up-or-create the team's mirror container; idempotent.

    Returns ``{container_id, network_addr}``. ``network_addr`` is the
    DNS:port that user containers should dial (D022/D023).

    The path:
      1. Find-or-insert the team_mirror_volumes row (creates the row on
         first ensure; refetches on concurrent-ensure race).
      2. Look for a running container with matching labels.
         - Found → log ``team_mirror_reused``, return.
         - Not found → fall through to create.
      3. ``create_or_replace`` the container by name + start it. On 409
         (concurrent ensure raced us to ``create``) refetch by labels.
      4. Stamp container_id + last_started_at + last_idle_at on the row
         and emit ``team_mirror_started``.

    Failure-mode contract (matches sessions.provision_container):
      - Docker daemon down → DockerUnavailable → 503 docker_unavailable
      - Postgres down → WorkspaceVolumeStoreUnavailable → 503
        workspace_volume_store_unavailable
    """
    volume_name = _team_mirror_volume_name(team_id)
    row = await _get_or_insert_team_mirror_row(pool, team_id, volume_name)

    existing = await _find_team_mirror_container(docker, team_id)
    if existing is not None:
        logger.info(
            "team_mirror_reused team_id=%s container_id=%s",
            team_id,
            existing[:12],
        )
        # Even on reuse, bump last_idle_at so an admin-triggered ensure
        # against an already-running mirror resets the idle clock —
        # otherwise an active mirror could be reaped between the next
        # tick and the next user clone.
        await _update_team_mirror_after_start(pool, team_id, existing)
        return {
            "container_id": existing,
            "network_addr": _network_addr(team_id),
            "reused": True,
            "row_id": str(row["id"]),
        }

    config = _build_team_mirror_container_config(team_id, volume_name)
    name = _team_mirror_container_name(team_id)
    try:
        container = await docker.containers.create_or_replace(
            name=name, config=config
        )
        await container.start()
    except DockerError as exc:
        if exc.status == 409:
            # Concurrent ensure raced us to `create`. Their container
            # wins; refetch.
            existing = await _find_team_mirror_container(docker, team_id)
            if existing is not None:
                logger.info(
                    "team_mirror_create_race_detected team_id=%s "
                    "container_id=%s",
                    team_id,
                    existing[:12],
                )
                await _update_team_mirror_after_start(pool, team_id, existing)
                return {
                    "container_id": existing,
                    "network_addr": _network_addr(team_id),
                    "reused": True,
                    "row_id": str(row["id"]),
                }
        logger.error(
            "team_mirror_create_failed team_id=%s reason=%s",
            team_id,
            f"{exc.status}:{exc.message}",
        )
        raise DockerUnavailable(
            f"team_mirror_create_failed:{exc.status}:{exc.message}"
        ) from exc
    except OSError as exc:
        raise DockerUnavailable(
            f"docker_unreachable:{type(exc).__name__}"
        ) from exc

    await _update_team_mirror_after_start(pool, team_id, container.id)

    logger.info(
        "team_mirror_started team_id=%s container_id=%s "
        "network_addr=%s trigger=%s",
        team_id,
        container.id[:12],
        _network_addr(team_id),
        trigger,
    )
    return {
        "container_id": container.id,
        "network_addr": _network_addr(team_id),
        "reused": False,
        "row_id": str(row["id"]),
    }


_CONTAINER_STOP_TIMEOUT_SECONDS = 5


async def reap_team_mirror(
    pool: asyncpg.Pool,
    docker: aiodocker.Docker,
    team_id: str,
    *,
    reason: str,
) -> bool:
    """Stop+remove the team's mirror container; NULL container_id on the row.

    Returns True if a container was actually removed, False if it was
    already gone (idempotent on the no-container path — same shape as
    the user-session reaper's 404-race handling, MEM176).

    The volume is intentionally NOT removed: the next ensure-spinup
    remounts the same ``/repos`` so bare-repo state persists. Volume
    cleanup is a manual operator step on team decommission.

    Caller passes ``reason`` for the structured log line — typically
    ``"idle"`` from the reaper, ``"admin"`` from the route handler.
    """
    existing = await _find_team_mirror_container(docker, team_id)
    if existing is None:
        # Already gone. Sync the row anyway (defensive — a manual
        # `docker rm` on the host could have left the row stale).
        try:
            await _update_team_mirror_after_reap(pool, team_id)
        except WorkspaceVolumeStoreUnavailable:
            # Don't mask the no-container path on a pg hiccup; the
            # reaper's per-tick swallow handles it on the next tick.
            pass
        logger.info(
            "team_mirror_reap_noop team_id=%s reason=%s",
            team_id,
            reason,
        )
        return False

    try:
        container = await docker.containers.get(existing)
        await container.stop(timeout=_CONTAINER_STOP_TIMEOUT_SECONDS)
        await container.delete(force=True)
    except DockerError as exc:
        if exc.status == 404:
            # Benign race — container disappeared between list and get.
            await _update_team_mirror_after_reap(pool, team_id)
            logger.info(
                "team_mirror_reap_race team_id=%s container_id=%s",
                team_id,
                existing[:12],
            )
            return False
        logger.warning(
            "team_mirror_reap_failed team_id=%s container_id=%s "
            "reason=DockerError:%s",
            team_id,
            existing[:12],
            exc.status,
        )
        raise DockerUnavailable(
            f"team_mirror_reap_failed:{exc.status}:{exc.message}"
        ) from exc
    except OSError as exc:
        raise DockerUnavailable(
            f"docker_unreachable:{type(exc).__name__}"
        ) from exc

    await _update_team_mirror_after_reap(pool, team_id)
    logger.info(
        "team_mirror_reaped team_id=%s container_id=%s reason=%s",
        team_id,
        existing[:12],
        reason,
    )
    return True


# Module-level helper used by the reaper module to compute "is this row
# idle". Exposed here (rather than inlined in the reaper) so test code
# can drive the deterministic boundary case without spinning up a row.
def is_row_reapable(
    row: dict[str, Any],
    *,
    idle_timeout_seconds: int,
    now_epoch: float | None = None,
) -> tuple[bool, str]:
    """Decide whether the reaper should reap ``row`` on this tick.

    Returns ``(reapable, reason)`` where ``reason`` describes either why
    the reap should fire (``idle``) or why it should be skipped
    (``always_on``, ``no_container``, ``recent_activity``,
    ``no_last_idle_at``). The string is logged verbatim by the reaper.

    ``now_epoch`` is injectable so tests can pin the clock. Production
    callers leave it None and we read ``time.time()``.
    """
    if row.get("always_on"):
        return False, "always_on"
    if not row.get("container_id"):
        return False, "no_container"
    last_idle_at = row.get("last_idle_at")
    if last_idle_at is None:
        # Cold row — no activity baseline yet. Treat as not-reapable;
        # ensure-spinup stamps last_idle_at, so any row in the wild
        # without it has never been started by us.
        return False, "no_last_idle_at"
    now = now_epoch if now_epoch is not None else time.time()
    # asyncpg returns TIMESTAMPTZ as datetime; .timestamp() is epoch.
    try:
        last_epoch = last_idle_at.timestamp()
    except AttributeError:
        # Defensive — a future schema migration could change the type.
        return False, "no_last_idle_at"
    if (now - last_epoch) <= idle_timeout_seconds:
        return False, "recent_activity"
    return True, "idle"


__all__ = [
    "ensure_team_mirror",
    "reap_team_mirror",
    "is_row_reapable",
    "_team_mirror_container_name",
    "_team_mirror_volume_name",
    "_build_team_mirror_container_config",
    "_find_team_mirror_container",
    "_network_addr",
    "_MIRROR_PORT",
    "_MIRROR_REPOS_DIR",
]
