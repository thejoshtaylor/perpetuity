"""Orchestrator FastAPI app.

T01 scope: minimal bootable app with a `/v1/health` endpoint so the compose
healthcheck passes. T02 wires shared-secret auth, Redis client, and image-pull
on boot; T03/T04 add session lifecycle and the WS bridge.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from orchestrator.config import settings

logger = logging.getLogger("orchestrator")
logging.basicConfig(
    level=logging.INFO,
    format='{"level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    redacted = (
        settings.redis_password[:4] + "..."
        if settings.redis_password
        else "<unset>"
    )
    logger.info("orchestrator_starting port=%d redis_password=%s", settings.port, redacted)
    logger.info("orchestrator_ready")
    yield


app = FastAPI(
    title="Perpetuity Orchestrator",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=_lifespan,
)


@app.get("/v1/health")
async def health() -> dict[str, Any]:
    return {"status": "ok"}
