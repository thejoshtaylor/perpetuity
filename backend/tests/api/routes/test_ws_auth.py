"""Integration tests for the /ws/ping endpoint's cookie-based authentication.

Exercises all three reject paths (missing_cookie, invalid_token, and the happy
path) against the real app via TestClient.websocket_connect. Also runs the
user_not_found / user_inactive branches because the plan flags those as failure
modes that the endpoint must advertise via the ws close reason string.
"""
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session
from starlette.websockets import WebSocketDisconnect

from app import crud
from app.core.config import settings
from app.core.security import ALGORITHM, create_session_token
from app.models import UserCreate
from tests.utils.utils import random_email, random_lower_string

WS_PING_URL = f"{settings.API_V1_STR}/ws/ping"


def test_ws_connect_without_cookie_rejects_missing_cookie(client: TestClient) -> None:
    client.cookies.clear()
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(WS_PING_URL) as ws:
            # The server closes before accept, so receive should raise.
            ws.receive_text()
    assert excinfo.value.code == 1008
    assert excinfo.value.reason == "missing_cookie"


def test_ws_connect_with_garbage_cookie_rejects_invalid_token(
    client: TestClient,
) -> None:
    client.cookies.clear()
    client.cookies.set(settings.SESSION_COOKIE_NAME, "not-a-jwt")
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(WS_PING_URL) as ws:
            ws.receive_text()
    assert excinfo.value.code == 1008
    assert excinfo.value.reason == "invalid_token"


def test_ws_connect_with_expired_cookie_rejects_invalid_token(
    client: TestClient, db: Session
) -> None:
    """Expired JWT → decode_session_token returns None → invalid_token close."""
    email = random_email()
    password = random_lower_string()
    user = crud.create_user(
        session=db, user_create=UserCreate(email=email, password=password)
    )
    assert isinstance(user.id, uuid.UUID)
    expired = jwt.encode(
        {
            "sub": str(user.id),
            "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
        },
        settings.SECRET_KEY,
        algorithm=ALGORITHM,
    )
    client.cookies.clear()
    client.cookies.set(settings.SESSION_COOKIE_NAME, expired)
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(WS_PING_URL) as ws:
            ws.receive_text()
    assert excinfo.value.code == 1008
    assert excinfo.value.reason == "invalid_token"


def test_ws_connect_with_unknown_user_rejects_user_not_found(
    client: TestClient,
) -> None:
    """Valid signature, sub is a well-formed UUID, but no such user in the DB."""
    ghost_id = uuid.uuid4()
    token = create_session_token(ghost_id)
    client.cookies.clear()
    client.cookies.set(settings.SESSION_COOKIE_NAME, token)
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(WS_PING_URL) as ws:
            ws.receive_text()
    assert excinfo.value.code == 1008
    assert excinfo.value.reason == "user_not_found"


def test_ws_connect_with_inactive_user_rejects_user_inactive(
    client: TestClient, db: Session
) -> None:
    email = random_email()
    password = random_lower_string()
    user = crud.create_user(
        session=db,
        user_create=UserCreate(email=email, password=password, is_active=False),
    )
    assert isinstance(user.id, uuid.UUID)
    token = create_session_token(user.id)
    client.cookies.clear()
    client.cookies.set(settings.SESSION_COOKIE_NAME, token)
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(WS_PING_URL) as ws:
            ws.receive_text()
    assert excinfo.value.code == 1008
    assert excinfo.value.reason == "user_inactive"


def test_ws_connect_with_valid_cookie_returns_pong_and_role(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """Happy path: authenticated WS echoes user id + role."""
    # Install superuser cookies onto the TestClient so the upgrade request carries them.
    client.cookies.clear()
    for name, value in superuser_cookies.items():
        client.cookies.set(name, value)
    with client.websocket_connect(WS_PING_URL) as ws:
        ws.send_text("ping")
        payload = ws.receive_json()
    assert "pong" in payload
    # pong is the user_id — ensure it's a parseable UUID string.
    uuid.UUID(payload["pong"])
    assert payload["role"] == "system_admin"
