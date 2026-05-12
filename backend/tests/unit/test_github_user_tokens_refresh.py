"""Unit tests for get_user_access_token — all paths.

T02 paths (no network calls):
- happy path: access token is fresh → decrypt and return.
- row_missing: no DB row → UserTokenUnavailable(reason="row_missing").

T03 paths (refresh-on-expiry, network mocked via unittest.mock):
- expired access token + valid refresh → POST to GitHub, update row, return new token.
- expired access token + bad_refresh_token error → DELETE row, raise UserTokenUnavailable.
- expired access token + unexpected response body → DELETE row, raise UserTokenUnavailable.
- network error (transient) → retry once, do NOT DELETE row, raise UserTokenUnavailable.
- corrupt refresh token ciphertext → GitHubUserTokenDecryptError, no DELETE.
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


# ===========================================================================
# T03: Refresh-on-expiry paths
# ===========================================================================


def _make_expired_row(
    user_id: uuid.UUID,
    plaintext_access_token: str,
    plaintext_refresh_token: str,
) -> MagicMock:
    """Build a mock row where the access token is already expired."""
    from app.core.github_user_tokens import encrypt_user_token

    row = MagicMock()
    row.user_id = user_id
    row.access_token_encrypted = encrypt_user_token(plaintext_access_token)
    row.access_token_expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    row.refresh_token_encrypted = encrypt_user_token(plaintext_refresh_token)
    row.refresh_token_expires_at = datetime.now(timezone.utc) + timedelta(days=5)
    return row


def _make_async_session_with_ops(row_or_none: object) -> AsyncMock:
    """Return an async session mock supporting get, delete, and commit."""
    session = AsyncMock()
    session.get = AsyncMock(return_value=row_or_none)
    session.delete = AsyncMock(return_value=None)
    session.commit = AsyncMock(return_value=None)
    return session


def _mock_github_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch read_github_app_oauth_credentials to return dummy creds."""
    import app.core.github_user_tokens as _mod

    monkeypatch.setattr(
        "app.core.github_app_oauth.read_github_app_oauth_credentials",
        lambda session: ("test_client_id", "test_client_secret"),
    )


# ---------------------------------------------------------------------------
# T03-1: Expired access token + valid GitHub refresh → update row, return new token
# ---------------------------------------------------------------------------


def test_expired_access_token_refresh_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Row exists, access token expired, GitHub refresh succeeds.

    get_user_access_token must:
    - POST to _GITHUB_TOKEN_URL with grant_type=refresh_token
    - Update the row with new encrypted tokens + expiry timestamps
    - Call session.commit()
    - Return the new plaintext access token
    - NOT call session.delete()
    """
    from unittest.mock import AsyncMock, patch

    import httpx

    from app.core.github_user_tokens import get_user_access_token

    user_id = uuid.uuid4()
    old_access = "ghu_OldExpiredToken1234567890abcdef"
    old_refresh = "ghr_OldRefreshToken1234567890abcdef"
    new_access = "ghu_NewAccessToken1234567890abcdef"
    new_refresh = "ghr_NewRefreshToken1234567890abcdef"

    row = _make_expired_row(user_id, old_access, old_refresh)
    session = _make_async_session_with_ops(row)

    github_response = MagicMock()
    github_response.json.return_value = {
        "access_token": new_access,
        "refresh_token": new_refresh,
        "token_type": "bearer",
        "expires_in": 28800,
        "refresh_token_expires_in": 15897600,
    }

    post_calls: list[Any] = []

    async def _fake_post(self: Any, url: str, **kwargs: Any) -> Any:
        post_calls.append({"url": url, "kwargs": kwargs})
        return github_response

    with patch(
        "app.core.github_app_oauth.read_github_app_oauth_credentials",
        return_value=("test_client_id", "test_client_secret"),
    ):
        with patch.object(httpx.AsyncClient, "post", _fake_post):
            result = _run(get_user_access_token(session, user_id))

    assert result == new_access, f"Expected new access token, got {result!r}"
    assert len(post_calls) == 1, "Exactly one POST to GitHub expected"
    assert post_calls[0]["url"] == "https://github.com/login/oauth/access_token"
    posted_json = post_calls[0]["kwargs"].get("json", {})
    assert posted_json.get("grant_type") == "refresh_token"
    assert posted_json.get("refresh_token") == old_refresh

    session.delete.assert_not_called()
    session.commit.assert_called_once()

    # Row attributes must have been updated in-place.
    assert row.access_token_encrypted is not None
    assert row.refresh_token_encrypted is not None
    assert row.access_token_expires_at is not None
    assert row.refresh_token_expires_at is not None


# ---------------------------------------------------------------------------
# T03-2: GitHub returns bad_refresh_token → DELETE row, raise UserTokenUnavailable
# ---------------------------------------------------------------------------


def test_bad_refresh_token_deletes_row_and_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """GitHub returns error=bad_refresh_token.

    get_user_access_token must:
    - Call session.delete(row) and session.commit()
    - Raise UserTokenUnavailable(reason="bad_refresh_token")
    """
    from unittest.mock import patch

    import httpx

    from app.core.github_user_tokens import UserTokenUnavailable, get_user_access_token

    user_id = uuid.uuid4()
    row = _make_expired_row(user_id, "ghu_Old", "ghr_Old")
    session = _make_async_session_with_ops(row)

    github_response = MagicMock()
    github_response.json.return_value = {
        "error": "bad_refresh_token",
        "error_description": "The `refresh_token` passed is incorrect or expired.",
    }

    async def _fake_post(self: Any, url: str, **kwargs: Any) -> Any:
        return github_response

    with patch(
        "app.core.github_app_oauth.read_github_app_oauth_credentials",
        return_value=("cid", "csecret"),
    ):
        with patch.object(httpx.AsyncClient, "post", _fake_post):
            with pytest.raises(UserTokenUnavailable) as exc_info:
                _run(get_user_access_token(session, user_id))

    exc = exc_info.value
    assert exc.user_id == user_id
    assert exc.reason == "bad_refresh_token"
    session.delete.assert_called_once_with(row)
    session.commit.assert_called_once()


# ---------------------------------------------------------------------------
# T03-3: GitHub returns non-parseable body → refresh_unexpected_response
# ---------------------------------------------------------------------------


def test_refresh_unexpected_response_deletes_row_and_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GitHub returns a 200 with a body that cannot be parsed as JSON.

    get_user_access_token must:
    - Call session.delete(row) and session.commit()
    - Raise UserTokenUnavailable(reason="refresh_unexpected_response")
    """
    from unittest.mock import patch

    import httpx

    from app.core.github_user_tokens import UserTokenUnavailable, get_user_access_token

    user_id = uuid.uuid4()
    row = _make_expired_row(user_id, "ghu_Old", "ghr_Old")
    session = _make_async_session_with_ops(row)

    github_response = MagicMock()
    github_response.json.side_effect = ValueError("not JSON")

    async def _fake_post(self: Any, url: str, **kwargs: Any) -> Any:
        return github_response

    with patch(
        "app.core.github_app_oauth.read_github_app_oauth_credentials",
        return_value=("cid", "csecret"),
    ):
        with patch.object(httpx.AsyncClient, "post", _fake_post):
            with pytest.raises(UserTokenUnavailable) as exc_info:
                _run(get_user_access_token(session, user_id))

    exc = exc_info.value
    assert exc.user_id == user_id
    assert exc.reason == "refresh_unexpected_response"
    session.delete.assert_called_once_with(row)
    session.commit.assert_called_once()


