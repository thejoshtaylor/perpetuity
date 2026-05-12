"""Fernet-backed encrypt/decrypt helpers for github_user_oauth_tokens.

Thin wrappers around encrypt_setting / decrypt_setting that re-raise
SystemSettingDecryptError as GitHubUserTokenDecryptError so a future ERROR
log line can pinpoint which table's ciphertext failed to decrypt.

Public surface (exactly three names):

    encrypt_user_token(plain: str) -> bytes
    decrypt_user_token(cipher: bytes) -> str
    GitHubUserTokenDecryptError

No Fernet constructor call here — all key management stays in encryption.py.
"""

from __future__ import annotations

import uuid

from app.core.encryption import SystemSettingDecryptError, decrypt_setting, encrypt_setting


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
