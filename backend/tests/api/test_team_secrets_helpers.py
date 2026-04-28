"""Unit tests for `app.api.team_secrets` helpers (M005/S01/T02).

Covers the four service helpers (`set_team_secret`, `get_team_secret`,
`delete_team_secret`, `list_team_secret_status`) plus the validator registry
boundary. The decrypt-failure path tampers `value_encrypted` directly via
SQL and asserts that `get_team_secret` raises the team-scoped
`TeamSecretDecryptError` (NOT the system-scoped variant — that distinction
matters for log searches and dashboards per the slice plan).

Test isolation:
  * autouse `_set_encryption_key` mirrors the admin-settings unit test
    pattern (MEM231): pin a deterministic test-only Fernet key, clear the
    `@functools.cache` on `_load_key` before+after, so a stable encrypt
    key is in place for every test in this module without depending on
    whatever was loaded earlier in the session.
  * autouse `_clean_team_secrets` deletes every team_secrets row at setup
    and teardown. The session-scoped `db` fixture is shared across tests,
    so without this each test would inherit prior writes.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlmodel import Session, delete, select

from app.api.team_secrets import (
    MissingTeamSecretError,
    TeamSecretDecryptError,
    delete_team_secret,
    get_team_secret,
    list_team_secret_status,
    set_team_secret,
)
from app.api.team_secrets_registry import (
    CLAUDE_API_KEY,
    OPENAI_API_KEY,
    InvalidTeamSecretValueError,
    UnregisteredTeamSecretKeyError,
    lookup,
    registered_keys,
)
from app.models import Team, TeamSecret

# 44-char url-safe base64 Fernet key — a real one, generated once and
# pinned here so encrypt/decrypt round-trips are deterministic across
# every run of this module. Must match the shape `_load_key` validates
# (44 chars decoding to exactly 32 raw bytes).
_TEST_FERNET_KEY = "q14YMz9s4jrbfD29GvcRfe_4krg82w6_mPWUu_y3LTo="


@pytest.fixture(autouse=True)
def _set_encryption_key(monkeypatch):
    """Pin a deterministic SYSTEM_SETTINGS_ENCRYPTION_KEY for every test.

    Encryption is `@functools.cache`-pinned at module level — clearing the
    cache before AND after each test makes the env swap take effect now and
    leaves a clean slate for whatever tests run next in the session.
    """
    monkeypatch.setenv("SYSTEM_SETTINGS_ENCRYPTION_KEY", _TEST_FERNET_KEY)
    from app.core import encryption as _enc

    _enc._load_key.cache_clear()
    yield
    _enc._load_key.cache_clear()


@pytest.fixture(autouse=True)
def _clean_team_secrets(db: Session):
    """Drop every team_secrets row before and after each test.

    Cheaper than scoping to the team_id we created — it also catches any
    orphan rows a prior failing test left behind. team_secrets is fully
    test-owned; we never co-tenant production data into the unit DB.
    """
    db.execute(delete(TeamSecret))
    db.commit()
    yield
    db.execute(delete(TeamSecret))
    db.commit()


def _make_team(db: Session) -> Team:
    """Insert a fresh Team row for the test and return it.

    Each test gets a UUID-suffixed team so cross-test row contamination is
    impossible — the unit `db` fixture is session-scoped and Team rows are
    not autouse-cleaned, so a fixed suffix would collide on the second
    test run within a session.
    """
    suffix = uuid.uuid4().hex[:8]
    team = Team(
        name=f"team-secrets-test-{suffix}",
        slug=f"team-secrets-test-{suffix}",
    )
    db.add(team)
    db.commit()
    db.refresh(team)
    return team


def _valid_claude_key() -> str:
    # Real Anthropic-shape key body: prefix + 40 chars of payload, well
    # past the 40-char floor.
    return "sk-ant-" + ("A" * 40)


def _valid_openai_key() -> str:
    return "sk-" + ("B" * 40)


# ---------------------------------------------------------------------------
# Validator registry
# ---------------------------------------------------------------------------


def test_lookup_unknown_key_raises_unregistered_error() -> None:
    """`lookup` raises a typed KeyError subclass on unregistered keys.

    Returning `None` (or letting `dict[KeyError]` propagate) would let a
    typo at a call site silently behave like "skip validation" — the
    typed exception forces every call site to handle the missing-spec
    path explicitly.
    """
    with pytest.raises(UnregisteredTeamSecretKeyError) as exc_info:
        lookup("not_a_real_key")
    assert exc_info.value.key == "not_a_real_key"
    # Subclass of KeyError so existing `dict[key]`-style call sites still
    # match `except KeyError` if they don't know about the subtype.
    assert isinstance(exc_info.value, KeyError)


def test_registered_keys_locks_m005_set() -> None:
    """The closed registry for M005 is exactly Claude + OpenAI.

    Future slices add to this set; the test guards against accidental
    addition or removal landing without an explicit registry update.
    """
    assert registered_keys() == (CLAUDE_API_KEY, OPENAI_API_KEY)


def test_validator_rejects_bad_prefix() -> None:
    spec = lookup(CLAUDE_API_KEY)
    with pytest.raises(InvalidTeamSecretValueError) as exc_info:
        spec.validator("sk-wrong-" + ("A" * 40))
    assert exc_info.value.key == CLAUDE_API_KEY
    assert exc_info.value.reason == "bad_prefix"


def test_validator_rejects_too_short() -> None:
    spec = lookup(OPENAI_API_KEY)
    with pytest.raises(InvalidTeamSecretValueError) as exc_info:
        spec.validator("sk-short")
    assert exc_info.value.key == OPENAI_API_KEY
    assert exc_info.value.reason == "too_short"


def test_validator_rejects_non_string() -> None:
    spec = lookup(CLAUDE_API_KEY)
    with pytest.raises(InvalidTeamSecretValueError) as exc_info:
        spec.validator(12345)  # type: ignore[arg-type]
    assert exc_info.value.reason == "must_be_string"


def test_validator_accepts_valid_value() -> None:
    """Happy path — both registered validators accept their canonical shape."""
    lookup(CLAUDE_API_KEY).validator(_valid_claude_key())
    lookup(OPENAI_API_KEY).validator(_valid_openai_key())


# ---------------------------------------------------------------------------
# set_team_secret
# ---------------------------------------------------------------------------


def test_set_team_secret_round_trips_through_encrypt(db: Session) -> None:
    """`set_team_secret` then `get_team_secret` returns the original plaintext.

    Asserts (a) the row landed with `has_value=True`/`sensitive=True`, (b)
    `value_encrypted` is non-empty bytes (not the plaintext), and (c) the
    plaintext bytes do NOT appear in the ciphertext payload (defense
    against accidental no-op encryption).
    """
    team = _make_team(db)
    plaintext = _valid_claude_key()

    set_team_secret(db, team.id, CLAUDE_API_KEY, plaintext)

    row = db.get(TeamSecret, (team.id, CLAUDE_API_KEY))
    assert row is not None
    assert row.has_value is True
    assert row.sensitive is True
    assert isinstance(row.value_encrypted, (bytes, memoryview))
    ct_bytes = bytes(row.value_encrypted)
    assert len(ct_bytes) > 0
    # Plaintext must not appear verbatim inside the Fernet token.
    assert plaintext.encode("utf-8") not in ct_bytes

    assert get_team_secret(db, team.id, CLAUDE_API_KEY) == plaintext


def test_set_team_secret_overwrites_existing_row(db: Session) -> None:
    """Second PUT for the same (team_id, key) replaces ciphertext + bumps updated_at."""
    team = _make_team(db)
    first = _valid_claude_key()
    second = "sk-ant-" + ("Z" * 40)

    set_team_secret(db, team.id, CLAUDE_API_KEY, first)
    row1 = db.get(TeamSecret, (team.id, CLAUDE_API_KEY))
    assert row1 is not None
    first_ct = bytes(row1.value_encrypted)
    first_updated_at = row1.updated_at

    set_team_secret(db, team.id, CLAUDE_API_KEY, second)
    db.expire_all()  # force re-read so updated_at + ciphertext reflect the upsert
    row2 = db.get(TeamSecret, (team.id, CLAUDE_API_KEY))
    assert row2 is not None
    assert bytes(row2.value_encrypted) != first_ct
    assert row2.updated_at is not None
    assert first_updated_at is not None
    # NOW() resolution is microsecond on Postgres, but two consecutive
    # NOW() calls in different transactions can land in the same tick on
    # fast hardware. We assert >= rather than > to keep the test stable.
    assert row2.updated_at >= first_updated_at
    assert get_team_secret(db, team.id, CLAUDE_API_KEY) == second


def test_set_team_secret_rejects_unregistered_key(db: Session) -> None:
    team = _make_team(db)
    with pytest.raises(UnregisteredTeamSecretKeyError):
        set_team_secret(db, team.id, "not_a_real_key", "anything")
    # No row was written.
    rows = db.exec(
        select(TeamSecret).where(TeamSecret.team_id == team.id)
    ).all()
    assert rows == []


def test_set_team_secret_rejects_invalid_value(db: Session) -> None:
    team = _make_team(db)
    with pytest.raises(InvalidTeamSecretValueError) as exc_info:
        set_team_secret(db, team.id, CLAUDE_API_KEY, "sk-bad-prefix-too-short")
    assert exc_info.value.key == CLAUDE_API_KEY
    # Validator failure must not write a row, even if encrypt could have
    # succeeded.
    assert db.get(TeamSecret, (team.id, CLAUDE_API_KEY)) is None


# ---------------------------------------------------------------------------
# get_team_secret
# ---------------------------------------------------------------------------


def test_get_team_secret_missing_row_raises_missing(db: Session) -> None:
    team = _make_team(db)
    with pytest.raises(MissingTeamSecretError) as exc_info:
        get_team_secret(db, team.id, CLAUDE_API_KEY)
    assert exc_info.value.team_id == team.id
    assert exc_info.value.key == CLAUDE_API_KEY


def test_get_team_secret_decrypt_failure_raises_team_decrypt_error(
    db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """Tampered ciphertext -> TeamSecretDecryptError + ERROR log line.

    This is the scenario S01's slice plan calls out as "decrypt failure
    surfaces as 503 with `team_secret_decrypt_failed`". We bypass the
    helper and write garbage directly into `value_encrypted` so Fernet's
    `InvalidToken` fires inside `decrypt_setting`. The helper must
    (a) raise the team-scoped exception, NOT the system-scoped one,
    (b) carry team_id+key on the exception, (c) log
    `team_secret_decrypt_failed` at ERROR.
    """
    team = _make_team(db)
    set_team_secret(db, team.id, CLAUDE_API_KEY, _valid_claude_key())

    # Stomp the row's ciphertext with bytes that cannot be a valid Fernet
    # token (Fernet tokens always start with a 0x80 version byte and
    # carry an HMAC the random bytes won't satisfy).
    db.execute(
        text(
            "UPDATE team_secrets SET value_encrypted = :ct "
            "WHERE team_id = :tid AND key = :k"
        ),
        {"ct": b"not-a-valid-fernet-token", "tid": team.id, "k": CLAUDE_API_KEY},
    )
    db.commit()

    with caplog.at_level("ERROR", logger="app.api.team_secrets"):
        with pytest.raises(TeamSecretDecryptError) as exc_info:
            get_team_secret(db, team.id, CLAUDE_API_KEY)

    assert exc_info.value.team_id == team.id
    assert exc_info.value.key == CLAUDE_API_KEY

    # The exception message MUST NOT carry the plaintext or its prefix.
    assert "sk-ant-" not in str(exc_info.value)

    # ERROR log present, names team_id + key, never the value or prefix.
    error_records = [r for r in caplog.records if r.levelname == "ERROR"]
    assert any(
        "team_secret_decrypt_failed" in r.getMessage() for r in error_records
    )
    for r in error_records:
        msg = r.getMessage()
        assert "sk-ant-" not in msg
        assert str(team.id) in msg
        assert CLAUDE_API_KEY in msg


# ---------------------------------------------------------------------------
# delete_team_secret
# ---------------------------------------------------------------------------


def test_delete_team_secret_returns_true_for_present_row(db: Session) -> None:
    team = _make_team(db)
    set_team_secret(db, team.id, OPENAI_API_KEY, _valid_openai_key())

    deleted = delete_team_secret(db, team.id, OPENAI_API_KEY)
    assert deleted is True
    assert db.get(TeamSecret, (team.id, OPENAI_API_KEY)) is None


def test_delete_team_secret_returns_false_for_absent_row(db: Session) -> None:
    """Idempotent — second DELETE returns False without raising."""
    team = _make_team(db)
    assert delete_team_secret(db, team.id, OPENAI_API_KEY) is False
    set_team_secret(db, team.id, OPENAI_API_KEY, _valid_openai_key())
    assert delete_team_secret(db, team.id, OPENAI_API_KEY) is True
    assert delete_team_secret(db, team.id, OPENAI_API_KEY) is False


# ---------------------------------------------------------------------------
# list_team_secret_status
# ---------------------------------------------------------------------------


def test_list_status_for_team_with_no_rows_returns_unset_for_each(
    db: Session,
) -> None:
    """Empty team_secrets for the team — one entry per registered key, all unset."""
    team = _make_team(db)
    statuses = list_team_secret_status(db, team.id)
    assert [s.key for s in statuses] == list(registered_keys())
    for s in statuses:
        assert s.has_value is False
        assert s.sensitive is True
        assert s.updated_at is None


def test_list_status_after_partial_set_reflects_only_set_row(
    db: Session,
) -> None:
    """Setting one key flips that entry's has_value/updated_at; the other stays unset."""
    team = _make_team(db)
    set_team_secret(db, team.id, CLAUDE_API_KEY, _valid_claude_key())

    statuses = {s.key: s for s in list_team_secret_status(db, team.id)}
    assert statuses[CLAUDE_API_KEY].has_value is True
    assert statuses[CLAUDE_API_KEY].updated_at is not None
    assert statuses[CLAUDE_API_KEY].sensitive is True
    assert statuses[OPENAI_API_KEY].has_value is False
    assert statuses[OPENAI_API_KEY].updated_at is None


def test_list_status_isolates_per_team(db: Session) -> None:
    """Team A's secrets must not appear in team B's status list.

    Guards against a stray join or missing where-clause in a future
    refactor — without an explicit cross-team test, a regression that
    leaks one team's `has_value=True` to another would silently pass
    every other test.
    """
    team_a = _make_team(db)
    team_b = _make_team(db)
    set_team_secret(db, team_a.id, CLAUDE_API_KEY, _valid_claude_key())

    a_statuses = {s.key: s for s in list_team_secret_status(db, team_a.id)}
    b_statuses = {s.key: s for s in list_team_secret_status(db, team_b.id)}
    assert a_statuses[CLAUDE_API_KEY].has_value is True
    assert b_statuses[CLAUDE_API_KEY].has_value is False
