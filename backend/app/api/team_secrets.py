"""Team-scoped secret service helpers (M005/S01/T02).

Boundary owned by S01: every downstream slice (S02–S06) that calls Claude
or Codex reads its API key from `team_secrets` via `get_team_secret`. The
helpers here are the *only* call sites that touch `decrypt_setting` for
team-scoped rows — routes never decrypt directly, the test-only round-trip
endpoint (T05) goes through `get_team_secret`, and the orchestrator's
mirrored module (when it lands) will follow the same contract.

Decrypt-only-at-call-site discipline (mirrors M004/S01): the row's plaintext
exists only inside `get_team_secret`'s return value; never logged, never
attached to an exception message, never serialized. The two custom
exceptions (`MissingTeamSecretError`, `TeamSecretDecryptError`) carry just
`team_id` and `key` so the API layer in T03 can translate them to 404 /
503 with the structured error shape the slice plan locks in.

The upsert is a single `INSERT … ON CONFLICT (team_id, key) DO UPDATE`
statement — the row's composite PK matches that conflict target exactly,
so the upsert is a single SQL round-trip and there is no read-then-write
race window. `commit()` is owned by `set_team_secret` / `delete_team_secret`;
read-only helpers leave the session uncommitted.
"""

from __future__ import annotations

import logging
import uuid

from cryptography.fernet import InvalidToken
from sqlalchemy import text
from sqlmodel import Session, select

from app.api.team_secrets_registry import lookup, registered_keys
from app.core.encryption import (
    SystemSettingDecryptError,
    decrypt_setting,
    encrypt_setting,
)
from app.models import TeamSecret, TeamSecretStatus

logger = logging.getLogger("app.api.team_secrets")


class MissingTeamSecretError(Exception):
    """Raised when `get_team_secret` is called with no row for (team_id, key).

    The API layer (T03) translates this to 404 with
    `{detail: "team_secret_not_set", key}`. Downstream callers in S02+ catch
    this directly and surface a step failure with
    `error_class='missing_team_secret'` (slice plan integration closure).

    Carries `team_id` and `key` for context but never the value (there is
    none) and never any prefix.
    """

    def __init__(self, team_id: uuid.UUID, key: str) -> None:
        self.team_id = team_id
        self.key = key
        super().__init__(
            f"team secret not set (team_id={team_id} key={key!r})"
        )


class TeamSecretDecryptError(Exception):
    """Raised when a row's `value_encrypted` fails Fernet decryption.

    Mirrors M004's `SystemSettingDecryptError` but team-scoped. The API
    layer's global handler (T03) translates this into a 503 with
    `{detail: "team_secret_decrypt_failed", key}` and an ERROR log line
    `team_secret_decrypt_failed team_id=<...> key=<...>`. The exception
    message MUST NOT carry the ciphertext, the plaintext, or any prefix.

    Distinct from `SystemSettingDecryptError` so the error-class taxonomy
    in dashboards and log searches stays unambiguous between system-scoped
    and team-scoped decrypt failures.
    """

    def __init__(self, team_id: uuid.UUID, key: str) -> None:
        self.team_id = team_id
        self.key = key
        super().__init__(
            f"team secret decrypt failed (team_id={team_id} key={key!r})"
        )


def set_team_secret(
    session: Session, team_id: uuid.UUID, key: str, plaintext: str
) -> None:
    """Validate, encrypt, and upsert a team_secret. Commits.

    Raises:
      * `UnregisteredTeamSecretKeyError` if `key` is not in `_VALIDATORS`.
      * `InvalidTeamSecretValueError` if the validator rejects the plaintext.

    On success the row carries `has_value=TRUE`, `sensitive=TRUE`,
    `updated_at=NOW()`. Existing rows for (team_id, key) are overwritten in
    place — the composite PK is the upsert conflict target.

    The plaintext lives in this function's frame only long enough to hand
    to `encrypt_setting`; the resulting ciphertext is what touches the DB.
    """
    spec = lookup(key)  # raises UnregisteredTeamSecretKeyError
    spec.validator(plaintext)  # raises InvalidTeamSecretValueError

    ciphertext = encrypt_setting(plaintext)

    upsert = text(
        """
        INSERT INTO team_secrets
            (team_id, key, value_encrypted, has_value, sensitive,
             created_at, updated_at)
        VALUES
            (:team_id, :key, :ct, TRUE, :sensitive, NOW(), NOW())
        ON CONFLICT (team_id, key) DO UPDATE
        SET value_encrypted = EXCLUDED.value_encrypted,
            has_value = TRUE,
            sensitive = EXCLUDED.sensitive,
            updated_at = NOW()
        """
    )
    session.execute(
        upsert,
        {
            "team_id": team_id,
            "key": key,
            "ct": ciphertext,
            "sensitive": spec.sensitive,
        },
    )
    session.commit()


