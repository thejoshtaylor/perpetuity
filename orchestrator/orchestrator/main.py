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
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import aiodocker
from aiodocker.exceptions import DockerError
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from orchestrator.auth import SharedSecretMiddleware
from orchestrator.config import settings
from orchestrator.errors import (
    DockerUnavailable,
    ImagePullFailed,
    RedisUnavailable,
)
from orchestrator.redis_client import RedisSessionRegistry, set_registry
from orchestrator.routes_sessions import router as sessions_router
from orchestrator.routes_ws import router as ws_router
from orchestrator.sessions import VolumeMountFailed

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

    logger.info("orchestrator_ready image_present=%s", _health["image_present"])
    try:
        yield
    finally:
        await registry.close()
        set_registry(None)
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
    # T03 placeholder shape; S02 owns the rich loopback-volume failure space.
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "volume_mount_failed", "reason": str(exc)},
    )


app.include_router(sessions_router)
app.include_router(ws_router)


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
