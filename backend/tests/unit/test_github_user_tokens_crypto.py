"""Unit tests for app.core.github_user_tokens crypto helpers.

Six test cases:
  1. encrypt_user_token / decrypt_user_token round-trip returns exact plaintext.
  2. Encrypted bytes do NOT contain the plaintext string.
  3. GitHubUserTokenDecryptError raised on corrupted ciphertext.
  4. GitHubUserTokenDecryptError is distinct from SystemSettingDecryptError
     (different exception class — future ERROR logs can pinpoint the table).
  5. GitHubUserTokenDecryptError accepts optional user_id; message includes it.
  6. test_model_registered — GitHubUserOAuthToken is registered in SQLModel
     metadata and has the expected __tablename__.

All tests patch SYSTEM_SETTINGS_ENCRYPTION_KEY so no real env var is needed.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

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
# Case 1: round-trip correctness
# ---------------------------------------------------------------------------


def test_round_trip_returns_exact_plaintext() -> None:
    from app.core.github_user_tokens import decrypt_user_token, encrypt_user_token

    plaintext = "ghs_MySecretAccessToken1234567890"
    assert decrypt_user_token(encrypt_user_token(plaintext)) == plaintext


# ---------------------------------------------------------------------------
# Case 2: ciphertext does not leak plaintext
# ---------------------------------------------------------------------------


def test_encrypted_bytes_do_not_contain_plaintext() -> None:
    from app.core.github_user_tokens import encrypt_user_token

    plaintext = "ghs_LeakyTokenCanary"
    cipher = encrypt_user_token(plaintext)
    assert plaintext.encode() not in cipher


# ---------------------------------------------------------------------------
# Case 3: bad ciphertext raises GitHubUserTokenDecryptError
# ---------------------------------------------------------------------------


def test_decrypt_bad_ciphertext_raises_github_decrypt_error() -> None:
    from app.core.github_user_tokens import GitHubUserTokenDecryptError, decrypt_user_token

    with pytest.raises(GitHubUserTokenDecryptError):
        decrypt_user_token(b"this-is-not-a-valid-fernet-token")


# ---------------------------------------------------------------------------
# Case 4: GitHubUserTokenDecryptError is distinct from SystemSettingDecryptError
# ---------------------------------------------------------------------------


def test_decrypt_error_is_distinct_from_system_setting_error() -> None:
    from app.core.encryption import SystemSettingDecryptError
    from app.core.github_user_tokens import GitHubUserTokenDecryptError

    assert GitHubUserTokenDecryptError is not SystemSettingDecryptError
    assert not issubclass(GitHubUserTokenDecryptError, SystemSettingDecryptError)
    assert not issubclass(SystemSettingDecryptError, GitHubUserTokenDecryptError)


# ---------------------------------------------------------------------------
# Case 5: GitHubUserTokenDecryptError carries optional user_id
# ---------------------------------------------------------------------------


def test_decrypt_error_accepts_optional_user_id() -> None:
    from app.core.github_user_tokens import GitHubUserTokenDecryptError

    uid = uuid.uuid4()

    err_with_id = GitHubUserTokenDecryptError(user_id=uid)
    assert err_with_id.user_id == uid
    assert str(uid) in str(err_with_id)

    err_no_id = GitHubUserTokenDecryptError()
    assert err_no_id.user_id is None
    assert "github user token decrypt failed" in str(err_no_id)


# ---------------------------------------------------------------------------
# Case 6: model registered in SQLModel metadata
# ---------------------------------------------------------------------------


def test_model_registered() -> None:
    from sqlmodel import SQLModel

    from app.models import GitHubUserOAuthToken

    assert GitHubUserOAuthToken.__tablename__ == "github_user_oauth_tokens"
    assert "github_user_oauth_tokens" in SQLModel.metadata.tables
