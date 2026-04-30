"""Unit tests for the GitHub install router (M004 / S02 / T02).

The orchestrator is faked via the same monkeypatch-on-`httpx.AsyncClient`
pattern used by `test_sessions.py` (MEM172/MEM184). Tests stand up a real
TestClient + real Postgres so the team-admin auth gate, the
`system_settings` read, and the UPSERT path all run for real — only the
GitHub-side hop is stubbed.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, delete

from app.api.routes.admin import GITHUB_APP_CLIENT_ID_KEY, GITHUB_APP_SLUG_KEY
from app.core.config import settings
from app.models import GitHubAppInstallation, SystemSetting
from tests.utils.utils import random_email, random_lower_string

API = settings.API_V1_STR
SIGNUP_URL = f"{API}/auth/signup"
TEAMS_URL = f"{API}/teams/"


# ---------------------------------------------------------------------------
# Test isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_github_install_state(db: Session):
    """Wipe github_app_installations + the github_app_slug and github_app_client_id rows.

    Mirrors the test_admin_settings cleanup posture (MEM246): clean before
    AND after so a flake in one test cannot poison the next, and so the
    surrounding suite is unaffected. We deliberately do NOT delete users,
    teams, or memberships — those leak across modules by design via the
    session-scoped `db` fixture.
    """
    db.execute(delete(GitHubAppInstallation))
    db.execute(
        delete(SystemSetting).where(
            SystemSetting.key.in_([GITHUB_APP_SLUG_KEY, GITHUB_APP_CLIENT_ID_KEY])
        )
    )
    db.commit()
    yield
    db.execute(delete(GitHubAppInstallation))
    db.execute(
        delete(SystemSetting).where(
            SystemSetting.key.in_([GITHUB_APP_SLUG_KEY, GITHUB_APP_CLIENT_ID_KEY])
        )
    )
    db.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _signup(client: TestClient) -> tuple[str, httpx.Cookies]:
    """Sign up a fresh user; return (user_id, detached cookie jar)."""
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


def _create_team(
    client: TestClient, cookies: httpx.Cookies, name: str = "GH Team"
) -> str:
    r = client.post(TEAMS_URL, json={"name": name}, cookies=cookies)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _seed_app_slug(db: Session, value: str = "test-app-slug") -> None:
    """Seed `github_app_slug` directly via INSERT...ON CONFLICT.

    We bypass the admin PUT path so this test stays focused on the github
    router; the admin path is exercised in test_admin_settings.
    """
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
    """Seed `github_app_client_id` directly via INSERT...ON CONFLICT.

    Kept for tests that verify the admin settings path for client_id;
    the install-url endpoint no longer reads this key.
    """
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


# ----- _FakeAsyncClient (MEM184) -------------------------------------------


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
    """Stub for httpx.AsyncClient as imported by `app.api.routes.github`.

    The route module imports `httpx` at the top level and then uses
    `httpx.AsyncClient(...)` — patching the module's `httpx` binding's
    `AsyncClient` attribute intercepts every `async with` block.
    """

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
            f"have {list(self._routes.keys())}"
        )

    async def get(
        self, url: str, *, headers: dict[str, str] | None = None, **_: object
    ) -> _FakeResponse:
        handler = self._resolve("GET", url)
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


# ----- state-JWT helpers (test side, mirrors route's contract) -------------


def _decode_state(token: str) -> dict[str, Any]:
    return jwt.decode(
        token,
        settings.SECRET_KEY,
        algorithms=["HS256"],
        audience="github-install",
        issuer="perpetuity-install",
    )


def _mint_state(
    team_id: str | uuid.UUID,
    *,
    secret: str | None = None,
    audience: str = "github-install",
    issuer: str = "perpetuity-install",
    exp_delta_seconds: int = 600,
    iat_delta_seconds: int = 0,
    extra: dict[str, Any] | None = None,
) -> str:
    """Mint a state JWT for negative-path tests."""
    now = datetime.now(timezone.utc)
    iat = now + timedelta(seconds=iat_delta_seconds)
    exp = now + timedelta(seconds=exp_delta_seconds)
    payload: dict[str, Any] = {
        "team_id": str(team_id),
        "jti": "deadbeefdeadbeef" + uuid.uuid4().hex[:8],
        "iat": int(iat.timestamp()),
        "exp": int(exp.timestamp()),
        "iss": issuer,
        "aud": audience,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, secret or settings.SECRET_KEY, algorithm="HS256")


# ---------------------------------------------------------------------------
# install-url
# ---------------------------------------------------------------------------


def test_install_url_returns_signed_state_and_url(
    client: TestClient, db: Session
) -> None:
    """Happy path: state JWT shape verifies, URL embeds app slug + state."""
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies)
    _seed_app_slug(db, "unit-test-app")

    r = client.get(
        f"{API}/teams/{team_id}/github/install-url", cookies=cookies
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert "install_url" in body and "state" in body and "expires_at" in body

    expected_prefix = (
        f"{settings.GITHUB_APP_INSTALL_URL_BASE}/apps/unit-test-app/installations/new?state="
    )
    assert body["install_url"].startswith(expected_prefix), body["install_url"]
    assert body["install_url"].endswith(body["state"])

    payload = _decode_state(body["state"])
    assert payload["team_id"] == team_id
    assert payload["iss"] == "perpetuity-install"
    assert payload["aud"] == "github-install"
    # exp ~10 min out (600s ±5s slack for test runtime).
    now = datetime.now(timezone.utc)
    expires_at = datetime.fromisoformat(body["expires_at"])
    delta = (expires_at - now).total_seconds()
    assert 595 <= delta <= 605, delta
    assert (payload["exp"] - payload["iat"]) == 600


def test_install_url_404_when_slug_unset(
    client: TestClient, db: Session
) -> None:
    """No `github_app_slug` row → 404 `github_app_not_configured`."""
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies)
    # Deliberately do NOT seed the app slug.

    r = client.get(
        f"{API}/teams/{team_id}/github/install-url", cookies=cookies
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "github_app_not_configured"


def test_install_url_403_when_caller_is_not_team_admin(
    client: TestClient, db: Session
) -> None:
    """Non-member caller → 403 from assert_caller_is_team_admin."""
    _, admin_cookies = _signup(client)
    team_id = _create_team(client, admin_cookies, "AdminOnly")
    _seed_app_slug(db)

    _, other_cookies = _signup(client)
    r = client.get(
        f"{API}/teams/{team_id}/github/install-url", cookies=other_cookies
    )
    assert r.status_code == 403, r.text


def test_install_url_unauthenticated_returns_401(
    client: TestClient, db: Session
) -> None:
    """Missing cookie → 401 from get_current_user."""
    _, admin_cookies = _signup(client)
    team_id = _create_team(client, admin_cookies, "NoCookie")
    _seed_app_slug(db)
    client.cookies.clear()

    r = client.get(f"{API}/teams/{team_id}/github/install-url")
    assert r.status_code == 401, r.text


# ---------------------------------------------------------------------------
# install-callback (public)
# ---------------------------------------------------------------------------


def test_install_callback_happy_path_persists_row(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Valid state + orchestrator lookup → 200 + row persisted."""
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "CallbackTeam")
    _seed_app_slug(db)

    r1 = client.get(
        f"{API}/teams/{team_id}/github/install-url", cookies=cookies
    )
    state = r1.json()["state"]

    routes: dict[tuple[str, str], object] = {
        ("GET", "/v1/installations/424242/lookup"): _FakeResponse(
            200,
            {"account_login": "test-org", "account_type": "Organization"},
        ),
    }
    _install_fake_orch(monkeypatch, routes)

    # Public callback — no cookies needed.
    client.cookies.clear()
    r2 = client.post(
        f"{API}/github/install-callback",
        json={
            "installation_id": 424242,
            "setup_action": "install",
            "state": state,
        },
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["installation_id"] == 424242
    assert body["team_id"] == team_id
    assert body["account_login"] == "test-org"
    assert body["account_type"] == "Organization"

    db.expire_all()
    row = db.exec(
        # Use a fresh select to bypass identity-map staleness.
        text(
            "SELECT installation_id, account_login, account_type, team_id"
            " FROM github_app_installations WHERE installation_id = 424242"
        )
    ).one()
    assert row.installation_id == 424242
    assert row.account_login == "test-org"
    assert row.account_type == "Organization"
    assert str(row.team_id) == team_id


def test_install_callback_idempotent_on_duplicate_installation_id(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two callbacks with the same installation_id → both 200, one row."""
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "DupeTeam")
    _seed_app_slug(db)

    state = client.get(
        f"{API}/teams/{team_id}/github/install-url", cookies=cookies
    ).json()["state"]

    routes: dict[tuple[str, str], object] = {
        ("GET", "/v1/installations/777/lookup"): _FakeResponse(
            200,
            {"account_login": "dupe-org", "account_type": "Organization"},
        ),
    }
    _install_fake_orch(monkeypatch, routes)

    body = {"installation_id": 777, "setup_action": "install", "state": state}
    client.cookies.clear()

    r_a = client.post(f"{API}/github/install-callback", json=body)
    assert r_a.status_code == 200, r_a.text

    # Mint a fresh state because the second callback would otherwise reuse
    # the same jti — the route does not enforce single-use jti, but mirroring
    # what a real second click looks like keeps the test honest.
    state2 = client.get(
        f"{API}/teams/{team_id}/github/install-url", cookies=cookies
    ).json()["state"]
    body2 = {"installation_id": 777, "setup_action": "install", "state": state2}

    # Re-install the fake to register the lookup again (FakeAsyncClient is
    # one-shot per `_install_fake_orch` call).
    _install_fake_orch(monkeypatch, routes)
    client.cookies.clear()
    r_b = client.post(f"{API}/github/install-callback", json=body2)
    assert r_b.status_code == 200, r_b.text

    count = db.execute(
        text(
            "SELECT COUNT(*) AS n FROM github_app_installations"
            " WHERE installation_id = 777"
        )
    ).one()
    assert count.n == 1


def test_install_callback_team_reassignment_logs_warning(
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Same installation_id, different team → accept + WARN reassignment."""
    import logging as _logging

    _, cookies_a = _signup(client)
    team_a = _create_team(client, cookies_a, "TeamA")
    _, cookies_b = _signup(client)
    team_b = _create_team(client, cookies_b, "TeamB")
    _seed_app_slug(db)

    state_a = client.get(
        f"{API}/teams/{team_a}/github/install-url", cookies=cookies_a
    ).json()["state"]
    routes: dict[tuple[str, str], object] = {
        ("GET", "/v1/installations/9001/lookup"): _FakeResponse(
            200,
            {"account_login": "shared-org", "account_type": "Organization"},
        ),
    }
    _install_fake_orch(monkeypatch, routes)
    client.cookies.clear()
    r_a = client.post(
        f"{API}/github/install-callback",
        json={"installation_id": 9001, "setup_action": "install", "state": state_a},
    )
    assert r_a.status_code == 200, r_a.text

    state_b = client.get(
        f"{API}/teams/{team_b}/github/install-url", cookies=cookies_b
    ).json()["state"]
    _install_fake_orch(monkeypatch, routes)

    with caplog.at_level(_logging.WARNING, logger="app.api.routes.github"):
        client.cookies.clear()
        r_b = client.post(
            f"{API}/github/install-callback",
            json={
                "installation_id": 9001,
                "setup_action": "install",
                "state": state_b,
            },
        )
        assert r_b.status_code == 200, r_b.text

    captured = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "github_install_callback_team_reassigned" in captured, captured
    assert "installation_id=9001" in captured

    # Final state: row belongs to team_b.
    row = db.execute(
        text(
            "SELECT team_id FROM github_app_installations"
            " WHERE installation_id = 9001"
        )
    ).one()
    assert str(row.team_id) == team_b


def test_install_callback_expired_state_returns_400(
    client: TestClient, db: Session
) -> None:
    """State expired by 60s → 400 install_state_expired."""
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "ExpiredState")
    _seed_app_slug(db)

    expired = _mint_state(team_id, exp_delta_seconds=-60, iat_delta_seconds=-660)
    client.cookies.clear()
    r = client.post(
        f"{API}/github/install-callback",
        json={"installation_id": 1, "setup_action": "install", "state": expired},
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "install_state_expired"


def test_install_callback_bad_signature_returns_400(
    client: TestClient, db: Session
) -> None:
    """State signed with the wrong secret → 400 install_state_invalid."""
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "BadSig")
    _seed_app_slug(db)

    bad = _mint_state(team_id, secret="not-the-real-secret-xxxxxxxxxxxxxxxxxx")
    client.cookies.clear()
    r = client.post(
        f"{API}/github/install-callback",
        json={"installation_id": 1, "setup_action": "install", "state": bad},
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "install_state_invalid"


def test_install_callback_wrong_audience_returns_400(
    client: TestClient, db: Session
) -> None:
    """State with audience='not-github' → 400 install_state_invalid."""
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "BadAud")
    _seed_app_slug(db)

    bad = _mint_state(team_id, audience="not-github")
    client.cookies.clear()
    r = client.post(
        f"{API}/github/install-callback",
        json={"installation_id": 1, "setup_action": "install", "state": bad},
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "install_state_invalid"


def test_install_callback_team_unknown_returns_400(
    client: TestClient, db: Session
) -> None:
    """State carries a team_id that does not exist → 400 install_state_team_unknown."""
    nonexistent_team = uuid.uuid4()
    state = _mint_state(nonexistent_team)
    client.cookies.clear()
    r = client.post(
        f"{API}/github/install-callback",
        json={
            "installation_id": 1,
            "setup_action": "install",
            "state": state,
        },
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "install_state_team_unknown"


def test_install_callback_empty_state_returns_400(client: TestClient) -> None:
    """state='' → 400 install_state_invalid."""
    client.cookies.clear()
    r = client.post(
        f"{API}/github/install-callback",
        json={"installation_id": 1, "setup_action": "install", "state": ""},
    )
    assert r.status_code in (400, 422), r.text
    if r.status_code == 400:
        assert r.json()["detail"] == "install_state_invalid"


def test_install_callback_garbage_state_returns_400(
    client: TestClient,
) -> None:
    """state='not.a.jwt' → 400 install_state_invalid."""
    client.cookies.clear()
    r = client.post(
        f"{API}/github/install-callback",
        json={
            "installation_id": 1,
            "setup_action": "install",
            "state": "not.a.jwt",
        },
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "install_state_invalid"


def test_install_callback_negative_installation_id_returns_422(
    client: TestClient,
) -> None:
    """installation_id=-1 → 422 from pydantic ge=1 constraint."""
    client.cookies.clear()
    r = client.post(
        f"{API}/github/install-callback",
        json={
            "installation_id": -1,
            "setup_action": "install",
            "state": "x",
        },
    )
    assert r.status_code == 422, r.text


def test_install_callback_missing_fields_returns_422(client: TestClient) -> None:
    """Missing installation_id → 422."""
    client.cookies.clear()
    r = client.post(
        f"{API}/github/install-callback",
        json={"setup_action": "install", "state": "x"},
    )
    assert r.status_code == 422, r.text


def test_install_callback_orchestrator_error_returns_502(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Orchestrator returns 503 → 502 github_lookup_failed."""
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "OrchFail")
    _seed_app_slug(db)

    state = client.get(
        f"{API}/teams/{team_id}/github/install-url", cookies=cookies
    ).json()["state"]

    routes: dict[tuple[str, str], object] = {
        ("GET", "/v1/installations/55/lookup"): _FakeResponse(503, None),
    }
    _install_fake_orch(monkeypatch, routes)
    client.cookies.clear()
    r = client.post(
        f"{API}/github/install-callback",
        json={
            "installation_id": 55,
            "setup_action": "install",
            "state": state,
        },
    )
    assert r.status_code == 502, r.text
    detail = r.json()["detail"]
    assert detail["detail"] == "github_lookup_failed"
    assert detail["reason"] == "503"

    # Row was NOT created.
    count = db.execute(
        text(
            "SELECT COUNT(*) AS n FROM github_app_installations"
            " WHERE installation_id = 55"
        )
    ).one()
    assert count.n == 0


def test_install_callback_orchestrator_timeout_returns_502_timeout(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Orchestrator times out → 502 reason='timeout'."""
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "Timeout")
    _seed_app_slug(db)
    state = client.get(
        f"{API}/teams/{team_id}/github/install-url", cookies=cookies
    ).json()["state"]

    routes: dict[tuple[str, str], object] = {
        ("GET", "/v1/installations/56/lookup"): httpx.ConnectTimeout(
            "boom", request=httpx.Request("GET", "http://orch")
        ),
    }
    _install_fake_orch(monkeypatch, routes)
    client.cookies.clear()
    r = client.post(
        f"{API}/github/install-callback",
        json={
            "installation_id": 56,
            "setup_action": "install",
            "state": state,
        },
    )
    assert r.status_code == 502, r.text
    detail = r.json()["detail"]
    assert detail["detail"] == "github_lookup_failed"
    assert detail["reason"] == "timeout"


def test_install_callback_orchestrator_malformed_returns_502_malformed(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Orchestrator returns non-JSON → 502 reason='malformed_lookup_response'."""
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "Malformed")
    _seed_app_slug(db)
    state = client.get(
        f"{API}/teams/{team_id}/github/install-url", cookies=cookies
    ).json()["state"]

    routes: dict[tuple[str, str], object] = {
        ("GET", "/v1/installations/57/lookup"): _FakeResponse(
            200, None, raises_on_json=True
        ),
    }
    _install_fake_orch(monkeypatch, routes)
    client.cookies.clear()
    r = client.post(
        f"{API}/github/install-callback",
        json={
            "installation_id": 57,
            "setup_action": "install",
            "state": state,
        },
    )
    assert r.status_code == 502, r.text
    detail = r.json()["detail"]
    assert detail["detail"] == "github_lookup_failed"
    assert detail["reason"] == "malformed_lookup_response"


def test_install_callback_orchestrator_missing_keys_returns_502_malformed(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Orchestrator returns JSON missing account_login → 502 malformed."""
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "MissKeys")
    _seed_app_slug(db)
    state = client.get(
        f"{API}/teams/{team_id}/github/install-url", cookies=cookies
    ).json()["state"]

    routes: dict[tuple[str, str], object] = {
        ("GET", "/v1/installations/58/lookup"): _FakeResponse(
            200, {"account_type": "Organization"}
        ),
    }
    _install_fake_orch(monkeypatch, routes)
    client.cookies.clear()
    r = client.post(
        f"{API}/github/install-callback",
        json={
            "installation_id": 58,
            "setup_action": "install",
            "state": state,
        },
    )
    assert r.status_code == 502, r.text
    assert r.json()["detail"]["reason"] == "malformed_lookup_response"


# ---------------------------------------------------------------------------
# list installations
# ---------------------------------------------------------------------------


def test_list_installations_returns_rows_ordered_by_created_at_desc(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "ListTeam")
    _seed_app_slug(db)

    # Two installs with different installation_ids.
    for inst_id in (101, 102):
        state = client.get(
            f"{API}/teams/{team_id}/github/install-url", cookies=cookies
        ).json()["state"]
        routes: dict[tuple[str, str], object] = {
            ("GET", f"/v1/installations/{inst_id}/lookup"): _FakeResponse(
                200, {"account_login": f"org{inst_id}", "account_type": "User"}
            ),
        }
        _install_fake_orch(monkeypatch, routes)
        client.cookies.clear()
        r = client.post(
            f"{API}/github/install-callback",
            json={
                "installation_id": inst_id,
                "setup_action": "install",
                "state": state,
            },
        )
        assert r.status_code == 200, r.text

    r_list = client.get(
        f"{API}/teams/{team_id}/github/installations", cookies=cookies
    )
    assert r_list.status_code == 200, r_list.text
    body = r_list.json()
    assert body["count"] == 2
    ids = [row["installation_id"] for row in body["data"]]
    # 102 was inserted after 101, so created_at DESC puts 102 first.
    assert ids == [102, 101], ids


def test_list_installations_empty_returns_empty_envelope(
    client: TestClient, db: Session
) -> None:
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "EmptyList")
    _seed_app_slug(db)
    r = client.get(
        f"{API}/teams/{team_id}/github/installations", cookies=cookies
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"data": [], "count": 0}


def test_list_installations_403_when_not_team_admin(
    client: TestClient, db: Session
) -> None:
    _, admin_cookies = _signup(client)
    team_id = _create_team(client, admin_cookies, "ListAdminOnly")
    _, other_cookies = _signup(client)
    r = client.get(
        f"{API}/teams/{team_id}/github/installations", cookies=other_cookies
    )
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# delete installation
# ---------------------------------------------------------------------------


def test_delete_installation_404_when_row_missing(
    client: TestClient,
) -> None:
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "DelMissing")
    bogus = uuid.uuid4()
    r = client.delete(
        f"{API}/teams/{team_id}/github/installations/{bogus}", cookies=cookies
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "installation_not_found"


def test_delete_installation_404_when_row_belongs_to_other_team(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cross-team delete attempt → 404 (no existence enumeration)."""
    _, cookies_a = _signup(client)
    team_a = _create_team(client, cookies_a, "DelOwnerA")
    _, cookies_b = _signup(client)
    team_b = _create_team(client, cookies_b, "DelOtherB")
    _seed_app_slug(db)

    state = client.get(
        f"{API}/teams/{team_a}/github/install-url", cookies=cookies_a
    ).json()["state"]
    routes: dict[tuple[str, str], object] = {
        ("GET", "/v1/installations/61/lookup"): _FakeResponse(
            200, {"account_login": "ownerA", "account_type": "User"}
        ),
    }
    _install_fake_orch(monkeypatch, routes)
    client.cookies.clear()
    r_install = client.post(
        f"{API}/github/install-callback",
        json={"installation_id": 61, "setup_action": "install", "state": state},
    )
    assert r_install.status_code == 200, r_install.text
    row_id = r_install.json()["id"]

    # team_b admin tries to delete A's row via b's URL
    r = client.delete(
        f"{API}/teams/{team_b}/github/installations/{row_id}",
        cookies=cookies_b,
    )
    assert r.status_code == 404, r.text


def test_delete_installation_happy_path_removes_row(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "DelHappy")
    _seed_app_slug(db)

    state = client.get(
        f"{API}/teams/{team_id}/github/install-url", cookies=cookies
    ).json()["state"]
    routes: dict[tuple[str, str], object] = {
        ("GET", "/v1/installations/77/lookup"): _FakeResponse(
            200, {"account_login": "delme", "account_type": "User"}
        ),
    }
    _install_fake_orch(monkeypatch, routes)
    client.cookies.clear()
    r_install = client.post(
        f"{API}/github/install-callback",
        json={"installation_id": 77, "setup_action": "install", "state": state},
    )
    row_id = r_install.json()["id"]

    r_del = client.delete(
        f"{API}/teams/{team_id}/github/installations/{row_id}", cookies=cookies
    )
    assert r_del.status_code == 200, r_del.text
    body = r_del.json()
    assert body["deleted"] is True
    assert body["id"] == row_id

    count = db.execute(
        text(
            "SELECT COUNT(*) AS n FROM github_app_installations"
            " WHERE installation_id = 77"
        )
    ).one()
    assert count.n == 0


def test_delete_installation_403_when_not_team_admin(
    client: TestClient, db: Session
) -> None:
    _, admin_cookies = _signup(client)
    team_id = _create_team(client, admin_cookies, "DelAdminOnly")
    _, other_cookies = _signup(client)
    bogus = uuid.uuid4()
    r = client.delete(
        f"{API}/teams/{team_id}/github/installations/{bogus}",
        cookies=other_cookies,
    )
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# log redaction sanity check
# ---------------------------------------------------------------------------


def test_install_url_log_does_not_contain_full_state(
    client: TestClient,
    db: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Slice contract: only the 8-char jti prefix appears in logs."""
    import logging as _logging

    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "LogRedact")
    _seed_app_slug(db)

    with caplog.at_level(_logging.INFO, logger="app.api.routes.github"):
        r = client.get(
            f"{API}/teams/{team_id}/github/install-url", cookies=cookies
        )
        assert r.status_code == 200
        state = r.json()["state"]

    captured = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "github_install_url_issued" in captured
    # The full state JWT must not appear; only the 8-char jti prefix.
    assert state not in captured
    # The state JWT has at least 100 chars; even any 32-char window of it
    # should not appear in logs.
    assert state[:64] not in captured


# ---------------------------------------------------------------------------
# GET /github/install-callback — browser redirect flow
# ---------------------------------------------------------------------------


def test_get_install_callback_happy_path_redirects_to_frontend(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET callback: valid state + orchestrator lookup → 302 to frontend /teams."""
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "GetCallbackHappy")
    _seed_app_slug(db)

    r1 = client.get(
        f"{API}/teams/{team_id}/github/install-url", cookies=cookies
    )
    state = r1.json()["state"]

    routes: dict[tuple[str, str], object] = {
        ("GET", "/v1/installations/555001/lookup"): _FakeResponse(
            200,
            {"account_login": "get-org", "account_type": "Organization"},
        ),
    }
    _install_fake_orch(monkeypatch, routes)

    client.cookies.clear()
    r2 = client.get(
        f"{API}/github/install-callback",
        params={
            "installation_id": 555001,
            "setup_action": "install",
            "state": state,
        },
        follow_redirects=False,
    )
    assert r2.status_code == 302, r2.text
    location = r2.headers["location"]
    assert location.endswith("/teams"), f"unexpected redirect: {location!r}"
    assert "github_install_error" not in location

    # Verify row was persisted.
    db.expire_all()
    row = db.exec(
        text(
            "SELECT installation_id, account_login, team_id"
            " FROM github_app_installations WHERE installation_id = 555001"
        )
    ).one()
    assert row.account_login == "get-org"
    assert str(row.team_id) == team_id


def test_get_install_callback_bad_state_redirects_with_error(
    client: TestClient, db: Session
) -> None:
    """GET callback with invalid state → 302 to /teams?github_install_error=..."""
    _, cookies = _signup(client)
    _create_team(client, cookies, "GetCallbackErr")
    _seed_app_slug(db)

    client.cookies.clear()
    r = client.get(
        f"{API}/github/install-callback",
        params={
            "installation_id": 555002,
            "setup_action": "install",
            "state": "not.a.valid.jwt",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302, r.text
    location = r.headers["location"]
    assert "github_install_error" in location
