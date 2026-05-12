"""Unit + integration tests for T03: _fetch_github_user_id and token persistence.

Tests cover:
  - _fetch_github_user_id happy path → returns int github_user_id
  - _fetch_github_user_id non-200 → HTTPException 502 github_user_lookup_failed
  - _fetch_github_user_id malformed JSON → HTTPException 502
  - _fetch_github_user_id missing/wrong-type id field → HTTPException 502
  - _fetch_github_user_id transport error → HTTPException 502
  - _process_install_callback with oauth_tuple persists github_user_oauth_tokens row
  - _process_install_callback without oauth_tuple (POST path) skips token row
  - GET callback (OAuth code-only flow) persists token row end-to-end
  - Both github_app_installations and github_user_oauth_tokens committed atomically

All tests use the same monkeypatched httpx.AsyncClient pattern as the rest of
the github route test suite (MEM184).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, delete

from app.api.routes.admin import (
    GITHUB_APP_CLIENT_ID_KEY,
    GITHUB_APP_CLIENT_SECRET_KEY,
    GITHUB_APP_SLUG_KEY,
)
from app.core.config import settings
from app.core.encryption import encrypt_setting
from app.core.github_user_tokens import decrypt_user_token
from app.models import GitHubAppInstallation, SystemSetting
from tests.utils.utils import random_email, random_lower_string

API = settings.API_V1_STR
SIGNUP_URL = f"{API}/auth/signup"
TEAMS_URL = f"{API}/teams/"

# ---------------------------------------------------------------------------
# Encryption key injection (required for encrypt_user_token in the route)
# ---------------------------------------------------------------------------

_TEST_FERNET_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _patch_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject a valid Fernet key and clear the _load_key LRU cache."""
    import app.core.encryption as enc

    monkeypatch.setenv("SYSTEM_SETTINGS_ENCRYPTION_KEY", _TEST_FERNET_KEY)
    enc._load_key.cache_clear()
    yield
    enc._load_key.cache_clear()


# ---------------------------------------------------------------------------
# DB isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_github_state(db: Session) -> None:
    """Wipe installs, tokens, and github system_settings before/after each test."""
    _SETTINGS_KEYS = [
        GITHUB_APP_SLUG_KEY,
        GITHUB_APP_CLIENT_ID_KEY,
        GITHUB_APP_CLIENT_SECRET_KEY,
    ]
    db.execute(text("DELETE FROM github_user_oauth_tokens"))
    db.execute(delete(GitHubAppInstallation))
    db.execute(
        delete(SystemSetting).where(SystemSetting.key.in_(_SETTINGS_KEYS))
    )
    db.commit()
    yield
    db.execute(text("DELETE FROM github_user_oauth_tokens"))
    db.execute(delete(GitHubAppInstallation))
    db.execute(
        delete(SystemSetting).where(SystemSetting.key.in_(_SETTINGS_KEYS))
    )
    db.commit()


# ---------------------------------------------------------------------------
# Shared HTTP fakes
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(
        self,
        status_code: int,
        json_body: object | None = None,
        *,
        raises_on_json: bool = False,
    ) -> None:
        self.status_code = status_code
        self._json = json_body
        self._raises = raises_on_json
        self.request = httpx.Request("GET", "http://fake")

    def json(self) -> object:
        if self._raises:
            raise ValueError("not json")
        return self._json


class _FakeAsyncClient:
    """Route-map stub for httpx.AsyncClient (MEM184 pattern)."""

    last_calls: list[tuple[str, str]] = []

    def __init__(self, route_map: dict[tuple[str, str], object]) -> None:
        self._routes = route_map

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def _resolve(self, method: str, url: str) -> object:
        type(self).last_calls.append((method, url))
        for (m, suffix), handler in self._routes.items():
            if m == method and url.endswith(suffix):
                return handler
        raise AssertionError(
            f"FakeAsyncClient: no route for {method} {url}; "
            f"registered={list(self._routes.keys())}"
        )

    async def get(self, url: str, **_: object) -> _FakeResponse:
        handler = self._resolve("GET", url)
        if isinstance(handler, Exception):
            raise handler
        assert isinstance(handler, _FakeResponse)
        return handler

    async def post(self, url: str, **_: object) -> _FakeResponse:
        handler = self._resolve("POST", url)
        if isinstance(handler, Exception):
            raise handler
        assert isinstance(handler, _FakeResponse)
        return handler


