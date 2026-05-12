"""Unit tests for _resolve_installation_id_from_oauth_code → ResolvedOAuthInstall.

These tests call the helper directly with a mock session and monkeypatched
httpx.AsyncClient so they run without a real database or GitHub connection.
They cover:
  - happy path: all token fields present → returns ResolvedOAuthInstall
  - missing refresh_token → HTTPException 502 github_oauth_exchange_failed
  - missing scope → HTTPException 502 github_oauth_exchange_failed
  - missing expires_in → HTTPException 502 github_oauth_exchange_failed
  - missing refresh_token_expires_in → HTTPException 502 github_oauth_exchange_failed

All tests inject their own Fernet key via monkeypatch so no real env var is
required (same approach as test_github_user_tokens_crypto.py).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from cryptography.fernet import Fernet

from app.api.routes.admin import (
    GITHUB_APP_CLIENT_ID_KEY,
    GITHUB_APP_CLIENT_SECRET_KEY,
)
from app.api.routes.github import (
    ResolvedOAuthInstall,
    _resolve_installation_id_from_oauth_code,
)
from app.core.encryption import encrypt_setting
from app.models import SystemSetting

# ---------------------------------------------------------------------------
# Encryption key injection
# ---------------------------------------------------------------------------

_TEST_FERNET_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _patch_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject a valid Fernet key and clear the _load_key cache before each test."""
    import app.core.encryption as enc

    monkeypatch.setenv("SYSTEM_SETTINGS_ENCRYPTION_KEY", _TEST_FERNET_KEY)
    enc._load_key.cache_clear()
    yield
    enc._load_key.cache_clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(
    client_id: str = "Iv1.test-client-id",
    client_secret: str = "supersecret",
) -> MagicMock:
    """Build a mock session that returns seeded SystemSetting rows."""
    client_id_row = SystemSetting(
        key=GITHUB_APP_CLIENT_ID_KEY,
        value=client_id,
        value_encrypted=None,
        sensitive=False,
        has_value=True,
    )
    secret_ciphertext = encrypt_setting(client_secret)
    secret_row = SystemSetting(
        key=GITHUB_APP_CLIENT_SECRET_KEY,
        value=None,
        value_encrypted=secret_ciphertext,
        sensitive=True,
        has_value=True,
    )

    def _get(model_cls: type, key: str) -> SystemSetting | None:  # noqa: ARG001
        if key == GITHUB_APP_CLIENT_ID_KEY:
            return client_id_row
        if key == GITHUB_APP_CLIENT_SECRET_KEY:
            return secret_row
        return None

    session = MagicMock()
    session.get.side_effect = _get
    return session


class _FakeResponse:
    def __init__(
        self,
        status_code: int,
        json_body: object | None = None,
        *,
        raises_on_json: bool = False,
    ) -> None:
        self.status_code = status_code
        self._json = json_body
        self._raises = raises_on_json
        self.request = httpx.Request("GET", "http://fake")

    def json(self) -> object:
        if self._raises:
            raise ValueError("not json")
        return self._json


class _FakeAsyncClient:
    """Minimal stub for httpx.AsyncClient used by the route module."""

    def __init__(self, token_resp: _FakeResponse, installs_resp: _FakeResponse) -> None:
        self._token_resp = token_resp
        self._installs_resp = installs_resp
        self._call_count = 0

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def post(self, url: str, **_: object) -> _FakeResponse:  # noqa: ARG002
        return self._token_resp

    async def get(self, url: str, **_: object) -> _FakeResponse:  # noqa: ARG002
        return self._installs_resp


def _good_token_body(
    *,
    access_token: str = "ghu_testtoken",
    refresh_token: str = "ghr_testrefresh",
    expires_in: int = 28800,
    refresh_token_expires_in: int = 15897600,
    scope: str = "repo",
) -> dict[str, Any]:
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_in": expires_in,
        "refresh_token_expires_in": refresh_token_expires_in,
        "scope": scope,
        "token_type": "bearer",
    }


