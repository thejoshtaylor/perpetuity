"""Unit tests for get_user_access_token — happy path and row_missing path.

These tests cover T02 of M006/S03.  No network calls are made in either path:
- happy path: access token is fresh → decrypt and return; no GitHub HTTP call.
- row_missing: no DB row → UserTokenUnavailable(reason="row_missing"); no HTTP call.

T03 will add the refresh-on-expiry paths and respx mocking for the network leg.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet

_TEST_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _patch_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject a valid Fernet key and clear the _load_key cache."""
    import app.core.encryption as enc

    monkeypatch.setenv("SYSTEM_SETTINGS_ENCRYPTION_KEY", _TEST_KEY)
    enc._load_key.cache_clear()
    yield
    enc._load_key.cache_clear()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _run(coro: Any) -> Any:
    """Run a coroutine synchronously (no pytest-asyncio required)."""
    return asyncio.run(coro)


def _make_row(
    user_id: uuid.UUID,
    plaintext_access_token: str,
    expires_at: datetime,
) -> MagicMock:
    """Build a minimal GitHubUserOAuthToken-like mock row."""
    from app.core.github_user_tokens import encrypt_user_token

    row = MagicMock()
    row.user_id = user_id
    row.access_token_encrypted = encrypt_user_token(plaintext_access_token)
    row.access_token_expires_at = expires_at
    return row


def _make_async_session(row_or_none: object) -> AsyncMock:
    """Return an async session mock whose .get() returns *row_or_none*."""
    session = AsyncMock()
    session.get = AsyncMock(return_value=row_or_none)
    return session


# ---------------------------------------------------------------------------
# Happy path: token is unexpired — no HTTP call
# ---------------------------------------------------------------------------


def test_happy_path_returns_access_token_without_github_call() -> None:
    """Row exists, access_token_expires_at is well in the future.

    get_user_access_token must return the decrypted token string and must NOT
    make any outbound HTTP call.
    """
    import httpx

    from app.core.github_user_tokens import _ACCESS_TOKEN_SKEW_SECONDS, get_user_access_token

    user_id = uuid.uuid4()
    plaintext = "ghu_HappyPathAccessToken1234567890"
    # Set expires well beyond the skew window to trigger the happy path.
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=_ACCESS_TOKEN_SKEW_SECONDS + 600)

    row = _make_row(user_id, plaintext, expires_at)
    session = _make_async_session(row)

    http_calls: list[Any] = []

    async def _no_post(*args: Any, **kwargs: Any) -> Any:
        http_calls.append(("post", args, kwargs))
        raise AssertionError("httpx.AsyncClient.post must not be called in happy path")

    with patch.object(httpx.AsyncClient, "post", _no_post):
        result = _run(get_user_access_token(session, user_id))

    assert result == plaintext, f"Expected {plaintext!r}, got {result!r}"
    assert http_calls == [], "No HTTP calls should have been made"


# ---------------------------------------------------------------------------
# Row-missing path: no row → UserTokenUnavailable(reason="row_missing")
# ---------------------------------------------------------------------------


def test_row_missing_raises_user_token_unavailable_without_github_call() -> None:
    """No row in DB for user_id.

    get_user_access_token must raise UserTokenUnavailable with reason
    "row_missing" and must NOT make any outbound HTTP call.
    """
    import httpx

    from app.core.github_user_tokens import UserTokenUnavailable, get_user_access_token

    user_id = uuid.uuid4()
    session = _make_async_session(None)  # session.get returns None → no row

    http_calls: list[Any] = []

    async def _no_post(*args: Any, **kwargs: Any) -> Any:
        http_calls.append(("post", args, kwargs))
        raise AssertionError("httpx.AsyncClient.post must not be called in row_missing path")

    with patch.object(httpx.AsyncClient, "post", _no_post):
        with pytest.raises(UserTokenUnavailable) as exc_info:
            _run(get_user_access_token(session, user_id))

    exc = exc_info.value
    assert exc.user_id == user_id
    assert exc.reason == "row_missing"
    assert http_calls == [], "No HTTP calls should have been made"


# ---------------------------------------------------------------------------
# UserTokenUnavailable class contract
# ---------------------------------------------------------------------------


def test_user_token_unavailable_carries_user_id_and_reason() -> None:
    """UserTokenUnavailable must expose .user_id and .reason attributes."""
    from app.core.github_user_tokens import UserTokenUnavailable

    uid = uuid.uuid4()
    exc = UserTokenUnavailable(user_id=uid, reason="row_missing")

    assert exc.user_id == uid
    assert exc.reason == "row_missing"
    assert str(uid) in str(exc)
    assert "row_missing" in str(exc)


def test_user_token_unavailable_is_distinct_from_decrypt_error() -> None:
    """UserTokenUnavailable and GitHubUserTokenDecryptError are unrelated."""
    from app.core.github_user_tokens import GitHubUserTokenDecryptError, UserTokenUnavailable

    assert UserTokenUnavailable is not GitHubUserTokenDecryptError
    assert not issubclass(UserTokenUnavailable, GitHubUserTokenDecryptError)
    assert not issubclass(GitHubUserTokenDecryptError, UserTokenUnavailable)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


def test_module_constants_exist() -> None:
    """_ACCESS_TOKEN_SKEW_SECONDS and _GITHUB_TOKEN_URL must be present."""
    from app.core.github_user_tokens import _ACCESS_TOKEN_SKEW_SECONDS, _GITHUB_TOKEN_URL

    assert _ACCESS_TOKEN_SKEW_SECONDS == 60
    assert _GITHUB_TOKEN_URL == "https://github.com/login/oauth/access_token"
