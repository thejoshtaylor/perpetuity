"""Read GitHub App OAuth credentials from system_settings.

Extracted from app.api.routes.github so the core refresh helper (M006/S03)
can read client_id + client_secret without importing from a route module.

Public surface (one name):

    read_github_app_oauth_credentials(session) -> tuple[str, str]

Raises HTTPException 503 on missing or unreadable credentials so the FastAPI
layer receives a well-typed response without any additional translation at the
call site.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

from app.api.routes.admin import (
    GITHUB_APP_CLIENT_ID_KEY,
    GITHUB_APP_CLIENT_SECRET_KEY,
)
from app.core.encryption import SystemSettingDecryptError, decrypt_setting
from app.models import SystemSetting

logger = logging.getLogger(__name__)


def read_github_app_oauth_credentials(session: Any) -> tuple[str, str]:
    """Return (client_id, client_secret) from system_settings.

    Reads `github_app_client_id` (plain JSONB string) and
    `github_app_client_secret` (Fernet-encrypted BYTEA) from the
    system_settings table via the supplied SQLModel/SQLAlchemy session.

    Raises:
        HTTPException(503, detail="github_app_not_configured") — if either
            row is absent, has no value, or has a value of the wrong type.
        HTTPException(503, detail="github_app_credential_error") — if the
            client_secret ciphertext fails to decrypt (key rotation mismatch
            or data corruption).

    Returns:
        (client_id, client_secret) as plain strings, ready for use in an
        OAuth token-exchange request.
    """
    # Read client_id (plain string, non-sensitive)
    client_id_row: SystemSetting | None = session.get(SystemSetting, GITHUB_APP_CLIENT_ID_KEY)
    if (
        client_id_row is None
        or not client_id_row.has_value
        or not isinstance(client_id_row.value, str)
        or not client_id_row.value
    ):
        logger.warning(
            "github_app_oauth_credentials_missing reason=client_id_not_configured"
        )
        raise HTTPException(
            status_code=503,
            detail="github_app_not_configured",
        )
    client_id: str = client_id_row.value

    # Read client_secret (Fernet-encrypted)
    secret_row: SystemSetting | None = session.get(SystemSetting, GITHUB_APP_CLIENT_SECRET_KEY)
    if secret_row is None or not secret_row.has_value or not secret_row.value_encrypted:
        logger.warning(
            "github_app_oauth_credentials_missing reason=client_secret_not_configured"
        )
        raise HTTPException(
            status_code=503,
            detail="github_app_not_configured",
        )
    try:
        client_secret = decrypt_setting(bytes(secret_row.value_encrypted))
    except SystemSettingDecryptError:
        logger.warning(
            "github_app_oauth_credentials_missing reason=client_secret_decrypt_error"
        )
        raise HTTPException(
            status_code=503,
            detail="github_app_credential_error",
        )

    return client_id, client_secret
