"""Integration tests for the team_secrets API router (M005/S01/T03).

Covers the four routes exposed under `/api/v1/teams/{team_id}/secrets`:

    PUT    /{team_id}/secrets/{key}
    GET    /{team_id}/secrets
    GET    /{team_id}/secrets/{key}
    DELETE /{team_id}/secrets/{key}

Real FastAPI app + real Postgres via the session-scoped `db` fixture and
module-scoped `client` fixture in `tests/conftest.py`. No mocks. The
encryption key is pinned to a deterministic test-only Fernet key so PUT's
encrypt path is exercised end-to-end without writing real credentials to
the DB.

Slice-plan must-haves this module proves directly:

  * (3) team-admin gate on PUT (`team_admin_required`), unknown key 400,
        validator failure 400 with `hint`.
  * (4) GET-single shape `{key, has_value, sensitive, updated_at}` with no
        `value` field, 404 on missing row, 400 on unregistered key.
  * (5) GET-list returns one row per registered key.
  * (6) DELETE 204 then 404 idempotency-shape, team-admin gate.
  * (8) INFO logs `team_secret_set` / `team_secret_deleted` with team_id +
        key only — value never present.

The decrypt-failure path (must-have #7) belongs to the helper unit tests
(`test_team_secrets_helpers.py`) and the e2e test (T05) — this module
covers the HTTP surface, not the storage-layer round-trip.
"""

from __future__ import annotations

import logging
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete

from app import crud
from app.core.config import settings
from app.models import Team, TeamMember, TeamRole, TeamSecret
from tests.utils.utils import random_email, random_lower_string

API = settings.API_V1_STR
SIGNUP_URL = f"{API}/auth/signup"

# Deterministic test-only Fernet key — same shape as
# `test_team_secrets_helpers.py` so encrypt/decrypt is reproducible.
_TEST_FERNET_KEY = "q14YMz9s4jrbfD29GvcRfe_4krg82w6_mPWUu_y3LTo="

CLAUDE = "claude_api_key"
OPENAI = "openai_api_key"


def _valid_claude_key() -> str:
    # `sk-ant-` prefix + 40-char body — safely past the 40-char floor.
    return "sk-ant-" + ("A" * 40)


def _valid_openai_key() -> str:
    return "sk-" + ("B" * 40)


@pytest.fixture(autouse=True)
def _set_encryption_key(monkeypatch):
    """Pin SYSTEM_SETTINGS_ENCRYPTION_KEY for every test in this module.

    `_load_key` is `@functools.cache`d at module level — clear before AND
    after so the env swap takes effect now and leaves a clean slate for
    later modules in the same session.
    """
    monkeypatch.setenv("SYSTEM_SETTINGS_ENCRYPTION_KEY", _TEST_FERNET_KEY)
    from app.core import encryption as _enc

    _enc._load_key.cache_clear()
    yield
    _enc._load_key.cache_clear()


@pytest.fixture(autouse=True)
def _clean_team_secrets(db: Session):
    """Drop every team_secrets row before and after each test."""
    db.execute(delete(TeamSecret))
    db.commit()
    yield
    db.execute(delete(TeamSecret))
    db.commit()


def _signup(client: TestClient) -> tuple[uuid.UUID, httpx.Cookies]:
    """Create a fresh user + personal team; return (user_id, cookie jar).

    Mirrors the cookie-detach pattern from `test_teams.py` so the shared
    TestClient jar can be cleared between signups in multi-user tests.
    """
    email = random_email()
    password = random_lower_string()
    client.cookies.clear()
    r = client.post(SIGNUP_URL, json={"email": email, "password": password})
    assert r.status_code == 200, r.text

    jar = httpx.Cookies()
    for c in client.cookies.jar:
        jar.set(c.name, c.value)
    client.cookies.clear()
    return uuid.UUID(r.json()["id"]), jar


def _create_team_with_admin(db: Session, admin_id: uuid.UUID) -> Team:
    """Build a non-personal team owned by `admin_id`."""
    return crud.create_team_with_admin(
        session=db, name=f"sec-test-{uuid.uuid4().hex[:8]}", creator_id=admin_id
    )


def _add_member(
    db: Session, team_id: uuid.UUID, user_id: uuid.UUID, role: TeamRole
) -> None:
    db.add(TeamMember(user_id=user_id, team_id=team_id, role=role))
    db.commit()


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------


