"""Fernet-backed encryption substrate for sensitive system_settings (M004/S01).

Mirror of `backend/app/core/encryption.py` (kept as a copy rather than a
shared package import — see the backend module's docstring for the
rationale). S02 will import `decrypt_setting` here when the orchestrator
reads the GitHub App private key from `system_settings`.

Shape mirrors the backend module 1:1:

    encrypt_setting(plaintext: str) -> bytes
    decrypt_setting(ciphertext: bytes) -> str
    SystemSettingDecryptError(key: str | None)

The orchestrator currently has no encrypt path of its own — it only
decrypts rows the backend wrote. `encrypt_setting` is exposed here for
shape symmetry (so a future orchestrator-side write path doesn't have to
reach into the backend tree) and so the round-trip verification test in
T01 can run identically against both modules.
"""

from __future__ import annotations

import base64
import functools
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("orchestrator.encryption")

ENV_VAR = "SYSTEM_SETTINGS_ENCRYPTION_KEY"


class SystemSettingDecryptError(Exception):
    """Raised when a Fernet ciphertext fails to decrypt.

    `key` is the system_settings row key whose value failed to decrypt;
    attached by the call site. The orchestrator's HTTPException handler
    translates this into the `system_settings_decrypt_failed key=<name>`
    ERROR log + a 503 response. Plaintext MUST NOT appear in the
    exception message or any log line.
    """

    def __init__(self, key: str | None = None) -> None:
        self.key = key
        super().__init__(
            f"system setting decrypt failed (key={key!r})"
            if key is not None
            else "system setting decrypt failed"
        )


@functools.cache
def _load_key() -> Fernet:
    """Load and validate SYSTEM_SETTINGS_ENCRYPTION_KEY from env.

    Same contract as the backend module: lazy + cached, fail-loud on
    missing/malformed key, single INFO log on first successful load with
    only the first 4 chars of the key as a non-secret fingerprint.
    """
    raw = os.environ.get(ENV_VAR)
    if not raw:
        raise RuntimeError(
            f"{ENV_VAR} is not set — sensitive system_settings cannot be "
            "decrypted on the orchestrator side. Set the same value used "
            "by the backend service (see compose env)."
        )

    raw_bytes = raw.encode("ascii") if isinstance(raw, str) else raw
    try:
        decoded = base64.urlsafe_b64decode(raw_bytes)
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            f"{ENV_VAR} is not valid url-safe base64: {exc}. Expected the "
            "44-char output of Fernet.generate_key()."
        ) from exc

    if len(decoded) != 32:
        raise RuntimeError(
            f"{ENV_VAR} must decode to exactly 32 bytes (Fernet key size); "
            f"got {len(decoded)} bytes."
        )

    try:
        fernet = Fernet(raw_bytes)
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            f"{ENV_VAR} is malformed and cannot construct a Fernet "
            f"instance: {exc}"
        ) from exc

    logger.info(
        "system_settings_encryption_loaded key_prefix=%s",
        raw[:4] + "...",
    )
    return fernet


def encrypt_setting(plaintext: str) -> bytes:
    """Encrypt `plaintext` with the configured Fernet key.

    Exposed for shape symmetry with the backend module; the orchestrator
    has no production write path today (S02 only reads).
    """
    return _load_key().encrypt(plaintext.encode("utf-8"))


def decrypt_setting(ciphertext: bytes) -> str:
    """Decrypt a Fernet token back to the original plaintext.

    Raises `SystemSettingDecryptError` (key=None — the caller attaches
    the row key). The orchestrator translates this into a 503 with the
    structured ERROR log; plaintext MUST NOT appear in any error
    message.
    """
    try:
        return _load_key().decrypt(ciphertext).decode("utf-8")
    except InvalidToken as exc:
        raise SystemSettingDecryptError(key=None) from exc
