"""Background per-team mirror reaper (M004/S03/T02).

Owns one asyncio.Task that wakes every ``mirror_reaper_interval_seconds``,
scans the ``team_mirror_volumes`` table, and reaps any container whose row
satisfies all of:

  - ``always_on`` is False (admin opt-out wins)
  - ``container_id`` is non-NULL (something is actually running)
  - ``last_idle_at`` is older than the resolved
    ``mirror_idle_timeout_seconds`` (live system_settings lookup per tick)

Structurally separate from the user-session reaper (D022): their failure
modes differ — reaping a user session mid-fetch is benign (the user
reconnects), reaping a mirror mid-clone breaks the user's fetch, and
mid-push corrupts the auto-push receiver. Keeping them apart means a
single bug in one reaper cannot disable the other; the two share no
in-process state.

Failure handling: every iteration is wrapped in a single ``try/except
Exception`` that logs WARNING ``team_mirror_reaper_tick_failed
reason=<class>`` and sleeps to the next interval. A transient pg or
docker hiccup must NOT kill the reaper. ``asyncio.CancelledError`` is the
only legitimate exit path — the lifespan teardown raises it via
``task.cancel()``.

Public surface:
  - ``start_team_mirror_reaper(app)`` — create + return the asyncio.Task
  - ``stop_team_mirror_reaper(task)`` — cancel + await with a 5s budget

Logging discipline (MEM134): UUIDs only.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any

import asyncpg
from aiodocker.exceptions import DockerError

from orchestrator.config import settings
from orchestrator.errors import (
    DockerUnavailable,
    WorkspaceVolumeStoreUnavailable,
)
from orchestrator.team_mirror import is_row_reapable, reap_team_mirror
from orchestrator.volume_store import (
    _resolve_mirror_idle_timeout_seconds,
    get_pool,
)

if TYPE_CHECKING:
    import aiodocker
    from fastapi import FastAPI

logger = logging.getLogger("orchestrator")


_MIRROR_REAPER_INTERVAL_MIN_SECONDS = 1
_MIRROR_REAPER_INTERVAL_MAX_SECONDS = 300
_STOP_TEARDOWN_BUDGET_SECONDS = 5.0


def _resolve_mirror_reaper_interval_seconds() -> int:
    """Read MIRROR_REAPER_INTERVAL_SECONDS from env, clamped to [1, 300].

    Env-overridable so the integration suite can run with a 1s tick
    without waiting half a minute per assertion. Defaults to
    ``settings.mirror_reaper_interval_seconds`` (30s) in production.
    """
    raw = os.environ.get("MIRROR_REAPER_INTERVAL_SECONDS")
    if not raw:
        return settings.mirror_reaper_interval_seconds
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "mirror_reaper_interval_invalid raw=%s reason=ValueError fallback=%d",
            raw[:32],
            settings.mirror_reaper_interval_seconds,
        )
        return settings.mirror_reaper_interval_seconds
    if value < _MIRROR_REAPER_INTERVAL_MIN_SECONDS:
        return _MIRROR_REAPER_INTERVAL_MIN_SECONDS
    if value > _MIRROR_REAPER_INTERVAL_MAX_SECONDS:
        return _MIRROR_REAPER_INTERVAL_MAX_SECONDS
    return value


async def _select_team_mirror_rows(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """SELECT all team_mirror_volumes rows.

    No filter — the reaper considers every row, then ``is_row_reapable``
    decides per-row. Cardinality is bounded by team count (D022 — one
    mirror per team), so a full scan per tick is fine for the M004 scale.
    """
    sql = (
        "SELECT id, team_id, volume_path, container_id, last_started_at, "
        "last_idle_at, always_on FROM team_mirror_volumes"
    )
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql)
    return [dict(row) for row in rows]


async def _reap_one_tick(
    pool: asyncpg.Pool, docker: aiodocker.Docker
) -> tuple[int, int]:
    """Run a single mirror-reaper pass. Returns (scanned, reaped).

    Pulled out as a top-level coroutine so tests can drive a single tick
    deterministically without spinning the loop. Logs:

      - INFO ``mirror_idle_timeout_seconds_resolved value=<n>`` (once per
        tick, emitted from ``_resolve_mirror_idle_timeout_seconds``)
      - INFO ``team_mirror_reap_skipped team_id=<uuid> reason=<class>``
        for each row that is_row_reapable rejected
      - INFO ``team_mirror_reaped team_id=<uuid> container_id=<12>
        reason=idle`` for each successful reap (emitted from
        ``reap_team_mirror``)

    Per-row reap failures are logged-and-swallowed so a single bad row
    doesn't shadow other reaps in the same tick.
    """
    idle_timeout_seconds = await _resolve_mirror_idle_timeout_seconds(pool)

    rows = await _select_team_mirror_rows(pool)
    scanned = 0
    reaped = 0

    for row in rows:
        scanned += 1
        team_id = str(row["team_id"])
        reapable, reason = is_row_reapable(
            row, idle_timeout_seconds=idle_timeout_seconds
        )
        if not reapable:
            # Only log the skip for rows that actually had a chance —
            # logging "no_container" for every row in the world would
            # be noisy. always_on / recent_activity are interesting;
            # the cold-row "no_container" / "no_last_idle_at" are not.
            if reason in ("always_on", "recent_activity"):
                logger.info(
                    "team_mirror_reap_skipped team_id=%s reason=%s",
                    team_id,
                    reason,
                )
            continue

        try:
            removed = await reap_team_mirror(
                pool, docker, team_id, reason="idle"
            )
        except DockerUnavailable as exc:
            # Per-row docker failure: log + continue the scan. The next
            # tick retries; the row stays as the durable source of
            # truth.
            logger.warning(
                "team_mirror_reap_failed team_id=%s reason=%s",
                team_id,
                str(exc)[:120],
            )
            continue
        except DockerError as exc:
            logger.warning(
                "team_mirror_reap_failed team_id=%s reason=DockerError:%s",
                team_id,
                exc.status,
            )
            continue
        except WorkspaceVolumeStoreUnavailable as exc:
            logger.warning(
                "team_mirror_reap_failed team_id=%s reason=%s",
                team_id,
                str(exc)[:120],
            )
            continue
        except OSError as exc:
            logger.warning(
                "team_mirror_reap_failed team_id=%s reason=%s",
                team_id,
                type(exc).__name__,
            )
            continue
        if removed:
            reaped += 1

    return scanned, reaped


async def mirror_reaper_loop(app: FastAPI) -> None:
    """Run the mirror reaper until cancelled.

    The loop sleeps FIRST so a fresh boot does not immediately scan a
    not-yet-warm pg. Each tick is wrapped in try/except so transient
    errors never kill the task — only ``asyncio.CancelledError`` exits
    the loop.
    """
    interval = _resolve_mirror_reaper_interval_seconds()
    logger.info("team_mirror_reaper_started interval_seconds=%d", interval)
    while True:
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        try:
            docker = getattr(app.state, "docker", None)
            if docker is None:
                logger.warning(
                    "team_mirror_reaper_tick_skipped "
                    "reason=docker_handle_unavailable"
                )
                continue
            try:
                pool = get_pool()
            except WorkspaceVolumeStoreUnavailable:
                logger.warning(
                    "team_mirror_reaper_tick_skipped "
                    "reason=pg_pool_unavailable"
                )
                continue
            scanned, reaped = await _reap_one_tick(pool, docker)
            logger.info(
                "team_mirror_reaper_tick scanned=%d reaped=%d",
                scanned,
                reaped,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            # MEM168/MEM176: sessions.py wraps DockerError + OSError into
            # DockerUnavailable; pg failures wrap as
            # WorkspaceVolumeStoreUnavailable. Catching the broad
            # Exception here is the safety net behind those — any
            # programmer error or otherwise-unhandled class lands as
            # ``team_mirror_reaper_tick_failed`` so the loop survives.
            logger.warning(
                "team_mirror_reaper_tick_failed reason=%s",
                type(exc).__name__,
            )


def start_team_mirror_reaper(app: FastAPI) -> asyncio.Task[None]:
    """Spawn the team-mirror reaper task.

    Caller stores the returned handle on app.state so
    ``stop_team_mirror_reaper`` can cancel it on lifespan teardown.
    """
    return asyncio.create_task(
        mirror_reaper_loop(app), name="team_mirror_reaper_loop"
    )


async def stop_team_mirror_reaper(task: asyncio.Task[None] | None) -> None:
    """Cancel the team-mirror reaper task and await its exit with a 5s budget.

    ``CancelledError`` is swallowed — the success path. Any other
    exception is logged but never re-raised; the lifespan teardown must
    not fail because the reaper had a bad day on the way out (MEM190).
    """
    if task is None:
        return
    if task.done():
        return
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=_STOP_TEARDOWN_BUDGET_SECONDS)
    except asyncio.CancelledError:
        pass
    except TimeoutError:
        logger.warning(
            "team_mirror_reaper_stop_timeout budget_seconds=%.1f",
            _STOP_TEARDOWN_BUDGET_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "team_mirror_reaper_stop_failed reason=%s", type(exc).__name__
        )


__all__ = [
    "start_team_mirror_reaper",
    "stop_team_mirror_reaper",
    "mirror_reaper_loop",
    "_reap_one_tick",
    "_resolve_mirror_reaper_interval_seconds",
    "_select_team_mirror_rows",
]
