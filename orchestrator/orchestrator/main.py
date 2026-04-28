"""Orchestrator FastAPI app.

T01 scope landed minimal /v1/health. T02 wires:
  - shared-secret middleware (HTTP) + helper (WS)
  - Redis client singleton bound to a lifespan
  - image-pull-on-boot via aiodocker (boot blocker on failure, per D018)
  - /v1/health now reports {status, image_present} so the compose healthcheck
    flips red when Docker is unreachable

T03/T04 add session lifecycle and the WS bridge on top of this.
"""

from __future__ import annotations

import logging
import os
import stat
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import aiodocker
from aiodocker.exceptions import DockerError
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from orchestrator.auth import SharedSecretMiddleware
from orchestrator.config import settings
from orchestrator.encryption import SystemSettingDecryptError
from orchestrator.errors import (
    DockerUnavailable,
    ImagePullFailed,
    RedisUnavailable,
    VolumeProvisionFailed,
    WorkspaceVolumeStoreUnavailable,
)
from orchestrator.reaper import start_reaper, stop_reaper
from orchestrator.redis_client import RedisSessionRegistry, set_registry
from orchestrator.routes_exec import router as exec_router
from orchestrator.routes_github import router as github_router
from orchestrator.routes_projects import router as projects_router
from orchestrator.routes_sessions import router as sessions_router
from orchestrator.routes_team_mirror import router as team_mirror_router
from orchestrator.routes_ws import router as ws_router
from orchestrator.sessions import VolumeMountFailed
from orchestrator.team_mirror_reaper import (
    start_team_mirror_reaper,
    stop_team_mirror_reaper,
)
from orchestrator.volume_store import close_pool, open_pool, set_pool

logger = logging.getLogger("orchestrator")
logging.basicConfig(
    level=logging.INFO,
    format='{"level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
)


# Module-level mutable health state. Set during lifespan startup; flipped by
# the docker probe inside the health endpoint. A class-free dict is enough —
# we have one process per orchestrator container and no concurrency hazards
# beyond a single boolean read.
_health: dict[str, Any] = {"image_present": False}


async def _pull_workspace_image(docker: aiodocker.Docker) -> None:
    """Ensure WORKSPACE_IMAGE is present locally; raise ImagePullFailed on any
    failure to obtain it.

    Two-step strategy (matches `docker pull --pull missing` semantics):
      1. `docker images inspect` the tag. If present, log image_pull_ok and
         return — workspace images are built locally per MEM099 and never
         pushed to a registry; a registry pull would always 404.
      2. If absent, attempt the registry pull. On any failure (404, daemon
         unreachable, network error) raise ImagePullFailed.

    Logs the same INFO `image_pull_ok` either way so downstream observability
    isn't conditional on whether the image was pre-built or freshly pulled.
    """
    image = settings.workspace_image
    # Step 1: short-circuit if the image is already cached on the daemon.
    try:
        await docker.images.inspect(image)
        logger.info("image_pull_ok image=%s source=local", image)
        return
    except DockerError as exc:
        if exc.status != 404:
            # Non-404 from the inspect itself is a real daemon problem, not a
            # missing image. Surface it.
            reason = f"{exc.status}:{exc.message}"
            logger.error("image_pull_failed image=%s reason=%s", image, reason)
            raise ImagePullFailed(reason) from exc
        # 404 → fall through to the registry pull attempt.
    except OSError as exc:
        reason = f"docker_unreachable:{type(exc).__name__}"
        logger.error("image_pull_failed image=%s reason=%s", image, reason)
        raise ImagePullFailed(reason) from exc

    # Step 2: registry pull. aiodocker.images.pull(stream=True) returns an
    # async generator directly (not an awaitable). Errors arrive either as
    # exceptions or as `{"error": ...}` events in the stream.
    try:
        events = docker.images.pull(image, stream=True)
        async for event in events:
            if isinstance(event, dict) and "error" in event:
                reason = event.get("error", "unknown")
                logger.error("image_pull_failed image=%s reason=%s", image, reason)
                raise ImagePullFailed(reason)
    except DockerError as exc:
        reason = f"{exc.status}:{exc.message}"
        logger.error("image_pull_failed image=%s reason=%s", image, reason)
        raise ImagePullFailed(reason) from exc
    except OSError as exc:
        reason = f"docker_unreachable:{type(exc).__name__}"
        logger.error("image_pull_failed image=%s reason=%s", image, reason)
        raise ImagePullFailed(reason) from exc

    logger.info("image_pull_ok image=%s source=registry", image)