def _install_fake_orch(
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[tuple[str, str], object],
) -> type[_FakeAsyncClient]:
    import app.api.routes.github as github_mod

    _FakeAsyncClient.last_calls = []

    def _factory(*_args: object, **_kwargs: object) -> _FakeAsyncClient:
        return _FakeAsyncClient(routes)

    monkeypatch.setattr(github_mod.httpx, "AsyncClient", _factory)
    return _FakeAsyncClient


# ---------------------------------------------------------------------------
# DB/client helpers
# ---------------------------------------------------------------------------


def _signup(client: TestClient) -> tuple[str, httpx.Cookies]:
    email = random_email()
    password = random_lower_string()
    client.cookies.clear()
    r = client.post(SIGNUP_URL, json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    jar = httpx.Cookies()
    for cookie in client.cookies.jar:
        jar.set(cookie.name, cookie.value)
    client.cookies.clear()
    return r.json()["id"], jar


def _create_team(client: TestClient, cookies: httpx.Cookies, name: str) -> str:
    r = client.post(TEAMS_URL, json={"name": name}, cookies=cookies)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _seed_app_slug(db: Session, value: str = "test-app-slug") -> None:
    import json

    db.execute(
        text(
            """
            INSERT INTO system_settings
                (key, value, value_encrypted, sensitive, has_value, updated_at)
            VALUES
                (:key, CAST(:value AS JSONB), NULL, FALSE, TRUE, NOW())
            ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value, has_value = TRUE, updated_at = NOW()
            """
        ),
        {"key": GITHUB_APP_SLUG_KEY, "value": json.dumps(value)},
    )
    db.commit()


def _seed_client_id(db: Session, value: str = "Iv1.test-client-id") -> None:
    import json

    db.execute(
        text(
            """
            INSERT INTO system_settings
                (key, value, value_encrypted, sensitive, has_value, updated_at)
            VALUES
                (:key, CAST(:value AS JSONB), NULL, FALSE, TRUE, NOW())
            ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value, has_value = TRUE, updated_at = NOW()
            """
        ),
        {"key": GITHUB_APP_CLIENT_ID_KEY, "value": json.dumps(value)},
    )
    db.commit()


def _seed_client_secret(db: Session, plaintext: str = "test-client-secret") -> None:
    ciphertext = encrypt_setting(plaintext)
    db.execute(
        text(
            """
            INSERT INTO system_settings
                (key, value, value_encrypted, sensitive, has_value, updated_at)
            VALUES
                (:key, NULL, :ct, TRUE, TRUE, NOW())
            ON CONFLICT (key) DO UPDATE
            SET value = NULL,
                value_encrypted = EXCLUDED.value_encrypted,
                sensitive = TRUE,
                has_value = TRUE,
                updated_at = NOW()
            """
        ),
        {"key": GITHUB_APP_CLIENT_SECRET_KEY, "ct": ciphertext},
    )
    db.commit()


# ---------------------------------------------------------------------------
# Unit tests for _fetch_github_user_id
# ---------------------------------------------------------------------------


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_fetch_github_user_id_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /user returns {id: 12345} → returns 12345 as int."""
    import app.api.routes.github as github_mod
    from app.api.routes.github import _fetch_github_user_id

    routes = {
        ("GET", "api.github.com/user"): _FakeResponse(200, {"id": 12345, "login": "octocat"}),
    }
    _install_fake_orch(monkeypatch, routes)

    result = _run(_fetch_github_user_id("ghu_testtoken"))
    assert result == 12345


def test_fetch_github_user_id_non_200_raises_502(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /user returns 401 → HTTPException 502 github_user_lookup_failed."""
    from fastapi import HTTPException

    from app.api.routes.github import _fetch_github_user_id

    routes = {
        ("GET", "api.github.com/user"): _FakeResponse(401, {"message": "Bad credentials"}),
    }
    _install_fake_orch(monkeypatch, routes)

    with pytest.raises(HTTPException) as exc_info:
        _run(_fetch_github_user_id("ghu_badtoken"))

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "github_user_lookup_failed"


def test_fetch_github_user_id_malformed_json_raises_502(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /user returns non-JSON body → HTTPException 502 github_user_lookup_failed."""
    from fastapi import HTTPException

    from app.api.routes.github import _fetch_github_user_id

    routes = {
        ("GET", "api.github.com/user"): _FakeResponse(200, None, raises_on_json=True),
    }
    _install_fake_orch(monkeypatch, routes)

    with pytest.raises(HTTPException) as exc_info:
        _run(_fetch_github_user_id("ghu_tok"))

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "github_user_lookup_failed"


def test_fetch_github_user_id_missing_id_field_raises_502(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /user returns JSON without 'id' key → HTTPException 502."""
    from fastapi import HTTPException

    from app.api.routes.github import _fetch_github_user_id

    routes = {
        ("GET", "api.github.com/user"): _FakeResponse(200, {"login": "octocat"}),
    }
    _install_fake_orch(monkeypatch, routes)

    with pytest.raises(HTTPException) as exc_info:
        _run(_fetch_github_user_id("ghu_tok"))

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "github_user_lookup_failed"


def test_fetch_github_user_id_string_id_raises_502(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /user returns {id: 'notanint'} (wrong type) → HTTPException 502."""
    from fastapi import HTTPException

    from app.api.routes.github import _fetch_github_user_id

    routes = {
        ("GET", "api.github.com/user"): _FakeResponse(200, {"id": "notanint", "login": "x"}),
    }
    _install_fake_orch(monkeypatch, routes)

    with pytest.raises(HTTPException) as exc_info:
        _run(_fetch_github_user_id("ghu_tok"))

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "github_user_lookup_failed"


def test_fetch_github_user_id_transport_error_raises_502(monkeypatch: pytest.MonkeyPatch) -> None:
    """Network error during GET /user → HTTPException 502 github_user_lookup_failed."""
    from fastapi import HTTPException

    from app.api.routes.github import _fetch_github_user_id

    routes: dict[tuple[str, str], object] = {
        ("GET", "api.github.com/user"): httpx.ConnectTimeout(
            "connection timed out",
            request=httpx.Request("GET", "https://api.github.com/user"),
        ),
    }
    _install_fake_orch(monkeypatch, routes)

    with pytest.raises(HTTPException) as exc_info:
        _run(_fetch_github_user_id("ghu_tok"))

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "github_user_lookup_failed"


# ---------------------------------------------------------------------------
# Integration: token persistence in _process_install_callback
# ---------------------------------------------------------------------------


def _build_full_oauth_routes(
    installation_id: int,
    github_user_id: int = 99001,
    access_token: str = "ghu_fakeaccess",
    refresh_token: str = "ghr_fakerefresh",
    orch_login: str = "persist-org",
    orch_type: str = "Organization",
) -> dict[tuple[str, str], object]:
    """Build a complete route map for the OAuth code-exchange + user-lookup + orch flow."""
    return {
        ("POST", "github.com/login/oauth/access_token"): _FakeResponse(
            200,
            {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_in": 28800,
                "refresh_token_expires_in": 15897600,
                "scope": "repo",
                "token_type": "bearer",
            },
        ),
        ("GET", "api.github.com/user/installations"): _FakeResponse(
            200,
            {
                "total_count": 1,
                "installations": [{"id": installation_id, "app_slug": "test"}],
            },
        ),
        ("GET", "api.github.com/user"): _FakeResponse(
            200, {"id": github_user_id, "login": "testuser"}
        ),
        ("GET", f"/v1/installations/{installation_id}/lookup"): _FakeResponse(
            200,
            {"account_login": orch_login, "account_type": orch_type},
        ),
    }


def test_get_callback_oauth_flow_persists_token_row(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OAuth code-only GET callback → github_user_oauth_tokens row created."""
    user_id, cookies = _signup(client)
    team_id = _create_team(client, cookies, "TokenPersist")
    _seed_app_slug(db)
    _seed_client_id(db)
    _seed_client_secret(db)

    state = client.get(
        f"{API}/teams/{team_id}/github/install-url", cookies=cookies
    ).json()["state"]

    inst_id = 700001
    gh_user_id = 42001
    access_tok = "ghu_access_700001"
    refresh_tok = "ghr_refresh_700001"
    routes = _build_full_oauth_routes(
        inst_id,
        github_user_id=gh_user_id,
        access_token=access_tok,
        refresh_token=refresh_tok,
    )
    _install_fake_orch(monkeypatch, routes)

    client.cookies.clear()
    r = client.get(
        f"{API}/github/install-callback",
        params={"code": "ghu_testcode", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 302, r.text
    assert "github_install_error" not in r.headers["location"]

    # Verify github_user_oauth_tokens row.
    db.expire_all()
    token_row = db.execute(
        text(
            "SELECT user_id, installation_id, github_user_id, "
            "access_token_encrypted, refresh_token_encrypted, "
            "access_token_expires_at, refresh_token_expires_at, scope "
            "FROM github_user_oauth_tokens WHERE user_id = :uid"
        ),
        {"uid": user_id},
    ).one()

    assert str(token_row.user_id) == user_id
    assert token_row.installation_id == inst_id
    assert token_row.github_user_id == gh_user_id
    assert token_row.scope == "repo"

    # Verify expiry timestamps are in the future.
    now = datetime.now(timezone.utc)
    # access_token_expires_at ~ 8h from now
    assert token_row.access_token_expires_at > now
    # refresh_token_expires_at ~ 6 months from now
    assert token_row.refresh_token_expires_at > token_row.access_token_expires_at

    # Verify token ciphertext decrypts correctly.
    decrypted_access = decrypt_user_token(bytes(token_row.access_token_encrypted))
    decrypted_refresh = decrypt_user_token(bytes(token_row.refresh_token_encrypted))
    assert decrypted_access == access_tok
    assert decrypted_refresh == refresh_tok

    # Also verify the installation row was persisted.
    install_row = db.execute(
        text(
            "SELECT installation_id, team_id FROM github_app_installations"
            " WHERE installation_id = :id"
        ),
        {"id": inst_id},
    ).one()
    assert install_row.installation_id == inst_id
    assert str(install_row.team_id) == team_id


def test_post_callback_no_oauth_tuple_skips_token_row(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST callback (no code) → installation row created but NO token row."""
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "NoTokenPost")
    _seed_app_slug(db)

    state = client.get(
        f"{API}/teams/{team_id}/github/install-url", cookies=cookies
    ).json()["state"]

    inst_id = 700002
    routes: dict[tuple[str, str], object] = {
        ("GET", f"/v1/installations/{inst_id}/lookup"): _FakeResponse(
            200,
            {"account_login": "post-org", "account_type": "Organization"},
        ),
    }
    _install_fake_orch(monkeypatch, routes)

    client.cookies.clear()
    r = client.post(
        f"{API}/github/install-callback",
        json={"installation_id": inst_id, "setup_action": "install", "state": state},
    )
    assert r.status_code == 200, r.text

    db.expire_all()
    count = db.execute(
        text("SELECT COUNT(*) AS n FROM github_user_oauth_tokens")
    ).one()
    assert count.n == 0, "POST path must not create a token row"


def test_get_callback_setup_url_flow_no_code_skips_token_row(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET callback with installation_id (no code) → no token row persisted."""
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "SetupUrlNoToken")
    _seed_app_slug(db)

    state = client.get(
        f"{API}/teams/{team_id}/github/install-url", cookies=cookies
    ).json()["state"]

    inst_id = 700003
    routes: dict[tuple[str, str], object] = {
        ("GET", f"/v1/installations/{inst_id}/lookup"): _FakeResponse(
            200,
            {"account_login": "setup-org", "account_type": "Organization"},
        ),
    }
    _install_fake_orch(monkeypatch, routes)

    client.cookies.clear()
    r = client.get(
        f"{API}/github/install-callback",
        params={"installation_id": inst_id, "setup_action": "install", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 302, r.text
    assert "github_install_error" not in r.headers["location"]

    db.expire_all()
    count = db.execute(
        text("SELECT COUNT(*) AS n FROM github_user_oauth_tokens")
    ).one()
    assert count.n == 0, "Setup URL flow (no code) must not create a token row"


def test_oauth_callback_upserts_token_row_on_reinstall(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second OAuth code callback for the same user overwrites the token row."""
    user_id, cookies = _signup(client)
    team_id = _create_team(client, cookies, "TokenUpsert")
    _seed_app_slug(db)
    _seed_client_id(db)
    _seed_client_secret(db)

    inst_id = 700004
    gh_user_id = 42004

    # First install
    state1 = client.get(
        f"{API}/teams/{team_id}/github/install-url", cookies=cookies
    ).json()["state"]
    routes1 = _build_full_oauth_routes(
        inst_id,
        github_user_id=gh_user_id,
        access_token="ghu_first",
        refresh_token="ghr_first",
    )
    _install_fake_orch(monkeypatch, routes1)
    client.cookies.clear()
    r1 = client.get(
        f"{API}/github/install-callback",
        params={"code": "code1", "state": state1},
        follow_redirects=False,
    )
    assert r1.status_code == 302, r1.text

    # Second install — different tokens, same user
    state2 = client.get(
        f"{API}/teams/{team_id}/github/install-url", cookies=cookies
    ).json()["state"]
    routes2 = _build_full_oauth_routes(
        inst_id,
        github_user_id=gh_user_id,
        access_token="ghu_second",
        refresh_token="ghr_second",
    )
    _install_fake_orch(monkeypatch, routes2)
    client.cookies.clear()
    r2 = client.get(
        f"{API}/github/install-callback",
        params={"code": "code2", "state": state2},
        follow_redirects=False,
    )
    assert r2.status_code == 302, r2.text

    # Only one token row must exist and it must contain the *second* tokens.
    db.expire_all()
    count = db.execute(
        text("SELECT COUNT(*) AS n FROM github_user_oauth_tokens WHERE user_id = :uid"),
        {"uid": user_id},
    ).one()
    assert count.n == 1, "Exactly one token row per user"

    token_row = db.execute(
        text(
            "SELECT access_token_encrypted, refresh_token_encrypted "
            "FROM github_user_oauth_tokens WHERE user_id = :uid"
        ),
        {"uid": user_id},
    ).one()
    assert decrypt_user_token(bytes(token_row.access_token_encrypted)) == "ghu_second"
    assert decrypt_user_token(bytes(token_row.refresh_token_encrypted)) == "ghr_second"


def test_oauth_callback_user_lookup_failure_redirects_with_error(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_fetch_github_user_id returning 502 → GET redirects with github_user_lookup_failed."""
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "UserLookupFail")
    _seed_app_slug(db)
    _seed_client_id(db)
    _seed_client_secret(db)

    state = client.get(
        f"{API}/teams/{team_id}/github/install-url", cookies=cookies
    ).json()["state"]

    inst_id = 700005
    routes: dict[tuple[str, str], object] = {
        ("POST", "github.com/login/oauth/access_token"): _FakeResponse(
            200,
            {
                "access_token": "ghu_tok",
                "refresh_token": "ghr_tok",
                "expires_in": 28800,
                "refresh_token_expires_in": 15897600,
                "scope": "repo",
                "token_type": "bearer",
            },
        ),
        ("GET", "api.github.com/user/installations"): _FakeResponse(
            200,
            {
                "total_count": 1,
                "installations": [{"id": inst_id, "app_slug": "test"}],
            },
        ),
        # /user returns 503 → _fetch_github_user_id raises 502
        ("GET", "api.github.com/user"): _FakeResponse(503, None),
        ("GET", f"/v1/installations/{inst_id}/lookup"): _FakeResponse(
            200,
            {"account_login": "fail-org", "account_type": "Organization"},
        ),
    }
    _install_fake_orch(monkeypatch, routes)

    client.cookies.clear()
    r = client.get(
        f"{API}/github/install-callback",
        params={"code": "ghu_code", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 302, r.text
    assert "github_user_lookup_failed" in r.headers["location"]

    # No token row should have been created (transaction rolled back).
    db.expire_all()
    count = db.execute(
        text("SELECT COUNT(*) AS n FROM github_user_oauth_tokens")
    ).one()
    assert count.n == 0
