"""Unit tests for app.core.github_app_oauth.read_github_app_oauth_credentials.

Three test cases:
  1. Happy path — both rows present and decryptable → returns (client_id, client_secret).
  2. Missing client_id row → HTTPException 503 github_app_not_configured.
  3. client_secret present but decrypt fails → HTTPException 503 github_app_credential_error.

All tests inject their own Fernet key via monkeypatch so no real env var is
required (same approach as test_github_user_tokens_crypto.py).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet, InvalidToken

from app.api.routes.admin import (
    GITHUB_APP_CLIENT_ID_KEY,
    GITHUB_APP_CLIENT_SECRET_KEY,
)
from app.core.encryption import encrypt_setting
from app.models import SystemSetting

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


def _make_full_session(
    client_id: str = "Iv1.test-client-id",
    client_secret: str = "supersecret",
) -> MagicMock:
    """Return a mock session with both credential rows populated."""
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


def _make_session_missing_client_id() -> MagicMock:
    """Return a mock session where the client_id row is absent."""

    def _get(model_cls: type, key: str) -> SystemSetting | None:  # noqa: ARG001
        return None

    session = MagicMock()
    session.get.side_effect = _get
    return session


def _make_session_bad_secret(client_id: str = "Iv1.test-client-id") -> MagicMock:
    """Return a mock session where client_id is fine but the secret ciphertext is corrupt."""
    client_id_row = SystemSetting(
        key=GITHUB_APP_CLIENT_ID_KEY,
        value=client_id,
        value_encrypted=None,
        sensitive=False,
        has_value=True,
    )
    # Use a bad ciphertext that will fail Fernet decryption
    bad_ciphertext = b"this-is-not-a-valid-fernet-token"
    secret_row = SystemSetting(
        key=GITHUB_APP_CLIENT_SECRET_KEY,
        value=None,
        value_encrypted=bad_ciphertext,
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


# ---------------------------------------------------------------------------
# Case 1: Happy path
# ---------------------------------------------------------------------------


def test_happy_path_returns_client_id_and_secret() -> None:
    """Both rows present and decryptable → returns (client_id, client_secret)."""
    from app.core.github_app_oauth import read_github_app_oauth_credentials

    session = _make_full_session(
        client_id="Iv1.my-real-client-id",
        client_secret="my-real-secret",
    )
    client_id, client_secret = read_github_app_oauth_credentials(session)

    assert client_id == "Iv1.my-real-client-id"
    assert client_secret == "my-real-secret"


# ---------------------------------------------------------------------------
# Case 2: Missing client_id row
# ---------------------------------------------------------------------------


def test_missing_client_id_raises_503_not_configured() -> None:
    """client_id row absent → HTTPException 503 github_app_not_configured."""
    from fastapi import HTTPException

    from app.core.github_app_oauth import read_github_app_oauth_credentials

    session = _make_session_missing_client_id()
    with pytest.raises(HTTPException) as exc_info:
        read_github_app_oauth_credentials(session)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "github_app_not_configured"


# ---------------------------------------------------------------------------
# Case 3: Decrypt failure on client_secret
# ---------------------------------------------------------------------------


def test_decrypt_failure_raises_503_credential_error() -> None:
    """Corrupt ciphertext → HTTPException 503 github_app_credential_error."""
    from fastapi import HTTPException

    from app.core.github_app_oauth import read_github_app_oauth_credentials

    session = _make_session_bad_secret()
    with pytest.raises(HTTPException) as exc_info:
        read_github_app_oauth_credentials(session)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "github_app_credential_error"
