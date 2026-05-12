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

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.encryption import SystemSettingDecryptError, decrypt_setting, encrypt_setting

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_ACCESS_TOKEN_SKEW_SECONDS: int = 60
_GITHUB_TOKEN_URL: str = "https://github.com/login/oauth/access_token"


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


async def get_user_access_token(session: Any, user_id: uuid.UUID) -> str:
    """Return a valid GitHub OAuth access token for *user_id*.

    Happy path (no GitHub call):
        Row exists AND ``now() < access_token_expires_at - 60s``  →
        decrypt and return the stored access token.

    Row-missing path:
        No row in ``github_user_oauth_tokens`` for *user_id*  →
        raise ``UserTokenUnavailable(reason="row_missing")``.

    Expired-access path (refresh required):
        Handled by T03 — this stub raises ``UserTokenUnavailable`` with
        reason ``"refresh_required"`` so the branch is clearly
        distinguishable from the two paths above during T02 testing.

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

    # Refresh path — T03 will implement this.  Raise a stub reason so T02
    # tests can confirm this branch is NOT reached in the happy/row_missing
    # cases.
    raise UserTokenUnavailable(user_id=user_id, reason="refresh_required")
