"""Unit tests for install-state JWT helpers (M006 / S02 / T01).

Tests the `_mint_install_state` / `_decode_install_state` contract
introduced in M006-S02: the state JWT now carries a `user_id` claim so
the install callback can attribute the GitHub installation to the
Perpetuity user who initiated it.

These are pure unit tests — no database, no HTTP client. They import the
helpers directly and call them with synthetic UUIDs + the test SECRET_KEY.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import HTTPException

from app.api.routes.github import (
    _STATE_ALGO,
    _STATE_AUD,
    _STATE_ISS,
    _decode_install_state,
    _mint_install_state,
)
from app.core.config import settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _raw_encode(payload: dict) -> str:
    """Encode a JWT with the app SECRET_KEY (bypasses helper validation)."""
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=_STATE_ALGO)


def _valid_payload(
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    exp_delta: int = 600,
) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "team_id": str(team_id),
        "user_id": str(user_id),
        "jti": "deadbeef12345678",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=exp_delta)).timestamp()),
        "iss": _STATE_ISS,
        "aud": _STATE_AUD,
    }


# ---------------------------------------------------------------------------
# Round-trip: user_id is preserved end-to-end
# ---------------------------------------------------------------------------


def test_mint_and_decode_roundtrip_preserves_user_id() -> None:
    """_mint_install_state embeds user_id; _decode_install_state returns it."""
    team_id = uuid.uuid4()
    user_id = uuid.uuid4()

    token, exp, jti = _mint_install_state(team_id, user_id)

    assert isinstance(token, str) and token
    assert isinstance(exp, datetime)
    assert isinstance(jti, str) and jti

    payload = _decode_install_state(token)

    assert payload["team_id"] == str(team_id)
    assert payload["user_id"] == str(user_id)
    assert payload["jti"] == jti
    assert payload["iss"] == _STATE_ISS
    assert payload["aud"] == _STATE_AUD


def test_mint_exp_is_approximately_10_minutes_from_now() -> None:
    """Minted token expires ~10 minutes in the future."""
    team_id = uuid.uuid4()
    user_id = uuid.uuid4()

    before = datetime.now(timezone.utc)
    token, exp, _ = _mint_install_state(team_id, user_id)
    after = datetime.now(timezone.utc)

    assert before + timedelta(seconds=590) <= exp <= after + timedelta(seconds=610)


# ---------------------------------------------------------------------------
# Missing user_id → install_state_user_unknown
# ---------------------------------------------------------------------------


def test_decode_rejects_token_missing_user_id() -> None:
    """JWT without user_id claim raises HTTPException 400 install_state_user_unknown."""
    team_id = uuid.uuid4()
    user_id = uuid.uuid4()
    payload = _valid_payload(team_id, user_id)
    del payload["user_id"]

    token = _raw_encode(payload)

    with pytest.raises(HTTPException) as exc_info:
        _decode_install_state(token)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "install_state_user_unknown"


def test_decode_rejects_token_with_empty_user_id() -> None:
    """JWT with user_id='' raises HTTPException 400 install_state_user_unknown."""
    team_id = uuid.uuid4()
    user_id = uuid.uuid4()
    payload = _valid_payload(team_id, user_id)
    payload["user_id"] = ""

    token = _raw_encode(payload)

    with pytest.raises(HTTPException) as exc_info:
        _decode_install_state(token)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "install_state_user_unknown"


def test_decode_rejects_token_with_null_user_id() -> None:
    """JWT with user_id=None raises HTTPException 400 install_state_user_unknown."""
    team_id = uuid.uuid4()
    user_id = uuid.uuid4()
    payload = _valid_payload(team_id, user_id)
    payload["user_id"] = None

    token = _raw_encode(payload)

    with pytest.raises(HTTPException) as exc_info:
        _decode_install_state(token)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "install_state_user_unknown"


# ---------------------------------------------------------------------------
# Malformed user_id → install_state_user_unknown
# ---------------------------------------------------------------------------


def test_decode_rejects_token_with_non_uuid_user_id() -> None:
    """JWT with user_id='not-a-uuid' raises HTTPException 400 install_state_user_unknown."""
    team_id = uuid.uuid4()
    user_id = uuid.uuid4()
    payload = _valid_payload(team_id, user_id)
    payload["user_id"] = "not-a-uuid"

    token = _raw_encode(payload)

    with pytest.raises(HTTPException) as exc_info:
        _decode_install_state(token)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "install_state_user_unknown"


def test_decode_rejects_token_with_integer_user_id() -> None:
    """JWT with user_id=12345 (int) raises HTTPException 400 install_state_user_unknown."""
    team_id = uuid.uuid4()
    user_id = uuid.uuid4()
    payload = _valid_payload(team_id, user_id)
    payload["user_id"] = 12345

    token = _raw_encode(payload)

    with pytest.raises(HTTPException) as exc_info:
        _decode_install_state(token)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "install_state_user_unknown"


def test_decode_rejects_token_with_random_string_user_id() -> None:
    """JWT with user_id='abc123xyz' raises HTTPException 400 install_state_user_unknown."""
    team_id = uuid.uuid4()
    user_id = uuid.uuid4()
    payload = _valid_payload(team_id, user_id)
    payload["user_id"] = "abc123xyz"

    token = _raw_encode(payload)

    with pytest.raises(HTTPException) as exc_info:
        _decode_install_state(token)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "install_state_user_unknown"


# ---------------------------------------------------------------------------
# Pre-existing failure modes still work
# ---------------------------------------------------------------------------


def test_decode_rejects_empty_state_token() -> None:
    """Empty string raises HTTPException 400 install_state_invalid."""
    with pytest.raises(HTTPException) as exc_info:
        _decode_install_state("")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "install_state_invalid"


def test_decode_rejects_expired_token() -> None:
    """Expired JWT raises HTTPException 400 install_state_expired."""
    team_id = uuid.uuid4()
    user_id = uuid.uuid4()
    payload = _valid_payload(team_id, user_id, exp_delta=-1)

    token = _raw_encode(payload)

    with pytest.raises(HTTPException) as exc_info:
        _decode_install_state(token)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "install_state_expired"


def test_decode_rejects_bad_signature_token() -> None:
    """JWT signed with a wrong key raises HTTPException 400 install_state_invalid."""
    team_id = uuid.uuid4()
    user_id = uuid.uuid4()
    payload = _valid_payload(team_id, user_id)
    token = jwt.encode(payload, "wrong-secret", algorithm=_STATE_ALGO)

    with pytest.raises(HTTPException) as exc_info:
        _decode_install_state(token)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "install_state_invalid"
