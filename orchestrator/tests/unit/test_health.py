"""Unit test for the orchestrator /v1/health endpoint.

Runs against the FastAPI app object directly via httpx.AsyncClient with the
ASGI transport — no network, no Docker, no Redis. Confirms the bootable
surface that compose's healthcheck depends on.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from orchestrator.main import app


@pytest.mark.asyncio
async def test_health_returns_ok() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
