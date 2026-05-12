"""Orchestrator integration tests for create-repository user-token branching (M006-ydo2ce S05 T03).

Proves the milestone's core claim: the orchestrator forwards the user token as a bearer
credential to POST /user/repos for personal installs — not the installation token.

Must-haves tested:
  (1) personal install + X-GitHub-User-Token present -> POST /user/repos with
      Authorization: token <user_token>; access_tokens mint call count == 0.
  (2) personal install + header absent -> 422 user_token_required_for_personal_install;
      no GitHub call at all.
  (3) org install (no user-token header) -> POST /orgs/{login}/repos with install token.
  (4) org install + X-GitHub-User-Token header present -> header ignored; POST
      /orgs/{login}/repos with install token; WARN log emitted.
  (5) redaction: no literal user_token string appears in any orchestrator log record.

Approach:
  - respx.mock() intercepts both api.github.com/app/installations/{id}/access_tokens
    and the target repo-creation URL so the test asserts the exact URL hit.
  - FakePool + FakeRedis from the unit harness pattern (same as test_github_tokens.py).
  - FastAPI TestClient with _install_state() pins fakes onto app.state after lifespan.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest

os.environ.setdefault("SKIP_IMAGE_PULL_ON_BOOT", "1")
os.environ.setdefault("SKIP_PG_POOL_ON_BOOT", "1")
os.environ.setdefault("ORCHESTRATOR_API_KEY", "unit-test-current-key")
os.environ.setdefault(
    "SYSTEM_SETTINGS_ENCRYPTION_KEY",
    "kfk5l7mPRFpBV7PzWJxYmO6LRRQAdZ4iGYZRG6xL0fY=",
)

import fakeredis.aioredis  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: F811 (re-import safe)
import respx  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from orchestrator.config import settings  # noqa: E402
from orchestrator.encryption import encrypt_setting  # noqa: E402
from orchestrator.github_tokens import (  # noqa: E402
    _GITHUB_APP_ID_KEY,
    _GITHUB_APP_PRIVATE_KEY_KEY,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_INSTALLATION_ID = 42
_ORG_LOGIN = "octocorp"
_USER_LOGIN = "octouser"
_INSTALL_TOKEN = "ghs_install_token_xyz"
_USER_TOKEN = "ghu_user_token_abc"
_REPO_NAME = "my-new-repo"

_MOCK_REPO_RESP = {
    "name": _REPO_NAME,
    "full_name": f"{_USER_LOGIN}/{_REPO_NAME}",
    "updated_at": "2026-05-12T00:00:00Z",
    "description": None,
}

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rsa_keypair() -> tuple[str, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


class _FakeConn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    async def fetch(self, _sql: str, _keys: list[str]) -> list[dict[str, Any]]:
        return list(self._rows)

    async def __aenter__(self) -> "_FakeConn":
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None


class _FakePool:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def acquire(self) -> _FakeConn:
        return _FakeConn(self._rows)


def _credentials_rows(app_id: int, private_pem: str) -> list[dict[str, Any]]:
    return [
        {
            "key": _GITHUB_APP_PRIVATE_KEY_KEY,
            "value": None,
            "value_encrypted": encrypt_setting(private_pem),
        },
        {
            "key": _GITHUB_APP_ID_KEY,
            "value": json.dumps(app_id),
            "value_encrypted": None,
        },
    ]


@pytest.fixture
async def fake_redis() -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def app_with_state(
    rsa_keypair: tuple[str, str], fake_redis: fakeredis.aioredis.FakeRedis
) -> Iterator[tuple[Any, Any, Any]]:
    from orchestrator.main import app
    from orchestrator.redis_client import RedisSessionRegistry

    private_pem, _ = rsa_keypair
    pool = _FakePool(_credentials_rows(7777, private_pem))
    registry = RedisSessionRegistry(client=fake_redis)
    yield app, pool, registry


def _install_state(app: Any, pool: Any, registry: Any) -> None:
    app.state.pg = pool
    app.state.registry = registry


def _auth() -> dict[str, str]:
    return {"X-Orchestrator-Key": settings.orchestrator_api_key}


def _github_base() -> str:
    return settings.github_api_base_url.rstrip("/")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _lookup_mock(
    router: respx.MockRouter,
    *,
    account_login: str,
    account_type: str,
    installation_id: int = _INSTALLATION_ID,
) -> respx.Route:
    """Mock the /app/installations/{id} lookup call."""
    base = _github_base()
    return router.get(f"{base}/app/installations/{installation_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": installation_id,
                "account": {"login": account_login, "type": account_type},
            },
        )
    )


def _access_tokens_mock(
    router: respx.MockRouter,
    *,
    installation_id: int = _INSTALLATION_ID,
    token: str = _INSTALL_TOKEN,
) -> respx.Route:
    """Mock the installation token mint call."""
    base = _github_base()
    return router.post(
        f"{base}/app/installations/{installation_id}/access_tokens"
    ).mock(
        return_value=httpx.Response(
            201,
            json={"token": token, "expires_at": "2026-06-01T00:00:00Z"},
        )
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_personal_install_with_user_token_uses_user_token_for_user_repos(
    app_with_state: tuple[Any, Any, Any],
) -> None:
    """Core milestone proof: personal install + user token -> POST /user/repos.

    Verifies:
    - The URL hit is exactly api.github.com/user/repos (not /orgs/…/repos).
    - Authorization header carries the forwarded user token, not the install token.
    - The installation access_tokens endpoint is NOT called (mint count == 0).
    - Response is 201 with the repository schema.
    """
    app, pool, registry = app_with_state
    base = _github_base()

    captured: dict[str, Any] = {}

    def _repo_handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization", "")
        return httpx.Response(201, json=_MOCK_REPO_RESP)

    with respx.mock(assert_all_called=False) as router:
        _lookup_mock(router, account_login=_USER_LOGIN, account_type="User")
        # access_tokens must NOT be called — registered here only to detect accidental calls
        mint_route = router.post(
            f"{base}/app/installations/{_INSTALLATION_ID}/access_tokens"
        ).mock(return_value=httpx.Response(201, json={"token": _INSTALL_TOKEN, "expires_at": "2026-06-01T00:00:00Z"}))
        router.post(f"{base}/user/repos").mock(side_effect=_repo_handler)

        with TestClient(app) as c:
            _install_state(app, pool, registry)
            r = c.post(
                f"/v1/installations/{_INSTALLATION_ID}/create-repository",
                headers={**_auth(), "X-GitHub-User-Token": _USER_TOKEN},
                json={"repo_name": _REPO_NAME, "private": False},
            )

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == _REPO_NAME
    assert body["full_name"] == f"{_USER_LOGIN}/{_REPO_NAME}"

    # The URL hit must be /user/repos, not /orgs/…/repos
    assert captured["url"] == f"{base}/user/repos", (
        f"Expected POST /user/repos, got {captured['url']!r}"
    )

    # Authorization must carry the user token
    assert captured["auth"] == f"token {_USER_TOKEN}", (
        f"Expected 'token {_USER_TOKEN}', got {captured['auth']!r}"
    )

    # Installation token mint must NOT have been called
    assert not mint_route.called, (
        f"access_tokens should not be called on personal-install user-token path; "
        f"call_count={mint_route.call_count}"
    )


def test_personal_install_no_user_token_returns_422(
    app_with_state: tuple[Any, Any, Any],
) -> None:
    """Personal install + no X-GitHub-User-Token header -> 422 defense-in-depth.

    No GitHub create-repository call should be made — the 422 short-circuits
    before any token mint or repo call.
    """
    app, pool, registry = app_with_state
    base = _github_base()

    with respx.mock(assert_all_called=False) as router:
        _lookup_mock(router, account_login=_USER_LOGIN, account_type="User")
        # Neither access_tokens nor /user/repos should be called
        mint_route = router.post(
            f"{base}/app/installations/{_INSTALLATION_ID}/access_tokens"
        ).mock(return_value=httpx.Response(201, json={"token": "should_not_reach", "expires_at": "2026-06-01T00:00:00Z"}))
        repo_route = router.post(f"{base}/user/repos").mock(
            return_value=httpx.Response(201, json=_MOCK_REPO_RESP)
        )

        with TestClient(app) as c:
            _install_state(app, pool, registry)
            r = c.post(
                f"/v1/installations/{_INSTALLATION_ID}/create-repository",
                headers=_auth(),  # no X-GitHub-User-Token
                json={"repo_name": _REPO_NAME, "private": False},
            )

    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "user_token_required_for_personal_install"

    # Neither github call should have been made
    assert not mint_route.called, "access_tokens must not be called on 422 path"
    assert not repo_route.called, "POST /user/repos must not be called on 422 path"


def test_org_install_uses_install_token_for_orgs_repos(
    app_with_state: tuple[Any, Any, Any],
) -> None:
    """Org install -> POST /orgs/{login}/repos with install token.

    Verifies the org-install path is byte-identical to M005-sqm8et behavior:
    - URL is /orgs/{login}/repos
    - Authorization uses the minted installation token
    - access_tokens IS called exactly once
    """
    app, pool, registry = app_with_state
    base = _github_base()

    captured: dict[str, Any] = {}

    def _repo_handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization", "")
        return httpx.Response(201, json=_MOCK_REPO_RESP)

    with respx.mock(assert_all_called=True) as router:
        _lookup_mock(router, account_login=_ORG_LOGIN, account_type="Organization")
        _access_tokens_mock(router, token=_INSTALL_TOKEN)
        router.post(f"{base}/orgs/{_ORG_LOGIN}/repos").mock(side_effect=_repo_handler)

        with TestClient(app) as c:
            _install_state(app, pool, registry)
            r = c.post(
                f"/v1/installations/{_INSTALLATION_ID}/create-repository",
                headers=_auth(),  # no user-token header
                json={"repo_name": _REPO_NAME, "private": True},
            )

    assert r.status_code == 201, r.text
    assert r.json()["name"] == _REPO_NAME

    # URL must be the org repos endpoint
    assert captured["url"] == f"{base}/orgs/{_ORG_LOGIN}/repos", (
        f"Expected /orgs/{_ORG_LOGIN}/repos, got {captured['url']!r}"
    )

    # Authorization must use the installation token
    assert captured["auth"] == f"token {_INSTALL_TOKEN}", (
        f"Expected 'token {_INSTALL_TOKEN}', got {captured['auth']!r}"
    )


def test_org_install_ignores_user_token_header(
    app_with_state: tuple[Any, Any, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Org install + X-GitHub-User-Token present -> header ignored, WARN log emitted.

    The user token must NOT appear in the Authorization header sent to GitHub.
    A WARN log 'github_create_repository_unexpected_user_token_on_org' must be emitted.
    """
    app, pool, registry = app_with_state
    base = _github_base()

    captured: dict[str, Any] = {}

    def _repo_handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization", "")
        return httpx.Response(201, json=_MOCK_REPO_RESP)

    import logging

    with caplog.at_level(logging.WARNING, logger="orchestrator"):
        with respx.mock(assert_all_called=True) as router:
            _lookup_mock(router, account_login=_ORG_LOGIN, account_type="Organization")
            _access_tokens_mock(router, token=_INSTALL_TOKEN)
            router.post(f"{base}/orgs/{_ORG_LOGIN}/repos").mock(side_effect=_repo_handler)

            with TestClient(app) as c:
                _install_state(app, pool, registry)
                r = c.post(
                    f"/v1/installations/{_INSTALLATION_ID}/create-repository",
                    headers={**_auth(), "X-GitHub-User-Token": _USER_TOKEN},
                    json={"repo_name": _REPO_NAME, "private": True},
                )

    assert r.status_code == 201, r.text

    # Authorization must use the install token, NOT the user token
    assert _USER_TOKEN not in captured["auth"], (
        f"User token must not appear in Authorization for org install; got {captured['auth']!r}"
    )
    assert f"token {_INSTALL_TOKEN}" == captured["auth"]

    # WARN log must be present
    warn_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "github_create_repository_unexpected_user_token_on_org" in msg
        for msg in warn_messages
    ), f"Expected WARN log not found; warnings: {warn_messages!r}"


