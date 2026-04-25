"""Background idle-session reaper for the orchestrator (S04 / T02).

Owns one asyncio.Task that wakes every `reaper_interval_seconds`, scans
the Redis session registry, and applies the D018 two-phase liveness check:
a session is reapable iff its Redis `last_activity` is older than the
admin-tunable `idle_timeout_seconds` AND the in-process AttachMap reports
no live WS attach. Reapable sessions are killed via tmux + dropped from
Redis. After all kills, any container the reaper just emptied (zero Redis
sessions referencing it AND `tmux ls` reports zero sessions) is stopped
and removed — the workspace_volume row + .img persist (D015 invariant).

Failure handling: every iteration is wrapped in a single `try/except
Exception` that logs WARNING `reaper_tick_failed reason=<class>` and
sleeps to the next interval. A transient Redis or Docker error must NOT
kill the reaper. `asyncio.CancelledError` is the only legitimate exit
path — the lifespan teardown raises it via `task.cancel()`.

Public surface:
  - `start_reaper(app)` — create + return the asyncio.Task
  - `stop_reaper(task)` — cancel + await with a 5s teardown budget

Logging discipline (MEM134): UUIDs only — never log emails or team
slugs. Container ids are truncated to the conventional first-12 form.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict
from typing import TYPE_CHECKING

from aiodocker.exceptions import DockerError

from orchestrator.attach_map import get_attach_map
from orchestrator.errors import DockerUnavailable
from orchestrator.redis_client import get_registry
from orchestrator.sessions import (
    _find_container_by_labels,
    kill_tmux_session,
    list_tmux_sessions,
)
from orchestrator.volume_store import _resolve_idle_timeout_seconds, get_pool

if TYPE_CHECKING:
    import aiodocker
    from fastapi import FastAPI

logger = logging.getLogger("orchestrator")


_REAPER_INTERVAL_DEFAULT_SECONDS = 30
_REAPER_INTERVAL_MIN_SECONDS = 1
_REAPER_INTERVAL_MAX_SECONDS = 300
_STOP_TEARDOWN_BUDGET_SECONDS = 5.0
_CONTAINER_STOP_TIMEOUT_SECONDS = 5


def _resolve_reaper_interval_seconds() -> int:
    """Read REAPER_INTERVAL_SECONDS from env, clamped to [1, 300].

    Env-overridable so the integration suite can run with a 1s tick
    without waiting half a minute per assertion. Defaults to 30s in
    production where the cost of an extra Redis scan dominates the cost
    of slightly-stale session reaping.
    """
    raw = os.environ.get("REAPER_INTERVAL_SECONDS")
    if not raw:
        return _REAPER_INTERVAL_DEFAULT_SECONDS
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "reaper_interval_invalid raw=%s reason=ValueError fallback=%d",
            raw[:32],
            _REAPER_INTERVAL_DEFAULT_SECONDS,
        )
        return _REAPER_INTERVAL_DEFAULT_SECONDS
    if value < _REAPER_INTERVAL_MIN_SECONDS:
        return _REAPER_INTERVAL_MIN_SECONDS
    if value > _REAPER_INTERVAL_MAX_SECONDS:
        return _REAPER_INTERVAL_MAX_SECONDS
    return value


async def _reap_one_tick(
    docker: aiodocker.Docker,
) -> tuple[int, int, int]:
    """Run a single reaper pass. Returns (scanned, killed, reaped_containers).

    Pulled out as a top-level coroutine so tests can drive a single tick
    deterministically without spinning the loop.
    """
    registry = get_registry()
    pool = get_pool()
    attach_map = get_attach_map()

    idle_timeout_seconds = await _resolve_idle_timeout_seconds(pool)

    now = time.time()
    scanned = 0
    killed = 0
    # container_id → (user_id, team_id) — populated only for containers we
    # actually killed at least one session on this tick. We only check
    # `list_tmux_sessions` for those, not for every container in the world.
    candidates_for_reap: dict[str, tuple[str, str]] = {}
    surviving_by_container: dict[str, int] = defaultdict(int)

    async for session_id, record in registry.scan_session_keys():
        scanned += 1
        last_activity = record.get("last_activity")
        if not isinstance(last_activity, (int, float)):
            # Defensive — a malformed record can't be aged out reliably.
            # Skip and let observability surface the surprise.
            logger.warning(
                "reaper_skipped_bad_record session_id=%s reason=missing_last_activity",
                session_id,
            )
            continue
        idle = now - float(last_activity)
        container_id = record.get("container_id")
        user_id = record.get("user_id")
        team_id = record.get("team_id")

        if idle <= idle_timeout_seconds:
            if container_id:
                surviving_by_container[str(container_id)] += 1
            continue
        if await attach_map.is_attached(session_id):
            # Two-phase check (D018): a stale Redis last_activity does NOT
            # justify killing a tmux session that has a live WS attach.
            if container_id:
                surviving_by_container[str(container_id)] += 1
            continue

        # Reapable. Kill the tmux session first, then drop the Redis row.
        if container_id:
            try:
                await kill_tmux_session(docker, str(container_id), session_id)
            except DockerUnavailable as exc:
                # sessions.py wraps DockerError + OSError into DockerUnavailable;
                # the docker daemon being unreachable for a single session is
                # not a reaper-killer — log and proceed with the Redis cleanup
                # so the row doesn't permanently block the reaper.
                logger.warning(
                    "reaper_kill_failed session_id=%s reason=%s",
                    session_id,
                    str(exc)[:120],
                )
            except DockerError as exc:
                logger.warning(
                    "reaper_kill_failed session_id=%s reason=DockerError:%s",
                    session_id,
                    exc.status,
                )
            except OSError as exc:
                logger.warning(
                    "reaper_kill_failed session_id=%s reason=%s",
                    session_id,
                    type(exc).__name__,
                )
        try:
            await registry.delete_session(session_id)
        except Exception as exc:  # noqa: BLE001
            # Mirror the kill path — log and move on. A failed delete leaves
            # the row to be retried next tick (idempotent: kill_tmux_session
            # returns False on the second call, delete_session is no-op on
            # missing).
            logger.warning(
                "reaper_delete_session_failed session_id=%s reason=%s",
                session_id,
                type(exc).__name__,
            )
            continue

        killed += 1
        logger.info(
            "reaper_killed_session session_id=%s reason=idle_no_attach",
            session_id,
        )
        if container_id and user_id and team_id:
            candidates_for_reap[str(container_id)] = (str(user_id), str(team_id))

    # Container reap pass: only for containers the reaper just emptied.
    reaped_containers = 0
    for container_id, (user_id, team_id) in candidates_for_reap.items():
        # If a sibling Redis session for this container is still around
        # (newer activity, or registered for a different session_id during
        # this tick), skip the reap — multi-tmux containers stay alive
        # until ALL sessions are gone (R008).
        if surviving_by_container.get(container_id, 0) > 0:
            continue
        try:
            tmux_alive = await list_tmux_sessions(docker, container_id)
        except DockerUnavailable as exc:
            logger.warning(
                "reaper_tmux_ls_failed container_id=%s reason=%s",
                container_id[:12],
                str(exc)[:120],
            )
            continue
        except DockerError as exc:
            logger.warning(
                "reaper_tmux_ls_failed container_id=%s reason=DockerError:%s",
                container_id[:12],
                exc.status,
            )
            continue
        except OSError as exc:
            logger.warning(
                "reaper_tmux_ls_failed container_id=%s reason=%s",
                container_id[:12],
                type(exc).__name__,
            )
            continue
        if tmux_alive:
            # Some tmux session survived — orphaned-state path (the reaper
            # killed every Redis-known session but tmux still has one). Do
            # NOT remove the container; let the next tick reconcile.
            continue

        # Label-scoped lookup: confirms the container we're about to remove
        # is actually a managed workspace container for the (user, team)
        # we recorded at scan-time.
        try:
            target = await _find_container_by_labels(docker, user_id, team_id)
        except DockerUnavailable as exc:
            logger.warning(
                "reaper_container_lookup_failed container_id=%s reason=%s",
                container_id[:12],
                str(exc)[:120],
            )
            continue
        if target is None or target != container_id:
            # Either the container is already gone (good — no-op) or it's
            # a different container than the one we recorded (e.g. the user
            # already re-provisioned). Don't clobber.
            continue
        try:
            container = await docker.containers.get(container_id)
            await container.stop(timeout=_CONTAINER_STOP_TIMEOUT_SECONDS)
            await container.delete(force=True)
        except DockerError as exc:
            # 404 from `containers.get` after `_find_container_by_labels`
            # said it existed is a benign race — someone (a parallel
            # delete?) cleaned it up between the two calls. Treat as
            # "already gone" rather than a failure.
            if exc.status == 404:
                continue
            logger.warning(
                "reaper_container_remove_failed container_id=%s reason=DockerError:%s",
                container_id[:12],
                exc.status,
            )
            continue
        except OSError as exc:
            logger.warning(
                "reaper_container_remove_failed container_id=%s reason=%s",
                container_id[:12],
                type(exc).__name__,
            )
            continue
        reaped_containers += 1
        logger.info(
            "reaper_reaped_container container_id=%s user_id=%s team_id=%s "
            "reason=last_session_killed",
            container_id[:12],
            user_id,
            team_id,
        )

    return scanned, killed, reaped_containers


async def reaper_loop(app: FastAPI) -> None:
    """Run the reaper until cancelled.

    The loop sleeps FIRST so a fresh boot does not immediately scan a
    not-yet-warm Redis (and so tests that create_task can deterministically
    register sessions before the first tick runs). Each tick is wrapped in
    try/except so transient errors never kill the task — only
    `asyncio.CancelledError` exits the loop.
    """
    interval = _resolve_reaper_interval_seconds()
    logger.info("reaper_started interval_seconds=%d", interval)
    while True:
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        try:
            docker = getattr(app.state, "docker", None)
            if docker is None:
                # Boot path with SKIP_IMAGE_PULL_ON_BOOT=1 (test-only) leaves
                # docker unset. The reaper is structurally a no-op without
                # a Docker handle; log once per tick and skip.
                logger.warning(
                    "reaper_tick_skipped reason=docker_handle_unavailable"
                )
                continue
            scanned, killed, reaped_containers = await _reap_one_tick(docker)
            logger.info(
                "reaper_tick scanned=%d killed=%d reaped_containers=%d",
                scanned,
                killed,
                reaped_containers,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            # Any other failure (Redis hiccup, pg pool issue, Docker
            # daemon hiccup, unexpected programmer error) MUST NOT kill
            # the reaper task. Log and let the next tick try again.
            logger.warning(
                "reaper_tick_failed reason=%s",
                type(exc).__name__,
            )


def start_reaper(app: FastAPI) -> asyncio.Task[None]:
    """Spawn the reaper task. Caller stores the returned handle so
    `stop_reaper` can cancel it on lifespan teardown.
    """
    return asyncio.create_task(reaper_loop(app), name="reaper_loop")


async def stop_reaper(task: asyncio.Task[None] | None) -> None:
    """Cancel the reaper task and await its exit with a 5s budget.

    `CancelledError` is swallowed — that is the success path. Any other
    exception is logged but never re-raised; the lifespan teardown must
    not fail because the reaper had a bad day on the way out.
    """
    if task is None:
        return
    if task.done():
        return
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=_STOP_TEARDOWN_BUDGET_SECONDS)
    except asyncio.CancelledError:
        # Expected — the cancel propagated through the loop's sleep.
        pass
    except TimeoutError:
        logger.warning("reaper_stop_timeout budget_seconds=%.1f",
                       _STOP_TEARDOWN_BUDGET_SECONDS)
    except Exception as exc:  # noqa: BLE001
        logger.warning("reaper_stop_failed reason=%s", type(exc).__name__)


__all__ = [
    "start_reaper",
    "stop_reaper",
    "reaper_loop",
    "_reap_one_tick",
    "_resolve_reaper_interval_seconds",
]