def test_put_without_cookie_returns_401(client: TestClient) -> None:
    """No session cookie → 401 before any team-membership check runs."""
    client.cookies.clear()
    team_id = uuid.uuid4()
    r = client.put(
        f"{API}/teams/{team_id}/secrets/{CLAUDE}",
        json={"value": _valid_claude_key()},
    )
    assert r.status_code == 401


def test_get_without_cookie_returns_401(client: TestClient) -> None:
    client.cookies.clear()
    team_id = uuid.uuid4()
    r = client.get(f"{API}/teams/{team_id}/secrets")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# PUT — happy path + validation + admin gate
# ---------------------------------------------------------------------------


def test_put_admin_can_set_claude_key(
    client: TestClient, db: Session
) -> None:
    """Admin pastes a valid Claude key → 200, has_value=True, no value field."""
    user_id, cookies = _signup(client)
    team = _create_team_with_admin(db, user_id)

    r = client.put(
        f"{API}/teams/{team.id}/secrets/{CLAUDE}",
        json={"value": _valid_claude_key()},
        cookies=cookies,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["key"] == CLAUDE
    assert body["has_value"] is True
    assert body["sensitive"] is True
    assert body["updated_at"] is not None
    assert "value" not in body  # never round-trip the plaintext


def test_put_admin_can_set_openai_key(
    client: TestClient, db: Session
) -> None:
    user_id, cookies = _signup(client)
    team = _create_team_with_admin(db, user_id)

    r = client.put(
        f"{API}/teams/{team.id}/secrets/{OPENAI}",
        json={"value": _valid_openai_key()},
        cookies=cookies,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["key"] == OPENAI
    assert body["has_value"] is True
    assert "value" not in body


def test_put_persists_encrypted_row(
    client: TestClient, db: Session
) -> None:
    """The DB row carries Fernet ciphertext, not the plaintext.

    Reads the row back directly; `value_encrypted` MUST NOT contain the
    `sk-ant-` prefix (would mean we wrote plaintext) and MUST be non-empty.
    """
    user_id, cookies = _signup(client)
    team = _create_team_with_admin(db, user_id)
    plaintext = _valid_claude_key()

    r = client.put(
        f"{API}/teams/{team.id}/secrets/{CLAUDE}",
        json={"value": plaintext},
        cookies=cookies,
    )
    assert r.status_code == 200, r.text

    row = db.get(TeamSecret, (team.id, CLAUDE))
    assert row is not None
    assert row.value_encrypted  # bytes, non-empty
    assert plaintext.encode() not in row.value_encrypted
    assert b"sk-ant-" not in row.value_encrypted


def test_put_non_admin_returns_403_team_admin_required(
    client: TestClient, db: Session
) -> None:
    """A team member without admin role cannot PUT — 403 `team_admin_required`."""
    admin_id, _ = _signup(client)
    member_id, member_cookies = _signup(client)
    team = _create_team_with_admin(db, admin_id)
    _add_member(db, team.id, member_id, TeamRole.member)

    r = client.put(
        f"{API}/teams/{team.id}/secrets/{CLAUDE}",
        json={"value": _valid_claude_key()},
        cookies=member_cookies,
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["detail"] == "team_admin_required"


def test_put_non_member_returns_403_not_team_member(
    client: TestClient, db: Session
) -> None:
    """A user with no membership row gets 403 `not_team_member` — admin check sees membership absent first."""
    admin_id, _ = _signup(client)
    outsider_id, outsider_cookies = _signup(client)
    team = _create_team_with_admin(db, admin_id)

    r = client.put(
        f"{API}/teams/{team.id}/secrets/{CLAUDE}",
        json={"value": _valid_claude_key()},
        cookies=outsider_cookies,
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["detail"] == "not_team_member"


def test_put_unknown_team_returns_404(
    client: TestClient, db: Session
) -> None:
    user_id, cookies = _signup(client)

    r = client.put(
        f"{API}/teams/{uuid.uuid4()}/secrets/{CLAUDE}",
        json={"value": _valid_claude_key()},
        cookies=cookies,
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["detail"] == "team_not_found"


def test_put_unregistered_key_returns_400(
    client: TestClient, db: Session
) -> None:
    """Unknown key → 400 `unregistered_key` with the offending key in the body."""
    user_id, cookies = _signup(client)
    team = _create_team_with_admin(db, user_id)

    r = client.put(
        f"{API}/teams/{team.id}/secrets/not_real_key",
        json={"value": _valid_claude_key()},
        cookies=cookies,
    )
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["detail"]["detail"] == "unregistered_key"
    assert body["detail"]["key"] == "not_real_key"


def test_put_bad_prefix_returns_400_invalid_value_shape(
    client: TestClient, db: Session
) -> None:
    """Validator rejects the plaintext → 400 `invalid_value_shape` with hint."""
    user_id, cookies = _signup(client)
    team = _create_team_with_admin(db, user_id)

    r = client.put(
        f"{API}/teams/{team.id}/secrets/{CLAUDE}",
        json={"value": "sk-wrong-" + ("A" * 40)},
        cookies=cookies,
    )
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["detail"]["detail"] == "invalid_value_shape"
    assert body["detail"]["key"] == CLAUDE
    assert body["detail"]["hint"] == "bad_prefix"


def test_put_too_short_returns_400(
    client: TestClient, db: Session
) -> None:
    user_id, cookies = _signup(client)
    team = _create_team_with_admin(db, user_id)

    r = client.put(
        f"{API}/teams/{team.id}/secrets/{OPENAI}",
        json={"value": "sk-short"},
        cookies=cookies,
    )
    assert r.status_code == 400
    assert r.json()["detail"]["hint"] == "too_short"


def test_put_replace_bumps_updated_at(
    client: TestClient, db: Session
) -> None:
    """Second PUT for the same key produces a strictly later `updated_at`."""
    user_id, cookies = _signup(client)
    team = _create_team_with_admin(db, user_id)

    first = client.put(
        f"{API}/teams/{team.id}/secrets/{CLAUDE}",
        json={"value": _valid_claude_key()},
        cookies=cookies,
    )
    assert first.status_code == 200
    first_at = first.json()["updated_at"]

    second = client.put(
        f"{API}/teams/{team.id}/secrets/{CLAUDE}",
        json={"value": _valid_claude_key().replace("A", "C")},
        cookies=cookies,
    )
    assert second.status_code == 200
    second_at = second.json()["updated_at"]
    assert second_at >= first_at


# ---------------------------------------------------------------------------
# GET single
# ---------------------------------------------------------------------------


def test_get_single_after_put_returns_status_no_value(
    client: TestClient, db: Session
) -> None:
    user_id, cookies = _signup(client)
    team = _create_team_with_admin(db, user_id)

    client.put(
        f"{API}/teams/{team.id}/secrets/{CLAUDE}",
        json={"value": _valid_claude_key()},
        cookies=cookies,
    )

    r = client.get(
        f"{API}/teams/{team.id}/secrets/{CLAUDE}", cookies=cookies
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["key"] == CLAUDE
    assert body["has_value"] is True
    assert body["sensitive"] is True
    assert "value" not in body
    assert body["updated_at"] is not None


def test_get_single_missing_row_returns_404_team_secret_not_set(
    client: TestClient, db: Session
) -> None:
    user_id, cookies = _signup(client)
    team = _create_team_with_admin(db, user_id)

    r = client.get(
        f"{API}/teams/{team.id}/secrets/{CLAUDE}", cookies=cookies
    )
    assert r.status_code == 404
    body = r.json()
    assert body["detail"]["detail"] == "team_secret_not_set"
    assert body["detail"]["key"] == CLAUDE


def test_get_single_unregistered_key_returns_400(
    client: TestClient, db: Session
) -> None:
    user_id, cookies = _signup(client)
    team = _create_team_with_admin(db, user_id)

    r = client.get(
        f"{API}/teams/{team.id}/secrets/not_real", cookies=cookies
    )
    assert r.status_code == 400
    assert r.json()["detail"]["detail"] == "unregistered_key"


def test_get_single_member_can_read(
    client: TestClient, db: Session
) -> None:
    """Non-admin member can read presence/absence — read path is member-gated."""
    admin_id, admin_cookies = _signup(client)
    member_id, member_cookies = _signup(client)
    team = _create_team_with_admin(db, admin_id)
    _add_member(db, team.id, member_id, TeamRole.member)

    client.put(
        f"{API}/teams/{team.id}/secrets/{CLAUDE}",
        json={"value": _valid_claude_key()},
        cookies=admin_cookies,
    )

    r = client.get(
        f"{API}/teams/{team.id}/secrets/{CLAUDE}", cookies=member_cookies
    )
    assert r.status_code == 200
    assert r.json()["has_value"] is True


def test_get_single_non_member_returns_403(
    client: TestClient, db: Session
) -> None:
    admin_id, _ = _signup(client)
    _, outsider_cookies = _signup(client)
    team = _create_team_with_admin(db, admin_id)

    r = client.get(
        f"{API}/teams/{team.id}/secrets/{CLAUDE}", cookies=outsider_cookies
    )
    assert r.status_code == 403
    assert r.json()["detail"]["detail"] == "not_team_member"


# ---------------------------------------------------------------------------
# GET list
# ---------------------------------------------------------------------------


def test_get_list_returns_row_per_registered_key_when_empty(
    client: TestClient, db: Session
) -> None:
    """Empty team_secrets table still returns one entry per registered key."""
    user_id, cookies = _signup(client)
    team = _create_team_with_admin(db, user_id)

    r = client.get(f"{API}/teams/{team.id}/secrets", cookies=cookies)
    assert r.status_code == 200
    body = r.json()
    keys = [entry["key"] for entry in body]
    assert keys == [CLAUDE, OPENAI]  # registry declaration order
    for entry in body:
        assert entry["has_value"] is False
        assert entry["sensitive"] is True
        assert entry["updated_at"] is None
        assert "value" not in entry


def test_get_list_reflects_set_rows(
    client: TestClient, db: Session
) -> None:
    user_id, cookies = _signup(client)
    team = _create_team_with_admin(db, user_id)

    client.put(
        f"{API}/teams/{team.id}/secrets/{CLAUDE}",
        json={"value": _valid_claude_key()},
        cookies=cookies,
    )

    r = client.get(f"{API}/teams/{team.id}/secrets", cookies=cookies)
    body = r.json()
    by_key = {e["key"]: e for e in body}
    assert by_key[CLAUDE]["has_value"] is True
    assert by_key[CLAUDE]["updated_at"] is not None
    assert by_key[OPENAI]["has_value"] is False
    assert by_key[OPENAI]["updated_at"] is None


def test_get_list_isolates_per_team(
    client: TestClient, db: Session
) -> None:
    """Setting a key on team-A does not affect team-B's status list."""
    user_id, cookies = _signup(client)
    team_a = _create_team_with_admin(db, user_id)
    team_b = _create_team_with_admin(db, user_id)

    client.put(
        f"{API}/teams/{team_a.id}/secrets/{CLAUDE}",
        json={"value": _valid_claude_key()},
        cookies=cookies,
    )

    r_b = client.get(f"{API}/teams/{team_b.id}/secrets", cookies=cookies)
    by_key = {e["key"]: e for e in r_b.json()}
    assert by_key[CLAUDE]["has_value"] is False


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


def test_delete_removes_row_returns_204(
    client: TestClient, db: Session
) -> None:
    user_id, cookies = _signup(client)
    team = _create_team_with_admin(db, user_id)
    client.put(
        f"{API}/teams/{team.id}/secrets/{CLAUDE}",
        json={"value": _valid_claude_key()},
        cookies=cookies,
    )

    r = client.delete(
        f"{API}/teams/{team.id}/secrets/{CLAUDE}", cookies=cookies
    )
    assert r.status_code == 204
    assert r.content == b""

    # Row is gone — GET-single now 404s.
    g = client.get(
        f"{API}/teams/{team.id}/secrets/{CLAUDE}", cookies=cookies
    )
    assert g.status_code == 404


def test_delete_idempotent_second_call_404(
    client: TestClient, db: Session
) -> None:
    """Second DELETE returns 404 `team_secret_not_set` — no row, no surprise."""
    user_id, cookies = _signup(client)
    team = _create_team_with_admin(db, user_id)
    client.put(
        f"{API}/teams/{team.id}/secrets/{CLAUDE}",
        json={"value": _valid_claude_key()},
        cookies=cookies,
    )

    first = client.delete(
        f"{API}/teams/{team.id}/secrets/{CLAUDE}", cookies=cookies
    )
    assert first.status_code == 204

    second = client.delete(
        f"{API}/teams/{team.id}/secrets/{CLAUDE}", cookies=cookies
    )
    assert second.status_code == 404
    assert second.json()["detail"]["detail"] == "team_secret_not_set"


def test_delete_non_admin_returns_403(
    client: TestClient, db: Session
) -> None:
    admin_id, admin_cookies = _signup(client)
    member_id, member_cookies = _signup(client)
    team = _create_team_with_admin(db, admin_id)
    _add_member(db, team.id, member_id, TeamRole.member)
    client.put(
        f"{API}/teams/{team.id}/secrets/{CLAUDE}",
        json={"value": _valid_claude_key()},
        cookies=admin_cookies,
    )

    r = client.delete(
        f"{API}/teams/{team.id}/secrets/{CLAUDE}", cookies=member_cookies
    )
    assert r.status_code == 403
    assert r.json()["detail"]["detail"] == "team_admin_required"


def test_delete_unregistered_key_returns_400(
    client: TestClient, db: Session
) -> None:
    user_id, cookies = _signup(client)
    team = _create_team_with_admin(db, user_id)

    r = client.delete(
        f"{API}/teams/{team.id}/secrets/not_real", cookies=cookies
    )
    assert r.status_code == 400
    assert r.json()["detail"]["detail"] == "unregistered_key"


# ---------------------------------------------------------------------------
# Observability — INFO logs on PUT/DELETE carry team_id + key only
# ---------------------------------------------------------------------------


def test_put_emits_team_secret_set_log_without_value(
    client: TestClient, db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """`team_secret_set` log line carries team_id + key, never the plaintext or any prefix."""
    user_id, cookies = _signup(client)
    team = _create_team_with_admin(db, user_id)

    plaintext = _valid_claude_key()
    with caplog.at_level(logging.INFO, logger="app.api.routes.team_secrets"):
        r = client.put(
            f"{API}/teams/{team.id}/secrets/{CLAUDE}",
            json={"value": plaintext},
            cookies=cookies,
        )
        assert r.status_code == 200, r.text

    matches = [rec for rec in caplog.records if "team_secret_set" in rec.getMessage()]
    assert matches, f"no team_secret_set log found, captured={[r.getMessage() for r in caplog.records]}"
    rendered = matches[0].getMessage()
    assert str(team.id) in rendered
    assert CLAUDE in rendered
    # Hard redaction guards: the value, its prefix, and any partial body MUST NOT appear.
    assert plaintext not in rendered
    assert "sk-ant-" not in rendered
    assert "sk-" not in rendered


def test_delete_emits_team_secret_deleted_log(
    client: TestClient, db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    user_id, cookies = _signup(client)
    team = _create_team_with_admin(db, user_id)
    client.put(
        f"{API}/teams/{team.id}/secrets/{CLAUDE}",
        json={"value": _valid_claude_key()},
        cookies=cookies,
    )

    with caplog.at_level(logging.INFO, logger="app.api.routes.team_secrets"):
        r = client.delete(
            f"{API}/teams/{team.id}/secrets/{CLAUDE}", cookies=cookies
        )
        assert r.status_code == 204

    matches = [
        rec for rec in caplog.records if "team_secret_deleted" in rec.getMessage()
    ]
    assert matches
    rendered = matches[0].getMessage()
    assert str(team.id) in rendered
    assert CLAUDE in rendered
    assert "sk-" not in rendered


def test_failed_put_does_not_emit_team_secret_set_log(
    client: TestClient, db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """A 400 (validator failure) MUST NOT emit the success log line.

    Guards against a future refactor that moves the `logger.info` above
    the validator boundary and ends up logging "set" on a failed write.
    """
    user_id, cookies = _signup(client)
    team = _create_team_with_admin(db, user_id)

    with caplog.at_level(logging.INFO, logger="app.api.routes.team_secrets"):
        r = client.put(
            f"{API}/teams/{team.id}/secrets/{CLAUDE}",
            json={"value": "sk-wrong-" + ("A" * 40)},
            cookies=cookies,
        )
        assert r.status_code == 400

    assert not any(
        "team_secret_set" in rec.getMessage() for rec in caplog.records
    )
