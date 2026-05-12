"""Fernet-backed encrypt/decrypt helpers for github_user_oauth_tokens,
plus the refresh-on-read helper get_user_access_token.

Public surface:

    encrypt_user_token(plain: str) -> bytes
    decrypt_user_token(cipher: bytes) -> str
    GitHubUserTokenDecryptError
    UserTokenUnavailable
    get_user_access_token(session, user_id) -> str   (async)

No Fernet constructor call here — all key management stays in encryption.py.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.core.encryption import SystemSettingDecryptError, decrypt_setting, encrypt_setting

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_ACCESS_TOKEN_SKEW_SECONDS: int = 60
_GITHUB_TOKEN_URL: str = "https://github.com/login/oauth/access_token"
_ORCH_TIMEOUT: httpx.Timeout = httpx.Timeout(10.0, connect=3.0)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class GitHubUserTokenDecryptError(Exception):
    """Raised when a github_user_oauth_tokens ciphertext fails to decrypt.

    Distinct from SystemSettingDecryptError so an ERROR log line can name
    which table's token is corrupt without sharing exception class with the
    system_settings path.

    `user_id` is optional; attach it at the call site when the row's
    user_id is known so the ERROR log can include it for incident triage.
    The ciphertext and plaintext MUST NOT appear in any log line.
    """

    def __init__(self, user_id: uuid.UUID | None = None) -> None:
        self.user_id = user_id
        super().__init__(
            f"github user token decrypt failed (user_id={user_id!r})"
            if user_id is not None
            else "github user token decrypt failed"
        )


class UserTokenUnavailable(Exception):
    """Raised when a valid access token for a user cannot be returned.

    Reasons include:
      - "row_missing"       — no github_user_oauth_tokens row for user_id
      - "bad_refresh_token" — GitHub rejected the refresh token
      - "refresh_rejected"  — GitHub returned an unexpected error payload
      - "refresh_unexpected_response" — response body was not parseable
      - "refresh_transient" — network-level failure (row NOT deleted)

    `user_id` and `reason` are always set so callers can log/map to HTTP.
    """

    def __init__(self, user_id: uuid.UUID, reason: str) -> None:
        self.user_id = user_id
        self.reason = reason
        super().__init__(
            f"github user token unavailable (user_id={user_id!r} reason={reason!r})"
        )


# ---------------------------------------------------------------------------
# Encrypt / decrypt helpers
# ---------------------------------------------------------------------------


def encrypt_user_token(plain: str) -> bytes:
    """Encrypt a GitHub access or refresh token for BYTEA storage."""
    return encrypt_setting(plain)


def decrypt_user_token(cipher: bytes) -> str:
    """Decrypt a BYTEA ciphertext back to the original token string.

    Raises `GitHubUserTokenDecryptError` on InvalidToken so callers can
    distinguish a user-token failure from a system-settings failure in
    logs and error handling.
    """
    try:
        return decrypt_setting(cipher)
    except SystemSettingDecryptError as exc:
        raise GitHubUserTokenDecryptError() from exc


# ---------------------------------------------------------------------------
# get_user_access_token
# ---------------------------------------------------------------------------


async def _refresh_user_token(
    session: Any,
    row: Any,
    user_id: uuid.UUID,
) -> str:
    """POST to GitHub to exchange a refresh token for a new access token.

    On success: encrypt and persist both new tokens + expiry timestamps,
    commit, log, and return the new plaintext access token.

    On any GitHub-reported failure: DELETE the row, commit, log, and raise
    UserTokenUnavailable with the appropriate reason.

    On network-class failure: do NOT delete; retry once, then raise
    UserTokenUnavailable(reason="refresh_transient").

    Raises:
        GitHubUserTokenDecryptError: if the stored refresh token ciphertext
            cannot be decrypted (row is NOT deleted — it may be a transient
            key issue).
        UserTokenUnavailable: for all other non-happy outcomes.
    """
    from app.core.github_app_oauth import read_github_app_oauth_credentials
    from app.models import GitHubUserOAuthToken

    logger.info(
        "github_user_token_refresh_attempted user_id=%s",
        user_id,
    )

    # Decrypt the stored refresh token — re-raise on corrupt ciphertext.
    if row.refresh_token_encrypted is None:
        await session.delete(row)
        await session.commit()
        logger.warning(
            "github_user_token_refresh_failed user_id=%s reason=bad_refresh_token",
            user_id,
        )
        raise UserTokenUnavailable(user_id=user_id, reason="bad_refresh_token")

    try:
        refresh_token_plain = decrypt_user_token(bytes(row.refresh_token_encrypted))
    except GitHubUserTokenDecryptError:
        # Corrupt ciphertext — re-raise without deleting; may be a key issue.
        raise GitHubUserTokenDecryptError(user_id=user_id)

    client_id, client_secret = read_github_app_oauth_credentials(session)

    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token_plain,
    }

    async def _post_once() -> httpx.Response:
        async with httpx.AsyncClient(timeout=_ORCH_TIMEOUT) as c:
            return await c.post(
                _GITHUB_TOKEN_URL,
                json=payload,
                headers={"Accept": "application/json"},
            )

    # Attempt with one retry on network-class errors.
    try:
        resp = await _post_once()
    except httpx.HTTPError:
        try:
            resp = await _post_once()
        except httpx.HTTPError:
            logger.warning(
                "github_user_token_refresh_failed user_id=%s reason=refresh_transient",
                user_id,
            )
            raise UserTokenUnavailable(user_id=user_id, reason="refresh_transient")

    # Parse the response body.
    try:
        body = resp.json()
    except ValueError:
        await session.delete(row)
        await session.commit()
        logger.warning(
            "github_user_token_refresh_failed user_id=%s reason=refresh_unexpected_response",
            user_id,
        )
        raise UserTokenUnavailable(user_id=user_id, reason="refresh_unexpected_response")

    # GitHub signals refresh-token rejection via error field in 200 body.
    error = body.get("error")
    if error == "bad_refresh_token":
        await session.delete(row)
        await session.commit()
        logger.warning(
            "github_user_token_refresh_failed user_id=%s reason=bad_refresh_token",
            user_id,
        )
        raise UserTokenUnavailable(user_id=user_id, reason="bad_refresh_token")

    if error is not None:
        await session.delete(row)
        await session.commit()
        logger.warning(
            "github_user_token_refresh_failed user_id=%s reason=refresh_rejected",
            user_id,
        )
        raise UserTokenUnavailable(user_id=user_id, reason="refresh_rejected")

    # Validate required fields in success body.
    new_access_token = body.get("access_token")
    new_refresh_token = body.get("refresh_token")
    expires_in = body.get("expires_in")
    refresh_expires_in = body.get("refresh_token_expires_in")

    if (
        not new_access_token
        or not isinstance(new_access_token, str)
        or not new_refresh_token
        or not isinstance(new_refresh_token, str)
    ):
        await session.delete(row)
        await session.commit()
        logger.warning(
            "github_user_token_refresh_failed user_id=%s reason=refresh_unexpected_response",
            user_id,
        )
        raise UserTokenUnavailable(user_id=user_id, reason="refresh_unexpected_response")

    # Persist new tokens atomically.
    now = datetime.now(timezone.utc)
    row.access_token_encrypted = encrypt_user_token(new_access_token)
    row.refresh_token_encrypted = encrypt_user_token(new_refresh_token)
    row.access_token_expires_at = (
        now + timedelta(seconds=int(expires_in)) if isinstance(expires_in, (int, float)) else None
    )
    row.refresh_token_expires_at = (
        now + timedelta(seconds=int(refresh_expires_in))
        if isinstance(refresh_expires_in, (int, float))
        else None
    )
    row.updated_at = now
    await session.commit()

    logger.info(
        "github_user_token_refreshed user_id=%s new_token_prefix=%s",
        user_id,
        new_access_token[:4],
    )
    return new_access_token


async def get_user_access_token(session: Any, user_id: uuid.UUID) -> str:
    """Return a valid GitHub OAuth access token for *user_id*.

    Happy path (no GitHub call):
        Row exists AND ``now() < access_token_expires_at - 60s``  →
        decrypt and return the stored access token.

    Row-missing path:
        No row in ``github_user_oauth_tokens`` for *user_id*  →
        raise ``UserTokenUnavailable(reason="row_missing")``.

    Expired-access path:
        Row exists, access token expired → POST to GitHub with the stored
        refresh token. On success returns the new plaintext and updates the
        row.  On failure raises ``UserTokenUnavailable`` with a reason string
        (or ``GitHubUserTokenDecryptError`` if the refresh token ciphertext is
        corrupt).

    Args:
        session: An async SQLAlchemy/SQLModel session.
        user_id: The Perpetuity user whose token is needed.

    Returns:
        The plaintext GitHub access token string.

    Raises:
        UserTokenUnavailable: When the token is absent or cannot be returned.
        GitHubUserTokenDecryptError: When the stored ciphertext is corrupt.
    """
    from app.models import GitHubUserOAuthToken

    row: GitHubUserOAuthToken | None = await session.get(GitHubUserOAuthToken, user_id)

    if row is None:
        raise UserTokenUnavailable(user_id=user_id, reason="row_missing")

    now = datetime.now(timezone.utc)
    skew = timedelta(seconds=_ACCESS_TOKEN_SKEW_SECONDS)

    if (
        row.access_token_expires_at is not None
        and row.access_token_encrypted is not None
        and now < row.access_token_expires_at - skew
    ):
        # Happy path: access token is still fresh — no GitHub call needed.
        return decrypt_user_token(bytes(row.access_token_encrypted))

    # Refresh path: access token is expired (or expiry is unknown).
    return await _refresh_user_token(session, row, user_id)