def test_personal_install_user_token_not_in_logs(
    app_with_state: tuple[Any, Any, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Redaction sweep: no log record may contain the literal user token string."""
    app, pool, registry = app_with_state
    base = _github_base()

    import logging

    with caplog.at_level(logging.DEBUG, logger="orchestrator"):
        with respx.mock(assert_all_called=False) as router:
            _lookup_mock(router, account_login=_USER_LOGIN, account_type="User")
            # access_tokens not called for personal + user_token path
            router.post(
                f"{base}/app/installations/{_INSTALLATION_ID}/access_tokens"
            ).mock(return_value=httpx.Response(201, json={"token": _INSTALL_TOKEN, "expires_at": "2026-06-01T00:00:00Z"}))
            router.post(f"{base}/user/repos").mock(
                return_value=httpx.Response(201, json=_MOCK_REPO_RESP)
            )

            with TestClient(app) as c:
                _install_state(app, pool, registry)
                r = c.post(
                    f"/v1/installations/{_INSTALLATION_ID}/create-repository",
                    headers={**_auth(), "X-GitHub-User-Token": _USER_TOKEN},
                    json={"repo_name": _REPO_NAME, "private": False},
                )

    assert r.status_code == 201, r.text

    # Sweep: the literal user token must not appear in any log message
    for record in caplog.records:
        msg = record.getMessage()
        assert _USER_TOKEN not in msg, (
            f"User token plaintext leaked in log record: {msg!r}"
        )