# ---------------------------------------------------------------------------
# T03-4: Network error on both attempts → refresh_transient, row NOT deleted
# ---------------------------------------------------------------------------


def test_refresh_transient_network_error_does_not_delete_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both POST attempts raise httpx.ConnectError (network failure).

    get_user_access_token must:
    - NOT call session.delete()
    - Raise UserTokenUnavailable(reason="refresh_transient")
    """
    from unittest.mock import patch

    import httpx

    from app.core.github_user_tokens import UserTokenUnavailable, get_user_access_token

    user_id = uuid.uuid4()
    row = _make_expired_row(user_id, "ghu_Old", "ghr_Old")
    session = _make_async_session_with_ops(row)

    call_count = 0

    async def _always_fail(self: Any, url: str, **kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        raise httpx.ConnectError("simulated network failure")

    with patch(
        "app.core.github_app_oauth.read_github_app_oauth_credentials",
        return_value=("cid", "csecret"),
    ):
        with patch.object(httpx.AsyncClient, "post", _always_fail):
            with pytest.raises(UserTokenUnavailable) as exc_info:
                _run(get_user_access_token(session, user_id))

    exc = exc_info.value
    assert exc.user_id == user_id
    assert exc.reason == "refresh_transient"
    assert call_count == 2, f"Expected 2 POST attempts (retry once), got {call_count}"
    session.delete.assert_not_called()


# ---------------------------------------------------------------------------
# T03-5: Corrupt refresh token ciphertext → GitHubUserTokenDecryptError, no DELETE
# ---------------------------------------------------------------------------


def test_corrupt_refresh_token_raises_decrypt_error_no_delete() -> None:
    """The stored refresh_token_encrypted is corrupt (cannot decrypt).

    get_user_access_token must:
    - NOT call session.delete()
    - Raise GitHubUserTokenDecryptError
    """
    from app.core.github_user_tokens import (
        GitHubUserTokenDecryptError,
        encrypt_user_token,
        get_user_access_token,
    )

    user_id = uuid.uuid4()
    row = MagicMock()
    row.user_id = user_id
    row.access_token_encrypted = encrypt_user_token("ghu_Expired")
    row.access_token_expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    # Intentionally corrupt bytes — not a valid Fernet token
    row.refresh_token_encrypted = b"corrupt_not_fernet"
    row.refresh_token_expires_at = datetime.now(timezone.utc) + timedelta(days=5)

    session = _make_async_session_with_ops(row)

    with pytest.raises(GitHubUserTokenDecryptError) as exc_info:
        _run(get_user_access_token(session, user_id))

    assert exc_info.value.user_id == user_id
    session.delete.assert_not_called()
