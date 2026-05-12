"""Orchestrator create-repository regression suite (M005-sqm8et baseline + M006-ydo2ce S05 update).

Tests here lock in the behavior established during M005-sqm8et (org-install path) and
document the one behavior change from M006-ydo2ce S05: personal installs that lack a
user token now return 422 instead of falling through to a 502.

Covers:
  - Org install -> 201 with name/full_name (M005-sqm8et baseline)
  - Org install -> 502 when GitHub returns non-201 (M005-sqm8et baseline)
  - Org install -> 503 when GitHub App not configured (M005-sqm8et baseline)
  - Personal install + no user token -> 422 user_token_required_for_personal_install
    [Updated from 502: M006-ydo2ce S05 defense-in-depth path]
  - Missing repo_name -> 422 repo_name_required (validation)
  - Invalid private type -> 422 private_must_be_boolean (validation)
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

_INSTALLATION_ID = 99
_ORG_LOGIN = "acmecorp"
_USER_LOGIN = "acmeuser"
_INSTALL_TOKEN = "ghs_baseline_install_token"
_REPO_NAME = "baseline-repo"

_MOCK_REPO_RESP = {
    "name": _REPO_NAME,
    "full_name": f"{_ORG_LOGIN}/{_REPO_NAME}",
    "updated_at": "2026-05-12T00:00:00Z",
    "description": "A baseline test repo",
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


def _lookup_mock(
    router: respx.MockRouter,
    *,
    account_login: str,
    account_type: str,
    installation_id: int = _INSTALLATION_ID,
) -> respx.Route:
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
# M005-sqm8et baseline: org-install happy path
# ---------------------------------------------------------------------------


def test_org_install_create_repository_returns_201(
    app_with_state: tuple[Any, Any, Any],
) -> None:
    """Org install -> 201 with repository schema (M005-sqm8et baseline behavior)."""
    app, pool, registry = app_with_state
    base = _github_base()

    with respx.mock(assert_all_called=True) as router:
        _lookup_mock(router, account_login=_ORG_LOGIN, account_type="Organization")
        _access_tokens_mock(router)
        router.post(f"{base}/orgs/{_ORG_LOGIN}/repos").mock(
            return_value=httpx.Response(201, json=_MOCK_REPO_RESP)
        )

        with TestClient(app) as c:
            _install_state(app, pool, registry)
            r = c.post(
                f"/v1/installations/{_INSTALLATION_ID}/create-repository",
                headers=_auth(),
                json={"repo_name": _REPO_NAME, "private": True, "description": "A baseline test repo"},
            )

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == _REPO_NAME
    assert body["full_name"] == f"{_ORG_LOGIN}/{_REPO_NAME}"
    assert body["description"] == "A baseline test repo"


def test_org_install_github_non_201_returns_502(
    app_with_state: tuple[Any, Any, Any],
) -> None:
    """Org install -> GitHub returns 422 -> orchestrator surfaces 502 (M005-sqm8et baseline)."""
    app, pool, registry = app_with_state
    base = _github_base()

    with respx.mock(assert_all_called=True) as router:
        _lookup_mock(router, account_login=_ORG_LOGIN, account_type="Organization")
        _access_tokens_mock(router)
        router.post(f"{base}/orgs/{_ORG_LOGIN}/repos").mock(
            return_value=httpx.Response(422, json={"message": "Repository creation failed."})
        )

        with TestClient(app) as c:
            _install_state(app, pool, registry)
            r = c.post(
                f"/v1/installations/{_INSTALLATION_ID}/create-repository",
                headers=_auth(),
                json={"repo_name": _REPO_NAME, "private": True},
            )

    assert r.status_code == 502, r.text
    assert r.json()["detail"] == "github_create_repository_failed"


def test_org_install_not_configured_returns_503(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """GitHub App not configured (missing credential row) -> 503 (M005-sqm8et baseline)."""
    from orchestrator.main import app
    from orchestrator.redis_client import RedisSessionRegistry

    # Pool with no credential rows -> _NotConfigured
    pool = _FakePool([])
    registry = RedisSessionRegistry(client=fake_redis)

    base = _github_base()
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{base}/app/installations/{_INSTALLATION_ID}").mock(
            return_value=httpx.Response(404)
        )
        with TestClient(app) as c:
            _install_state(app, pool, registry)
            r = c.post(
                f"/v1/installations/{_INSTALLATION_ID}/create-repository",
                headers=_auth(),
                json={"repo_name": _REPO_NAME, "private": True},
            )

    assert r.status_code == 503, r.text


# ---------------------------------------------------------------------------
# M006-ydo2ce S05: personal install without user token -> 422
# Previously this path would have fallen through to a 502 before M006-ydo2ce S05
# implemented the 422 defense-in-depth gate.
# ---------------------------------------------------------------------------


def test_personal_install_no_user_token_returns_422(
    app_with_state: tuple[Any, Any, Any],
) -> None:
    """Personal install + no X-GitHub-User-Token -> 422 (M006-ydo2ce S05 change, was 502 in M005-sqm8et).

    The orchestrator must return 422 user_token_required_for_personal_install
    before attempting any installation token mint or GitHub create-repository call.
    This test is updated from the M005-sqm8et expectation (502) to reflect the
    M006-ydo2ce S05 defense-in-depth gate.
    """
    app, pool, registry = app_with_state
    base = _github_base()

    with respx.mock(assert_all_called=False) as router:
        _lookup_mock(router, account_login=_USER_LOGIN, account_type="User")
        # These routes must NOT be called
        mint_route = router.post(
            f"{base}/app/installations/{_INSTALLATION_ID}/access_tokens"
        ).mock(
            return_value=httpx.Response(201, json={"token": "should_not_reach", "expires_at": "2026-06-01T00:00:00Z"})
        )
        repo_route = router.post(f"{base}/user/repos").mock(
            return_value=httpx.Response(201, json=_MOCK_REPO_RESP)
        )

        with TestClient(app) as c:
            _install_state(app, pool, registry)
            r = c.post(
                f"/v1/installations/{_INSTALLATION_ID}/create-repository",
                headers=_auth(),  # deliberately omits X-GitHub-User-Token
                json={"repo_name": _REPO_NAME, "private": False},
            )

    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "user_token_required_for_personal_install"

    assert not mint_route.called, "access_tokens must not be called on 422 path"
    assert not repo_route.called, "POST /user/repos must not be called on 422 path"


# ---------------------------------------------------------------------------
# Validation tests (pre-existing, schema-level)
# ---------------------------------------------------------------------------


def test_missing_repo_name_returns_422(
    app_with_state: tuple[Any, Any, Any],
) -> None:
    """Missing repo_name field -> 422 repo_name_required."""
    app, pool, registry = app_with_state
    base = _github_base()

    with respx.mock(assert_all_called=False) as router:
        _lookup_mock(router, account_login=_ORG_LOGIN, account_type="Organization")
        router.post(f"{base}/app/installations/{_INSTALLATION_ID}/access_tokens").mock(
            return_value=httpx.Response(201, json={"token": _INSTALL_TOKEN, "expires_at": "2026-06-01T00:00:00Z"})
        )
        router.post(f"{base}/orgs/{_ORG_LOGIN}/repos").mock(
            return_value=httpx.Response(201, json=_MOCK_REPO_RESP)
        )

        with TestClient(app) as c:
            _install_state(app, pool, registry)
            r = c.post(
                f"/v1/installations/{_INSTALLATION_ID}/create-repository",
                headers=_auth(),
                json={"private": True},
            )

    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "repo_name_required"


def test_invalid_private_type_returns_422(
    app_with_state: tuple[Any, Any, Any],
) -> None:
    """Non-boolean private field -> 422 private_must_be_boolean."""
    app, pool, registry = app_with_state
    base = _github_base()

    with respx.mock(assert_all_called=False) as router:
        _lookup_mock(router, account_login=_ORG_LOGIN, account_type="Organization")
        router.post(f"{base}/app/installations/{_INSTALLATION_ID}/access_tokens").mock(
            return_value=httpx.Response(201, json={"token": _INSTALL_TOKEN, "expires_at": "2026-06-01T00:00:00Z"})
        )
        router.post(f"{base}/orgs/{_ORG_LOGIN}/repos").mock(
            return_value=httpx.Response(201, json=_MOCK_REPO_RESP)
        )

        with TestClient(app) as c:
            _install_state(app, pool, registry)
            r = c.post(
                f"/v1/installations/{_INSTALLATION_ID}/create-repository",
                headers=_auth(),
                json={"repo_name": _REPO_NAME, "private": "yes"},
            )

    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "private_must_be_boolean"
