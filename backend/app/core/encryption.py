"""Fernet-backed encryption substrate for sensitive system_settings (M004/S01).

Per D020 (architecture, MEM224) we use Fernet (cryptography.fernet) rather
than raw AES-GCM because Fernet is library-vetted, manages its own nonce,
and gives us authenticated encryption with rotation-friendly token format
without manual primitive composition.

Module shape (mirrored 1:1 in orchestrator/orchestrator/encryption.py so
S02 has a stable import target on the orchestrator side):

    encrypt_setting(plaintext: str) -> bytes
    decrypt_setting(ciphertext: bytes) -> str
    SystemSettingDecryptError(key: str | None)

Loader contract:

  * `_load_key()` is module-level and lazy (`@functools.cache`). Importing
    this module does NOT crash when no sensitive key has been registered
    yet — validation fires on the first encrypt/decrypt call. This keeps
    the test/dev experience sane while preserving fail-loud semantics for
    the production hot path.
  * Reads `SYSTEM_SETTINGS_ENCRYPTION_KEY` from the process env; the value
    must be 32 url-safe base64 bytes (i.e. the exact `Fernet.generate_key()`
    output shape, 44 url-safe base64 chars decoding to 32 raw bytes).
  * If the env var is unset or malformed, raises RuntimeError naming the
    env var. The handler at the FastAPI layer translates that into the
    structured ERROR log + 503 response.

Key rotation is intentionally out of scope for M004; rotating the
SYSTEM_SETTINGS_ENCRYPTION_KEY without re-encrypting every sensitive row
breaks every subsequent decrypt. The operator runbook covering rotation
lands in S07.

The orchestrator-side mirror is intentionally a copy rather than a shared
package import: the two services have different config surfaces
(`pydantic_settings.BaseSettings` in orchestrator vs. `os.environ` plus
`Settings` in backend) and a shared package would force a new packaging
boundary in M004 that nothing else in the milestone needs.
"""

from __future__ import annotations

import base64
import functools
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("app.core.encryption")

ENV_VAR = "SYSTEM_SETTINGS_ENCRYPTION_KEY"


class SystemSettingDecryptError(Exception):
    """Raised when a Fernet ciphertext fails to decrypt.

    `key` is the system_settings row key whose value failed to decrypt; it
    is attached by the call site (the encryption module itself does not
    know which row a ciphertext belongs to). The handler at the FastAPI
    layer translates this into the `system_settings_decrypt_failed
    key=<name>` ERROR log + a 503 response. The plaintext value MUST NOT
    appear in the exception message or any log line.
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

    Cached so the validation cost (env read + base64 decode + Fernet
    construction) is paid once per process. `functools.cache` returns the
    same Fernet instance for every call, which is safe because Fernet
    holds no per-call state.

    Emits a one-shot INFO `system_settings_encryption_loaded
    key_prefix=<first_4_chars>` so operators can confirm the loader fired
    and which key is active. The prefix is intentionally truncated to 4
    chars so the log proves the key changed without leaking the full
    secret.
    """
    raw = os.environ.get(ENV_VAR)
    if not raw:
        raise RuntimeError(
            f"{ENV_VAR} is not set — sensitive system_settings cannot be "
            "encrypted or decrypted without it. Generate one with "
            "`python -c 'from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())'` and set it on the "
            "backend and orchestrator services."
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

    Returns the Fernet token as bytes (suitable for storage in the
    `system_settings.value_encrypted` BYTEA column). The token is itself
    url-safe base64 but we keep it as bytes since the column type is
    BYTEA and round-tripping through str adds no value.
    """
    return _load_key().encrypt(plaintext.encode("utf-8"))


def decrypt_setting(ciphertext: bytes) -> str:
    """Decrypt a Fernet token back to the original plaintext.

    Raises `SystemSettingDecryptError` (key=None — the caller attaches
    the row key) on `cryptography.fernet.InvalidToken`. Callers SHOULD
    catch this, re-raise with the row key attached, and translate to a
    503 with the structured ERROR log. The plaintext MUST NOT appear in
    any error message.
    """
    try:
        return _load_key().decrypt(ciphertext).decode("utf-8")
    except InvalidToken as exc:
        raise SystemSettingDecryptError(key=None) from exc
