"""Unit tests for shared-secret auth (T02).

Boots the FastAPI app via lifespan but skips the image pull — these tests
care about the auth middleware and the WS auth helper, not Docker. The unit
suite runs anywhere the orchestrator package is installed, including in CI
without a Docker daemon.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

# Force the unit suite to skip the boot-time image pull. Must be set BEFORE
# the orchestrator app is imported because the lifespan reads the env var.
os.environ.setdefault("SKIP_IMAGE_PULL_ON_BOOT", "1")
os.environ.setdefault("ORCHESTRATOR_API_KEY", "unit-test-current-key")
os.environ.setdefault("ORCHESTRATOR_API_KEY_PREVIOUS", "")


@pytest.fixture
def reset_keys() -> Iterator[None]:
    """Snapshot/restore key env vars + settings around each test."""
    from orchestrator.config import settings

    original_current = settings.orchestrator_api_key
    original_previous = settings.orchestrator_api_key_previous
    yield
    settings.orchestrator_api_key = original_current
    settings.orchestrator_api_key_previous = original_previous


def _make_app_with_ws() -> FastAPI:
    """Build a fresh app instance and register a WS endpoint that uses the
    auth helper. We can't add the WS route to the singleton `app` from the
    main module without polluting other tests; build a local app instead.
    """
    from orchestrator.auth import (
        SharedSecretMiddleware,
        authenticate_websocket,
    )

    local = FastAPI()
    local.add_middleware(SharedSecretMiddleware)

    @local.get("/v1/protected")
    async def _protected() -> dict[str, str]:
        return {"ok": "yes"}

    @local.websocket("/v1/ws/echo")
    async def _ws_echo(websocket: WebSocket) -> None:
        if not await authenticate_websocket(websocket):
            return
        await websocket.accept()
        msg = await websocket.receive_text()
        await websocket.send_text(f"echo:{msg}")
        await websocket.close()

    return local


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    local = _make_app_with_ws()
    transport = ASGITransport(app=local)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def ws_client() -> Iterator[TestClient]:
    """starlette TestClient supports websocket_connect; AsyncClient does not."""
    local = _make_app_with_ws()
    with TestClient(local) as tc:
        yield tc


@pytest.mark.asyncio
async def test_http_correct_key_returns_200(
    client: AsyncClient, reset_keys: None
) -> None:
    resp = await client.get(
        "/v1/protected", headers={"X-Orchestrator-Key": "unit-test-current-key"}
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": "yes"}


@pytest.mark.asyncio
async def test_http_wrong_key_returns_401(
    client: AsyncClient, reset_keys: None
) -> None:
    resp = await client.get(
        "/v1/protected", headers={"X-Orchestrator-Key": "definitely-not-the-key"}
    )
    assert resp.status_code == 401
    # No body content reveals which key would have worked.
    assert resp.json() == {"detail": "unauthorized"}


@pytest.mark.asyncio
async def test_http_missing_key_returns_401(
    client: AsyncClient, reset_keys: None
) -> None:
    resp = await client.get("/v1/protected")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_http_previous_key_accepted_during_rotation(
    client: AsyncClient, reset_keys: None
) -> None:
    """Two-key acceptance per D016: presenting the PREVIOUS key during a
    rotation window must succeed so the backend can be deployed second.
    """
    from orchestrator.config import settings

    settings.orchestrator_api_key = "current-key-after-rotation"
    settings.orchestrator_api_key_previous = "old-key-still-on-some-backends"

    resp = await client.get(
        "/v1/protected",
        headers={"X-Orchestrator-Key": "old-key-still-on-some-backends"},
    )
    assert resp.status_code == 200

    # Current key still accepted in parallel.
    resp = await client.get(
        "/v1/protected",
        headers={"X-Orchestrator-Key": "current-key-after-rotation"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_http_health_is_public(
    client: AsyncClient, reset_keys: None
) -> None:
    """The compose healthcheck must reach /v1/health without a key."""
    # The shared local app doesn't define /v1/health — but the middleware's
    # public path allowlist still applies. Test instead that the path is
    # exempt from auth by registering a health route on the local app.
    from orchestrator.auth import SharedSecretMiddleware

    app = FastAPI()
    app.add_middleware(SharedSecretMiddleware)

    @app.get("/v1/health")
    async def _h() -> dict[str, str]:
        return {"status": "ok"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/v1/health")
    assert resp.status_code == 200


def test_ws_correct_key_accepts(ws_client: TestClient, reset_keys: None) -> None:
    with ws_client.websocket_connect(
        "/v1/ws/echo?key=unit-test-current-key"
    ) as ws:
        ws.send_text("hello")
        assert ws.receive_text() == "echo:hello"


def test_ws_wrong_key_closes_1008(ws_client: TestClient, reset_keys: None) -> None:
    """WS with wrong key → close-before-accept with reason='unauthorized'.

    Starlette's TestClient surfaces a close-before-accept as a
    WebSocketDisconnect on the first receive — we match that and assert the
    code/reason. We do NOT assert on what code Starlette sends on the HTTP
    upgrade (it's a 403) because the contract is the WS-level close code.
    """
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with ws_client.websocket_connect("/v1/ws/echo?key=wrong-key") as ws:
            ws.receive_text()

    assert exc_info.value.code == 1008
    # Reason is the human-readable string we emit at close time.
    assert exc_info.value.reason == "unauthorized"


def test_ws_missing_key_closes_1008(ws_client: TestClient, reset_keys: None) -> None:
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with ws_client.websocket_connect("/v1/ws/echo") as ws:
            ws.receive_text()

    assert exc_info.value.code == 1008
    assert exc_info.value.reason == "unauthorized"


def test_ws_previous_key_accepted(ws_client: TestClient, reset_keys: None) -> None:
    from orchestrator.config import settings

    settings.orchestrator_api_key = "rotated-current"
    settings.orchestrator_api_key_previous = "rotated-previous"

    with ws_client.websocket_connect("/v1/ws/echo?key=rotated-previous") as ws:
        ws.send_text("rotated")
        assert ws.receive_text() == "echo:rotated"


def test_ws_unauthorized_log_redacts_full_key(
    ws_client: TestClient, caplog: pytest.LogCaptureFixture, reset_keys: None
) -> None:
    """Regression: the orchestrator_ws_unauthorized log line must NEVER
    contain the full presented key — only a 4-char prefix. Per the slice
    observability appendix.
    """
    from starlette.websockets import WebSocketDisconnect

    import logging

    # Force the orchestrator logger to propagate so caplog can see records.
    orch_logger = logging.getLogger("orchestrator")
    prior_propagate = orch_logger.propagate
    orch_logger.propagate = True

    secret_attempt = "supersecret-attempt-xyz123"
    try:
        with caplog.at_level(logging.ERROR, logger="orchestrator"):
            with pytest.raises(WebSocketDisconnect):
                with ws_client.websocket_connect(
                    f"/v1/ws/echo?key={secret_attempt}"
                ) as ws:
                    ws.receive_text()
    finally:
        orch_logger.propagate = prior_propagate

    # The unauthorized log line should appear with a redacted prefix.
    matching = [r for r in caplog.records if "orchestrator_ws_unauthorized" in r.message]
    assert matching, "expected orchestrator_ws_unauthorized log line"
    for rec in matching:
        assert secret_attempt not in rec.message
        assert "supe..." in rec.message