def _ensure_loop_device_nodes(count: int = 32) -> None:
    """mknod /dev/loopN for N in [0, count) if missing.

    On Docker Desktop / linuxkit (MEM136), the privileged orchestrator
    container ships only loops 0-7 by default. Per-volume provisioning
    can outpace that; the kernel's LOOP_CTL_GET_FREE returns numbers
    beyond the pre-created nodes and `losetup --find --show` then fails
    with `No such file or directory` for the new device. mknod-ing more
    nodes up-front gives us headroom without changing the security
    boundary (we already need privileged for losetup at all).

    On native Linux hosts the nodes are universally present; mknod on
    an existing path raises FileExistsError which we ignore.
    """
    for n in range(count):
        path = f"/dev/loop{n}"
        if os.path.exists(path):
            continue
        try:
            # major=7 (loop), minor=N. mode 0o660 with type S_IFBLK.
            os.mknod(path, mode=stat.S_IFBLK | 0o660, device=os.makedev(7, n))
        except (FileExistsError, PermissionError, OSError) as exc:
            # Best-effort — if we can't mknod (non-privileged, non-Linux,
            # whatever) we let the first losetup --find failure surface
            # the real problem at request time. Boot doesn't depend on
            # this.
            logger.warning(
                "loop_device_mknod_failed loop=%d reason=%s",
                n,
                type(exc).__name__,
            )
            break


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    redacted = (
        settings.redis_password[:4] + "..." if settings.redis_password else "<unset>"
    )
    logger.info(
        "orchestrator_starting port=%d redis_password=%s image=%s",
        settings.port,
        redacted,
        settings.workspace_image,
    )

    # Pre-create enough loop device nodes for sustained provisioning. On
    # privileged Linux this is a no-op; on linuxkit it adds the missing
    # /dev/loopN nodes the kernel needs to bind beyond loop 7. See MEM136
    # and the docstring on `_ensure_loop_device_nodes`.
    _ensure_loop_device_nodes(count=32)

    # Boot-time fail-fast: ORCHESTRATOR_API_KEY must be set, otherwise every
    # downstream request 401s and we'd rather scream than silently misbehave.
    if not settings.orchestrator_api_key:
        logger.error("orchestrator_boot_failed reason=missing_api_key")
        # Use os._exit so the lifespan unwind doesn't swallow the signal —
        # we want the container to actually die so the orchestrator restart
        # policy kicks in (or the operator notices).
        os._exit(1)

    # Image pull. Boot blocker per D018 / CONTEXT error-handling. In test
    # contexts the suite can opt out via SKIP_IMAGE_PULL_ON_BOOT=1 (used by
    # the unit suite where Docker isn't available; integration tests that
    # cover the pull behavior boot a real container so the env var is unset).
    docker: aiodocker.Docker | None = None
    if os.environ.get("SKIP_IMAGE_PULL_ON_BOOT") != "1":
        docker = aiodocker.Docker()
        try:
            await _pull_workspace_image(docker)
            _health["image_present"] = True
        except ImagePullFailed:
            # Already logged. Close the docker handle then exit hard.
            await docker.close()
            os._exit(1)
        # Keep docker handle around for the health endpoint to reuse for the
        # liveness probe — avoids re-opening a socket per healthcheck.
        app.state.docker = docker
    else:
        # Skipped pull → image_present stays False. Tests that exercise
        # the auth middleware don't care about image presence.
        app.state.docker = None
        _health["image_present"] = False

    # Redis client singleton — bound here so tests that import the app
    # without running the lifespan can substitute their own.
    registry = RedisSessionRegistry()
    set_registry(registry)
    app.state.registry = registry

    # Postgres asyncpg pool — owned by the lifespan. Tests that import
    # the app without running the lifespan can substitute via set_pool.
    # Boot-time pool open is best-effort: if Postgres is briefly slow,
    # the pool open itself will retry inside asyncpg; if it fails hard
    # we log a warning and continue — routes will 503 with the structured
    # `workspace_volume_store_unavailable` shape until pg comes back. We
    # deliberately do NOT os._exit here — Postgres can come up after the
    # orchestrator and we'd rather serve /v1/health and the auth
    # middleware than crash-loop the orchestrator container.
    pg_pool = None
    if os.environ.get("SKIP_PG_POOL_ON_BOOT") != "1":
        try:
            pg_pool = await open_pool()
            set_pool(pg_pool)
            logger.info("pg_pool_opened size=5")
        except WorkspaceVolumeStoreUnavailable as exc:
            logger.warning(
                "pg_pool_unavailable_at_boot reason=%s",
                str(exc),
            )
            set_pool(None)
    else:
        # Test path: the unit suite imports the app without a real Postgres.
        set_pool(None)
    app.state.pg = pg_pool

    # Background idle reaper (S04/T02). The task owns its own asyncio.Task
    # handle stored on app.state so the lifespan teardown can cancel+await
    # before the Redis/pg/docker handles close — otherwise pytest leaks
    # `Task was destroyed but it is pending` warnings on every test that
    # boots the orchestrator. The reaper is a structural no-op when
    # `app.state.docker is None` (test path with SKIP_IMAGE_PULL_ON_BOOT=1)
    # so we always start it; the loop logs `reaper_tick_skipped` for those.
    app.state.reaper_task = start_reaper(app)

    # Per-team mirror reaper (M004/S03/T02). Structurally separate from
    # the user-session reaper because their failure modes differ (D022 —
    # reaping a mirror mid-clone breaks the user's fetch). Started AFTER
    # the user-session reaper so the lifespan startup ordering matches
    # the symmetrically-ordered teardown below.
    app.state.team_mirror_reaper_task = start_team_mirror_reaper(app)

    logger.info("orchestrator_ready image_present=%s", _health["image_present"])
    try:
        yield
    finally:
        # Stop the team-mirror reaper FIRST (MEM190 — in-flight tick
        # reads pg + docker), then the user-session reaper FIRST among
        # the remaining three handles, then registry/pg/docker. Tearing
        # any handle out from under an in-flight tick would surface as
        # `team_mirror_reaper_tick_failed` / `reaper_tick_failed`
        # warnings on every shutdown. The 5s budget per stop covers the
        # worst-case in-flight `docker exec` / `containers.stop` call.
        await stop_team_mirror_reaper(
            getattr(app.state, "team_mirror_reaper_task", None)
        )
        await stop_reaper(getattr(app.state, "reaper_task", None))
        await registry.close()
        set_registry(None)
        await close_pool(pg_pool)
        set_pool(None)
        if docker is not None:
            await docker.close()