def get_team_secret(
    session: Session, team_id: uuid.UUID, key: str
) -> str:
    """Return the decrypted plaintext for (team_id, key).

    Raises:
      * `MissingTeamSecretError` if there is no row.
      * `TeamSecretDecryptError` if Fernet decryption fails (corrupted
        ciphertext, key rotated without re-encrypt, etc.). Logs an ERROR
        line `team_secret_decrypt_failed team_id=<...> key=<...>` here so
        the failure is recorded even if the caller swallows the exception.

    Does not validate the key against the registry — callers that read a
    secret have already gone through PUT (which validates), and a row with
    a key not in the current registry is still a legitimate stored secret
    until DELETE clears it. This matches the M004/S01 shape: decrypt is a
    storage-layer operation, not a registry-layer one.
    """
    row = session.get(TeamSecret, (team_id, key))
    if row is None:
        raise MissingTeamSecretError(team_id, key)

    try:
        return decrypt_setting(row.value_encrypted)
    except SystemSettingDecryptError as exc:
        # SystemSettingDecryptError wraps InvalidToken without a key — we
        # re-raise as the team-scoped exception so the API layer's handler
        # has the right error shape. Log here (not at the call site) so a
        # caller that catches and re-tries doesn't silently lose the
        # corruption signal.
        logger.error(
            "team_secret_decrypt_failed team_id=%s key=%s",
            team_id,
            key,
        )
        raise TeamSecretDecryptError(team_id, key) from exc
    except InvalidToken as exc:
        # Defense in depth: if the encryption module's contract changes and
        # `decrypt_setting` ever leaks a raw `InvalidToken`, treat it the
        # same as the wrapped variant. Without this branch a contract drift
        # would surface as a 500.
        logger.error(
            "team_secret_decrypt_failed team_id=%s key=%s",
            team_id,
            key,
        )
        raise TeamSecretDecryptError(team_id, key) from exc


def delete_team_secret(
    session: Session, team_id: uuid.UUID, key: str
) -> bool:
    """Remove (team_id, key) from team_secrets. Commits.

    Returns True if a row was deleted, False if no row existed (idempotent —
    the API DELETE returns 204 either way per the slice plan, but the bool
    lets the route emit a different log line when the call was a no-op).

    Does not validate against the registry: deleting a row whose key is no
    longer registered is exactly the cleanup operators want.
    """
    row = session.get(TeamSecret, (team_id, key))
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


def list_team_secret_status(
    session: Session, team_id: uuid.UUID
) -> list[TeamSecretStatus]:
    """Return one TeamSecretStatus per registered key for `team_id`.

    Keys absent from the team's row set come back with `has_value=False`,
    `updated_at=None`, `sensitive=spec.sensitive`. This shape lets the
    frontend panel render a row for every registered key without a second
    round-trip, and it's what the GET-list route returns verbatim in T03.

    Order matches `registered_keys()` (declaration order in
    `_VALIDATORS`), so the panel's row order is stable across deployments.
    """
    # One round-trip pull of every existing row for this team; the registry
    # is small (2 keys today, ~5 expected long-term) so a per-key
    # session.get would be fine, but this scales cleanly if the registry
    # grows.
    existing = {
        row.key: row
        for row in session.exec(
            select(TeamSecret).where(TeamSecret.team_id == team_id)
        ).all()
    }

    out: list[TeamSecretStatus] = []
    for key in registered_keys():
        spec = lookup(key)
        row = existing.get(key)
        if row is None:
            out.append(
                TeamSecretStatus(
                    key=key,
                    has_value=False,
                    sensitive=spec.sensitive,
                    updated_at=None,
                )
            )
        else:
            out.append(
                TeamSecretStatus(
                    key=key,
                    has_value=row.has_value,
                    sensitive=row.sensitive,
                    updated_at=row.updated_at,
                )
            )
    return out