def _good_installs_body(installation_id: int = 42) -> dict[str, Any]:
    return {
        "total_count": 1,
        "installations": [{"id": installation_id, "app_slug": "test-app"}],
    }


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _install_fake_client(
    monkeypatch: pytest.MonkeyPatch,
    token_body: dict[str, Any],
    installs_body: dict[str, Any] | None = None,
    token_status: int = 200,
    installs_status: int = 200,
) -> None:
    import app.api.routes.github as github_mod

    if installs_body is None:
        installs_body = _good_installs_body()

    token_resp = _FakeResponse(token_status, token_body)
    installs_resp = _FakeResponse(installs_status, installs_body)

    def _factory(*_args: object, **_kwargs: object) -> _FakeAsyncClient:
        return _FakeAsyncClient(token_resp, installs_resp)

    monkeypatch.setattr(github_mod.httpx, "AsyncClient", _factory)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_happy_path_returns_resolved_oauth_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All token fields present + valid installations → returns ResolvedOAuthInstall."""
    token_body = _good_token_body(
        access_token="ghu_abc123",
        refresh_token="ghr_refresh456",
        expires_in=28800,
        refresh_token_expires_in=15897600,
        scope="repo,read:org",
    )
    install_id = 99001
    _install_fake_client(monkeypatch, token_body, _good_installs_body(install_id))

    session = _make_session()
    result = _run(_resolve_installation_id_from_oauth_code(session, "ghu_testcode"))

    assert isinstance(result, ResolvedOAuthInstall)
    assert result.installation_id == install_id
    assert result.access_token == "ghu_abc123"
    assert result.refresh_token == "ghr_refresh456"
    assert result.expires_in == 28800
    assert result.refresh_token_expires_in == 15897600
    assert result.scope == "repo,read:org"


def test_happy_path_empty_scope_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scope='' (empty string) is a valid value — key must exist but may be empty."""
    token_body = _good_token_body(scope="")
    _install_fake_client(monkeypatch, token_body, _good_installs_body(12345))

    session = _make_session()
    result = _run(_resolve_installation_id_from_oauth_code(session, "code"))

    assert result.scope == ""


def test_missing_refresh_token_raises_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Token body without refresh_token → HTTPException 502 github_oauth_exchange_failed."""
    from fastapi import HTTPException

    token_body = _good_token_body()
    del token_body["refresh_token"]
    _install_fake_client(monkeypatch, token_body)

    session = _make_session()
    with pytest.raises(HTTPException) as exc_info:
        _run(_resolve_installation_id_from_oauth_code(session, "code"))

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "github_oauth_exchange_failed"


def test_missing_scope_raises_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Token body without scope key → HTTPException 502 github_oauth_exchange_failed."""
    from fastapi import HTTPException

    token_body = _good_token_body()
    del token_body["scope"]
    _install_fake_client(monkeypatch, token_body)

    session = _make_session()
    with pytest.raises(HTTPException) as exc_info:
        _run(_resolve_installation_id_from_oauth_code(session, "code"))

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "github_oauth_exchange_failed"


def test_missing_expires_in_raises_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Token body without expires_in → HTTPException 502 github_oauth_exchange_failed."""
    from fastapi import HTTPException

    token_body = _good_token_body()
    del token_body["expires_in"]
    _install_fake_client(monkeypatch, token_body)

    session = _make_session()
    with pytest.raises(HTTPException) as exc_info:
        _run(_resolve_installation_id_from_oauth_code(session, "code"))

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "github_oauth_exchange_failed"


def test_missing_refresh_token_expires_in_raises_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Token body without refresh_token_expires_in → HTTPException 502."""
    from fastapi import HTTPException

    token_body = _good_token_body()
    del token_body["refresh_token_expires_in"]
    _install_fake_client(monkeypatch, token_body)

    session = _make_session()
    with pytest.raises(HTTPException) as exc_info:
        _run(_resolve_installation_id_from_oauth_code(session, "code"))

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "github_oauth_exchange_failed"


def test_wrong_type_refresh_token_raises_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """refresh_token present but wrong type (int) → HTTPException 502."""
    from fastapi import HTTPException

    token_body = _good_token_body()
    token_body["refresh_token"] = 12345  # type: ignore[assignment]
    _install_fake_client(monkeypatch, token_body)

    session = _make_session()
    with pytest.raises(HTTPException) as exc_info:
        _run(_resolve_installation_id_from_oauth_code(session, "code"))

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "github_oauth_exchange_failed"