app = FastAPI(
    title="Perpetuity Orchestrator",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=_lifespan,
)

app.add_middleware(SharedSecretMiddleware)


@app.exception_handler(RedisUnavailable)
async def _redis_unavailable_handler(
    _request: Request, exc: RedisUnavailable
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "redis_unavailable", "reason": str(exc)},
    )


@app.exception_handler(DockerUnavailable)
async def _docker_unavailable_handler(
    _request: Request, exc: DockerUnavailable
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "docker_unavailable", "reason": str(exc)},
    )


@app.exception_handler(VolumeMountFailed)
async def _volume_mount_failed_handler(
    _request: Request, exc: VolumeMountFailed
) -> JSONResponse:
    # Backward-compat handler kept from T03's plain-dir path. The S02
    # loopback-volume flow surfaces failures as VolumeProvisionFailed
    # (handled below); this handler only fires if a future code path
    # falls back to the bind-mount mkdir helper.
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "volume_mount_failed", "reason": str(exc)},
    )


@app.exception_handler(VolumeProvisionFailed)
async def _volume_provision_failed_handler(
    _request: Request, exc: VolumeProvisionFailed
) -> JSONResponse:
    """S02 loopback-ext4 provisioning failure → 500 with the failing step.

    `step` is one of {truncate, mkfs, losetup, mount} so the next agent
    can re-run that exact command by hand from inside the orchestrator.
    `reason` is the first non-empty stderr line, truncated to 200 chars
    by the `volumes` module — leak-safe by construction (MEM134).
    """
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "volume_provision_failed",
            "step": exc.step,
            "reason": exc.reason,
        },
    )


@app.exception_handler(WorkspaceVolumeStoreUnavailable)
async def _workspace_volume_store_unavailable_handler(
    _request: Request, exc: WorkspaceVolumeStoreUnavailable
) -> JSONResponse:
    """Postgres unreachable / pool exhausted / query timeout → 503.

    Distinct shape from `redis_unavailable` so the backend can surface
    "your scrollback is degraded" (redis) differently from "your fresh
    workspace can't be provisioned right now" (pg).
    """
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "detail": "workspace_volume_store_unavailable",
            "reason": str(exc),
        },
    )


@app.exception_handler(SystemSettingDecryptError)
async def _system_settings_decrypt_failed_handler(
    _request: Request, exc: SystemSettingDecryptError
) -> JSONResponse:
    """M004/S02 mirror of the backend handler.

    A Fernet decrypt of a sensitive system_settings row failed — most
    likely a key rotation mismatch between backend and orchestrator.
    Plaintext NEVER appears in the log line or response body; only the
    row key is named so triage can localize without leaking the secret.
    """
    logger.error(
        "system_settings_decrypt_failed key=%s",
        exc.key,
    )
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "detail": "system_settings_decrypt_failed",
            "key": exc.key,
        },
    )


app.include_router(sessions_router)
app.include_router(ws_router)
app.include_router(github_router)
app.include_router(team_mirror_router)
app.include_router(projects_router)
# M005/S02: one-shot exec endpoint that the backend's run_workflow Celery
# task uses to drive each ``ai`` step inside the (user, team) workspace
# container. Mounted under the same /v1/sessions prefix as the tmux
# session lifecycle, but ``session_id`` here is purely a correlation
# handle — see routes_exec.py module docstring.
app.include_router(exec_router)


@app.get("/v1/health")
async def health() -> dict[str, Any]:
    """Compose healthcheck target.

    Public (no shared-secret) per the middleware allowlist. Returns
    `image_present: True` after the boot pull succeeded, `False` otherwise
    (boot pull skipped or Docker has since become unreachable). The compose
    healthcheck only requires a 200 — operators read the `image_present`
    field for diagnostic context.
    """
    docker: aiodocker.Docker | None = getattr(app.state, "docker", None)
    image_present = _health["image_present"]
    if docker is not None and image_present:
        # Cheap liveness probe: list a few images. If Docker is gone we flip
        # image_present to False so /v1/health surfaces the degradation.
        try:
            await docker.system.info()
        except (DockerError, OSError) as exc:
            logger.warning("docker_unreachable op=health reason=%s", type(exc).__name__)
            image_present = False
            _health["image_present"] = False
    return {"status": "ok", "image_present": image_present}
